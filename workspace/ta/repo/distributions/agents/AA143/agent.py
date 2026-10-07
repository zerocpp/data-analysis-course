"""Agent-E: Execute-Then-Verify LLM + Pandas Agent Harness.

核心流程：

    Task
      ↓
    LLM Generate pandas code
      ↓
    Execute
      ↓
    Observation
      ↓
    Forced Verify Stage
      ↓
    Does the current Observation truly answer the Task?
      ├─ FAIL -> generate new pandas code
      └─ PASS -> final_answer

与 Agent-A 的关键区别：
- Agent-A 在看到 Observation 后，可以直接 final_answer；
- Agent-E 在每次 execute_pandas 后，Harness 强制进入 verify_result 阶段；
- 未经过 Verify 的 Observation 永远不能直接 final_answer；
- Verify=FAIL 后只能重新生成 pandas 代码；
- Verify=PASS 后才允许 final_answer。

Verify 检查的是：
    “当前 Observation 是否已经真正回答了原始 Task？”

它不是单纯检查代码有没有报错，也不是检查语法是否正确。

对外接口：
    run(task: dict, df: pd.DataFrame, llm)

需要轨迹时：
    answer, trace = run_with_trace(task, df, llm)

资源预算（总时间 / Token / 内存 / 总调用次数）由外层 Runtime / Supervisor 管理。
"""

from __future__ import annotations

import ast
import contextlib
import io
import json

import pandas as pd


_OUTPUT_LIMIT = 6000

SYSTEM_PROMPT = """You are an Execute-Then-Verify data-analysis agent operating on a pandas DataFrame.

You are given:
1. a task description;
2. access to a pandas DataFrame named `df`.

You MUST follow this protocol:

PHASE 1 — GENERATE CODE
Generate pandas/Python code against `df`.

Return:
{"action":"execute_pandas","code":"..."}

The runtime executes the code and returns an Observation.

PHASE 2 — VERIFY
After EVERY Observation, you MUST verify whether the CURRENT Observation truly answers the original Task.

You MUST return exactly one verify action:

PASS:
{"action":"verify_result","verdict":"pass","reason":"short reason"}

FAIL:
{"action":"verify_result","verdict":"fail","reason":"short reason"}

Verification question:
Does the current Observation itself provide sufficient and task-correct evidence to answer the original Task?

Verify PASS only when:
- the analysis operation matches the Task;
- the required filter / column / formula / aggregation / date boundary is correct;
- the Observation contains the needed result;
- the requested answer format can be produced from the Observation;
- there is no unresolved execution error.

Verify FAIL when:
- the wrong column, filter, formula, aggregation, boundary, or statistic was used;
- the Observation is only exploratory and does not answer the Task;
- the result is incomplete or ambiguous;
- execution failed;
- more pandas analysis is still required.

After VERIFY FAIL:
Generate new pandas code:
{"action":"execute_pandas","code":"..."}

After VERIFY PASS:
Return the final answer:
{"action":"final_answer","answer":...}

STRICT PROTOCOL RULES:
- You may NOT return final_answer immediately after execute_pandas.
- Every execute_pandas result MUST be followed by verify_result.
- After verify_result=fail, the next valid action is execute_pandas.
- After verify_result=pass, the next valid action is final_answer.
- Do not use verify_result before any Observation exists.

PANDAS RULES:
- use `df` as the input dataset;
- pandas is available as `pd`;
- every code block must be self-contained and start from `df`;
- do not read files;
- do not access the network;
- do not import modules;
- do not invent values that have not been observed;
- prefer short and verifiable pandas operations;
- preserve the exact answer format requested by the Task.

OUTPUT RULES:
- return JSON only;
- do not include Markdown fences;
- keep `reason` short;
- do not output long chain-of-thought.
"""


class InvalidAgentJSON(Exception):
    """LLM reply cannot be parsed as a JSON action."""


class InvalidAgentAction(Exception):
    """Action is unknown, malformed, or invalid for the current phase."""


_FORBIDDEN_CALLS = frozenset(
    {"open", "exec", "eval", "compile", "__import__"}
)

_FORBIDDEN_MODULES = frozenset(
    {"os", "sys", "subprocess", "socket", "requests", "pathlib", "shutil"}
)


