"""Agent-D: Plan-Then-Code LLM + Pandas Agent Harness.

Flow:
Task -> LLM Plan -> Record Plan -> Generate pandas code -> Execute -> Observation
-> Check Plan -> Continue -> Final.

The first valid model action MUST be `plan`. After that, the model executes the
recorded plan step-by-step with pandas code. A plan step is marked complete only
when its code executes successfully. `final_answer` is accepted only after every
plan step is complete.

External Runtime / Supervisor owns wall-time, token, and memory budgets.
"""

from __future__ import annotations

import ast
import contextlib
import io
import json

import pandas as pd


_OUTPUT_LIMIT = 6000
_MAX_PLAN_STEPS = 8

SYSTEM_PROMPT = """You are a Plan-Then-Code data-analysis agent operating on a pandas DataFrame.

You are given:
1. a task description;
2. access to a pandas DataFrame named `df`.

You MUST work in two phases.

PHASE 1 — PLAN
Your FIRST response must be:
{"action":"plan","plan":["step 1","step 2"]}

Rules for the plan:
- create a short task-level plan before generating pandas code;
- each item is one concrete analysis step;
- do not put pandas/Python code in the plan;
- do not include a final-answer step;
- keep the plan short.

PHASE 2 — CODE
After the runtime records the plan, execute it one step at a time:
{"action":"execute_pandas","plan_step":1,"code":"..."}

Rules:
- plan_step is 1-based;
- execute the next unfinished step;
- if execution fails, fix the code and retry the same step;
- every code block must be self-contained and start from `df`;
- pandas is available as `pd`;
- do not read files;
- do not access the network;
- do not import modules;
- do not invent unobserved values;
- prefer short, verifiable pandas operations.

FINAL
Only after all plan steps are complete:
{"action":"final_answer","answer":...}

Return JSON only. Do not use Markdown fences.
Preserve the exact answer format requested by the task.
"""


class InvalidAgentJSON(Exception):
    pass


class InvalidAgentAction(Exception):
    pass


_FORBIDDEN_CALLS = frozenset({"open", "exec", "eval", "compile", "__import__"})
_FORBIDDEN_MODULES = frozenset(
    {"os", "sys", "subprocess", "socket", "requests", "pathlib", "shutil"}
)