def build_initial_messages(
    task: dict,
    df: pd.DataFrame,
) -> list[dict[str, str]]:
    """Initial prompt exposes only Task + shape / columns / dtypes."""

    info = (
        f"rows: {len(df)}\n"
        f"columns: {list(df.columns)}\n"
        f"dtypes:\n{df.dtypes.to_string()}"
    )

    user = (
        f"Task:\n{json.dumps(task, ensure_ascii=False, default=str)}\n\n"
        f"DataFrame information:\n{info}\n\n"
        "A pandas DataFrame named `df` is available through execute_pandas.\n"
        "Begin by generating pandas code."
    )

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def _extract_json(raw: str) -> dict:
    """Light JSON parsing tolerance for fences / small surrounding noise."""

    text = (raw or "").strip()

    if text.startswith("```"):
        first_line = text.find("\n")
        if first_line != -1:
            text = text[first_line + 1 :]

    if text.endswith("```"):
        text = text[:-3]

    text = text.strip()

    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1 or end < start:
        raise InvalidAgentJSON(
            f"response is not JSON: {text[:120]!r}"
        )

    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise InvalidAgentJSON(
            f"invalid JSON: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise InvalidAgentJSON(
            "action payload must be a JSON object"
        )

    return data


def parse_action(raw: str) -> dict:
    """Parse execute_pandas / verify_result / final_answer."""

    data = _extract_json(raw)
    action = data.get("action")

    if action == "execute_pandas":
        code = data.get("code")

        if not isinstance(code, str) or not code.strip():
            raise InvalidAgentAction(
                "execute_pandas requires non-empty 'code'"
            )

        return {
            "action": "execute_pandas",
            "code": code,
        }

    if action == "verify_result":
        verdict = data.get("verdict")
        reason = data.get("reason", "")

        if verdict not in {"pass", "fail"}:
            raise InvalidAgentAction(
                "verify_result requires verdict 'pass' or 'fail'"
            )

        if not isinstance(reason, str):
            raise InvalidAgentAction(
                "verify_result 'reason' must be a string"
            )

        return {
            "action": "verify_result",
            "verdict": verdict,
            "reason": reason.strip(),
        }

    if action == "final_answer":
        if "answer" not in data:
            raise InvalidAgentAction(
                "final_answer requires 'answer'"
            )

        return {
            "action": "final_answer",
            "answer": data["answer"],
        }

    raise InvalidAgentAction(
        f"unknown action {action!r}"
    )


def _forbidden_reason(code: str) -> str | None:
    """Minimal static guardrail; this is not a full sandbox."""

    try:
        tree = ast.parse(code)
    except SyntaxError:
        # Syntax errors become execution Observations so the model can repair them.
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            return (
                f"import is not allowed: "
                f"{node.names[0].name}"
            )

        if isinstance(node, ast.ImportFrom):
            return (
                f"import is not allowed: "
                f"from {node.module or 'relative'}"
            )

        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in _FORBIDDEN_CALLS
        ):
            return (
                f"{node.func.id}() is not allowed"
            )

        if (
            isinstance(node, ast.Name)
            and node.id in _FORBIDDEN_MODULES
        ):
            return (
                f"module {node.id!r} is not allowed"
            )

    return None


def execute_pandas(
    code: str,
    df: pd.DataFrame,
) -> dict:
    """Execute one self-contained pandas program against the original df."""

    reason = _forbidden_reason(code)

    if reason is not None:
        return {
            "ok": False,
            "error": f"ForbiddenCode: {reason}",
        }

    namespace = {
        "df": df,
        "pd": pd,
    }

    buffer = io.StringIO()

    try:
        tree = ast.parse(code)

        last = (
            tree.body[-1]
            if tree.body
            and isinstance(tree.body[-1], ast.Expr)
            else None
        )

        body = (
            tree.body[:-1]
            if last is not None
            else tree.body
        )

        with contextlib.redirect_stdout(buffer):
            if body:
                exec(  # noqa: S102
                    compile(
                        ast.Module(
                            body=body,
                            type_ignores=[],
                        ),
                        "<agent-code>",
                        "exec",
                    ),
                    namespace,
                )

            value = None

            if last is not None:
                value = eval(  # noqa: S307
                    compile(
                        ast.Expression(last.value),
                        "<agent-code>",
                        "eval",
                    ),
                    namespace,
                )

    except Exception as exc:
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }

    parts = []

    stdout = buffer.getvalue()

    if stdout:
        parts.append(
            stdout.rstrip("\n")
        )

    if value is not None:
        parts.append(
            repr(value)
        )

    output = (
        "\n".join(parts)
        or "<no output>"
    )

    return {
        "ok": True,
        "output": output[:_OUTPUT_LIMIT],
    }


def _observation_message(
    observation: dict,
) -> str:
    """After execution, force the next model action to be verify_result."""

    if observation["ok"]:
        return (
            "Observation:\n"
            f"{observation['output']}\n\n"
            "FORCED VERIFY STAGE:\n"
            "You may NOT return final_answer yet.\n"
            "Check whether this current Observation truly answers the original Task.\n"
            "Your next action MUST be verify_result with verdict pass or fail."
        )

    return (
        "Observation (error):\n"
        f"{observation['error']}\n\n"
        "FORCED VERIFY STAGE:\n"
        "The execution failed, so this Observation cannot answer the Task.\n"
        "Your next action MUST be verify_result. "
        "The verdict should be fail."
    )


def _protocol_error_message(
    state: str,
    error: str,
) -> str:
    """Tell the model what action is legal in the current state."""

    if state == "need_code":
        expected = (
            'The next valid action is '
            '{"action":"execute_pandas","code":"..."}.'
        )

    elif state == "need_verify":
        expected = (
            'The next valid action is '
            '{"action":"verify_result","verdict":"pass|fail","reason":"..."}.'
        )

    elif state == "need_final":
        expected = (
            'Verification already PASSED. '
            'The next valid action is '
            '{"action":"final_answer","answer":...}.'
        )

    else:
        expected = "Follow the Execute-Then-Verify protocol."

    return (
        f"Protocol error:\n{error}\n\n"
        f"{expected}"
    )