def build_initial_messages(task: dict, df: pd.DataFrame) -> list[dict[str, str]]:
    info = (
        f"rows: {len(df)}\n"
        f"columns: {list(df.columns)}\n"
        f"dtypes:\n{df.dtypes.to_string()}"
    )
    user = (
        f"Task:\n{json.dumps(task, ensure_ascii=False, default=str)}\n\n"
        f"DataFrame information:\n{info}\n\n"
        "Your first response MUST be a plan action."
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def _extract_json(raw: str) -> dict:
    text = (raw or "").strip()
    if text.startswith("```"):
        first_line = text.find("\n")
        if first_line != -1:
            text = text[first_line + 1 :]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise InvalidAgentJSON(f"response is not JSON: {text[:120]!r}")

    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise InvalidAgentJSON(f"invalid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise InvalidAgentJSON("action payload must be a JSON object")
    return data


def parse_action(raw: str) -> dict:
    data = _extract_json(raw)
    action = data.get("action")

    if action == "plan":
        plan = data.get("plan")
        if not isinstance(plan, list) or not plan:
            raise InvalidAgentAction("plan requires non-empty 'plan'")
        if len(plan) > _MAX_PLAN_STEPS:
            raise InvalidAgentAction(
                f"plan has {len(plan)} steps; max is {_MAX_PLAN_STEPS}"
            )
        clean = []
        for i, item in enumerate(plan, 1):
            if not isinstance(item, str) or not item.strip():
                raise InvalidAgentAction(f"plan step {i} must be non-empty text")
            clean.append(item.strip())
        return {"action": "plan", "plan": clean}

    if action == "execute_pandas":
        step = data.get("plan_step")
        code = data.get("code")
        if not isinstance(step, int) or isinstance(step, bool) or step < 1:
            raise InvalidAgentAction(
                "execute_pandas requires integer 'plan_step' >= 1"
            )
        if not isinstance(code, str) or not code.strip():
            raise InvalidAgentAction("execute_pandas requires non-empty 'code'")
        return {
            "action": "execute_pandas",
            "plan_step": step,
            "code": code,
        }

    if action == "final_answer":
        if "answer" not in data:
            raise InvalidAgentAction("final_answer requires 'answer'")
        return {"action": "final_answer", "answer": data["answer"]}

    raise InvalidAgentAction(f"unknown action {action!r}")


def _forbidden_reason(code: str) -> str | None:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            return f"import is not allowed: {node.names[0].name}"
        if isinstance(node, ast.ImportFrom):
            return f"import is not allowed: from {node.module or 'relative'}"
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in _FORBIDDEN_CALLS
        ):
            return f"{node.func.id}() is not allowed"
        if isinstance(node, ast.Name) and node.id in _FORBIDDEN_MODULES:
            return f"module {node.id!r} is not allowed"
    return None


def execute_pandas(code: str, df: pd.DataFrame) -> dict:
    """Execute one self-contained pandas program against the original df."""
    reason = _forbidden_reason(code)
    if reason is not None:
        return {"ok": False, "error": f"ForbiddenCode: {reason}"}

    namespace = {"df": df, "pd": pd}
    buffer = io.StringIO()

    try:
        tree = ast.parse(code)
        last = tree.body[-1] if tree.body and isinstance(tree.body[-1], ast.Expr) else None
        body = tree.body[:-1] if last is not None else tree.body

        with contextlib.redirect_stdout(buffer):
            if body:
                exec(
                    compile(
                        ast.Module(body=body, type_ignores=[]),
                        "<agent-code>",
                        "exec",
                    ),
                    namespace,
                )
            value = None
            if last is not None:
                value = eval(
                    compile(ast.Expression(last.value), "<agent-code>", "eval"),
                    namespace,
                )
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    parts = []
    stdout = buffer.getvalue()
    if stdout:
        parts.append(stdout.rstrip("\n"))
    if value is not None:
        parts.append(repr(value))

    return {
        "ok": True,
        "output": ("\n".join(parts) or "<no output>")[:_OUTPUT_LIMIT],
    }


def _progress(plan: list[str], completed: set[int]) -> str:
    return "\n".join(
        f"{i}. [{'DONE' if i in completed else 'TODO'}] {step}"
        for i, step in enumerate(plan, 1)
    )


def _next_step(plan: list[str], completed: set[int]) -> int | None:
    for i in range(1, len(plan) + 1):
        if i not in completed:
            return i
    return None


def _feedback(
    observation: dict,
    plan: list[str],
    completed: set[int],
    step: int,
) -> str:
    progress = _progress(plan, completed)

    if not observation["ok"]:
        return (
            f"Observation (error) for plan step {step}:\n"
            f"{observation['error']}\n\n"
            f"Plan progress:\n{progress}\n\n"
            f"Step {step} is NOT complete. Fix the code and retry step {step}."
        )

    nxt = _next_step(plan, completed)
    if nxt is None:
        guidance = "All plan steps are complete. Return final_answer."
    else:
        guidance = f"Continue with plan step {nxt}."

    return (
        f"Observation for plan step {step}:\n"
        f"{observation['output']}\n\n"
        f"Plan progress:\n{progress}\n\n"
        f"{guidance}"
    )


def _run_with_trace_internal(
    task: dict,
    df: pd.DataFrame,
    llm,
    trace: list[dict] | None = None,
):
    """Strict Plan -> Code -> Execute -> Observe -> Check Plan -> Final loop."""

    if trace is None:
        trace = []

    messages = build_initial_messages(task, df)
    plan = None
    completed: set[int] = set()

    # Phase 1: plan is mandatory before code.
    while plan is None:
        raw = llm.complete(messages)

        try:
            action = parse_action(raw)
        except (InvalidAgentJSON, InvalidAgentAction) as exc:
            trace.append(
                {"phase": "plan", "accepted": False, "raw": raw, "error": str(exc)}
            )
            messages.append({"role": "assistant", "content": raw.strip()})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"Planning protocol error: {exc}\n"
                        'Return only {"action":"plan","plan":["step 1","..."]}.'
                    ),
                }
            )
            continue

        if action["action"] != "plan":
            trace.append(
                {
                    "phase": "plan",
                    "accepted": False,
                    "action": action["action"],
                    "error": "plan_required_before_code",
                }
            )
            messages.append({"role": "assistant", "content": raw.strip()})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "A plan is required before any pandas code or final answer. "
                        'Return {"action":"plan","plan":[...]}.'
                    ),
                }
            )
            continue

        plan = action["plan"]
        trace.append(
            {"phase": "plan", "accepted": True, "action": "plan", "plan": plan.copy()}
        )

        messages.append({"role": "assistant", "content": raw.strip()})
        messages.append(
            {
                "role": "user",
                "content": (
                    "Plan recorded.\n\n"
                    f"Plan progress:\n{_progress(plan, completed)}\n\n"
                    "Now execute plan step 1."
                ),
            }
        )

    # Phase 2: execute the frozen plan sequentially.
    while True:
        raw = llm.complete(messages)

        try:
            action = parse_action(raw)
        except (InvalidAgentJSON, InvalidAgentAction) as exc:
            trace.append(
                {
                    "phase": "execute",
                    "accepted": False,
                    "raw": raw,
                    "error": str(exc),
                }
            )
            messages.append({"role": "assistant", "content": raw.strip()})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"Protocol error: {exc}\n\n"
                        f"Plan progress:\n{_progress(plan, completed)}"
                    ),
                }
            )
            continue

        if action["action"] == "plan":
            messages.append({"role": "assistant", "content": raw.strip()})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Protocol error: the plan is already recorded and frozen.\n\n"
                        f"Plan progress:\n{_progress(plan, completed)}"
                    ),
                }
            )
            continue

        if action["action"] == "final_answer":
            if _next_step(plan, completed) is not None:
                trace.append(
                    {
                        "phase": "final",
                        "accepted": False,
                        "answer": action["answer"],
                        "error": "plan_not_complete",
                    }
                )
                messages.append({"role": "assistant", "content": raw.strip()})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "final_answer is not allowed before all plan steps finish.\n\n"
                            f"Plan progress:\n{_progress(plan, completed)}"
                        ),
                    }
                )
                continue

            trace.append(
                {
                    "phase": "final",
                    "accepted": True,
                    "action": "final_answer",
                    "answer": action["answer"],
                }
            )
            return action["answer"]

        step = action["plan_step"]
        expected_step = _next_step(plan, completed)

        if step > len(plan):
            error = f"plan_step {step} does not exist; plan has {len(plan)} steps"
        elif step in completed:
            error = f"plan_step {step} is already complete"
        elif step != expected_step:
            error = f"next unfinished step is {expected_step}, not {step}"
        else:
            error = None

        if error is not None:
            trace.append(
                {
                    "phase": "execute",
                    "accepted": False,
                    "plan_step": step,
                    "code": action["code"],
                    "error": error,
                }
            )
            messages.append({"role": "assistant", "content": raw.strip()})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"Protocol error: {error}\n\n"
                        f"Plan progress:\n{_progress(plan, completed)}"
                    ),
                }
            )
            continue

        observation = execute_pandas(action["code"], df)
        if observation["ok"]:
            completed.add(step)

        trace.append(
            {
                "phase": "execute",
                "accepted": True,
                "plan_step": step,
                "plan_step_description": plan[step - 1],
                "code": action["code"],
                "observation": observation,
                "step_completed": observation["ok"],
                "completed_steps": sorted(completed),
            }
        )

        messages.append({"role": "assistant", "content": raw.strip()})
        messages.append(
            {
                "role": "user",
                "content": _feedback(observation, plan, completed, step),
            }
        )


def run(task: dict, df: pd.DataFrame, llm):
    """Course Main public contract: exactly ``run(task, df, llm)``."""
    return _run_with_trace_internal(task, df, llm, trace=None)


def run_with_trace(task: dict, df: pd.DataFrame, llm):
    trace: list[dict] = []
    answer = _run_with_trace_internal(task, df, llm, trace=trace)
    return answer, trace