def _run_with_trace_internal(
    task: dict,
    df: pd.DataFrame,
    llm,
    trace: list[dict] | None = None,
):
    """Strict Execute -> Observation -> Verify -> (Retry | Final) loop.

    State machine:
        need_code
            -> execute_pandas
            -> need_verify

        need_verify
            -> verify_result=fail
                -> need_code
            -> verify_result=pass
                -> need_final

        need_final
            -> final_answer
            -> return

    Resource budgets are external Runtime / Supervisor responsibilities.
    """

    if trace is None:
        trace = []

    messages = build_initial_messages(
        task,
        df,
    )

    state = "need_code"
    last_observation: dict | None = None
    last_code: str | None = None

    while True:
        raw = llm.complete(messages)

        try:
            action = parse_action(raw)

        except (
            InvalidAgentJSON,
            InvalidAgentAction,
        ) as exc:
            trace.append(
                {
                    "state": state,
                    "action": "invalid",
                    "raw": raw,
                    "error": str(exc),
                }
            )

            messages.append(
                {
                    "role": "assistant",
                    "content": raw.strip(),
                }
            )

            messages.append(
                {
                    "role": "user",
                    "content": _protocol_error_message(
                        state,
                        str(exc),
                    ),
                }
            )

            continue

        # -----------------------------------------------------
        # STATE 1: Generate pandas code
        # -----------------------------------------------------
        if state == "need_code":
            if action["action"] != "execute_pandas":
                error = (
                    "A pandas execution is required before verification "
                    "or final_answer."
                )

                trace.append(
                    {
                        "state": state,
                        "action": action["action"],
                        "accepted": False,
                        "error": error,
                    }
                )

                messages.append(
                    {
                        "role": "assistant",
                        "content": raw.strip(),
                    }
                )

                messages.append(
                    {
                        "role": "user",
                        "content": _protocol_error_message(
                            state,
                            error,
                        ),
                    }
                )

                continue

            last_code = action["code"]

            observation = execute_pandas(
                last_code,
                df,
            )

            last_observation = observation

            trace.append(
                {
                    "state": state,
                    "action": "execute_pandas",
                    "accepted": True,
                    "code": last_code,
                    "observation": observation,
                }
            )

            messages.append(
                {
                    "role": "assistant",
                    "content": raw.strip(),
                }
            )

            messages.append(
                {
                    "role": "user",
                    "content": _observation_message(
                        observation,
                    ),
                }
            )

            state = "need_verify"
            continue

        # -----------------------------------------------------
        # STATE 2: Forced Verify stage
        # -----------------------------------------------------
        if state == "need_verify":
            if action["action"] != "verify_result":
                error = (
                    "Every execute_pandas Observation MUST be verified "
                    "before any new code or final_answer."
                )

                trace.append(
                    {
                        "state": state,
                        "action": action["action"],
                        "accepted": False,
                        "error": error,
                    }
                )

                messages.append(
                    {
                        "role": "assistant",
                        "content": raw.strip(),
                    }
                )

                messages.append(
                    {
                        "role": "user",
                        "content": _protocol_error_message(
                            state,
                            error,
                        ),
                    }
                )

                continue

            verdict = action["verdict"]

            # An execution error can never be verified as PASS.
            if (
                verdict == "pass"
                and last_observation is not None
                and not last_observation["ok"]
            ):
                error = (
                    "The previous pandas execution failed. "
                    "An error Observation cannot receive verification PASS."
                )

                trace.append(
                    {
                        "state": state,
                        "action": "verify_result",
                        "accepted": False,
                        "verdict": verdict,
                        "reason": action["reason"],
                        "error": error,
                    }
                )

                messages.append(
                    {
                        "role": "assistant",
                        "content": raw.strip(),
                    }
                )

                messages.append(
                    {
                        "role": "user",
                        "content": _protocol_error_message(
                            state,
                            error,
                        ),
                    }
                )

                continue

            trace.append(
                {
                    "state": state,
                    "action": "verify_result",
                    "accepted": True,
                    "verdict": verdict,
                    "reason": action["reason"],
                    "code_being_verified": last_code,
                    "observation_being_verified": last_observation,
                }
            )

            messages.append(
                {
                    "role": "assistant",
                    "content": raw.strip(),
                }
            )

            if verdict == "fail":
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Verification result: FAIL.\n"
                            "The current Observation does not sufficiently "
                            "answer the original Task.\n"
                            "Generate a new self-contained pandas program "
                            "that corrects the problem."
                        ),
                    }
                )

                state = "need_code"
                continue

            # verdict == pass
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Verification result: PASS.\n"
                        "The current Observation has been accepted as "
                        "sufficient evidence for the original Task.\n"
                        "Return final_answer now, preserving the exact "
                        "format requested by the Task."
                    ),
                }
            )

            state = "need_final"
            continue

        # -----------------------------------------------------
        # STATE 3: Final answer only after verification PASS
        # -----------------------------------------------------
        if state == "need_final":
            if action["action"] != "final_answer":
                error = (
                    "Verification has already PASSED. "
                    "The next action must be final_answer."
                )

                trace.append(
                    {
                        "state": state,
                        "action": action["action"],
                        "accepted": False,
                        "error": error,
                    }
                )

                messages.append(
                    {
                        "role": "assistant",
                        "content": raw.strip(),
                    }
                )

                messages.append(
                    {
                        "role": "user",
                        "content": _protocol_error_message(
                            state,
                            error,
                        ),
                    }
                )

                continue

            trace.append(
                {
                    "state": state,
                    "action": "final_answer",
                    "accepted": True,
                    "answer": action["answer"],
                    "verified_code": last_code,
                    "verified_observation": last_observation,
                }
            )

            return action["answer"]


def run(task: dict, df: pd.DataFrame, llm):
    """Course Main public contract: exactly ``run(task, df, llm)``."""
    return _run_with_trace_internal(task, df, llm, trace=None)


def run_with_trace(
    task: dict,
    df: pd.DataFrame,
    llm,
):
    """Return (answer, trace) for analysis and debugging."""

    trace: list[dict] = []

    answer = _run_with_trace_internal(
        task,
        df,
        llm,
        trace=trace,
    )

    return answer, trace
