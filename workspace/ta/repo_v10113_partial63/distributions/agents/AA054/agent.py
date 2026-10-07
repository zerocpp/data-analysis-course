"""Agent-C: Stateful Pandas Workspace Agent.

核心思想：
- LLM 仍然自由生成 pandas/Python 代码；
- 执行命名空间跨轮次持久化；
- 上一轮创建的中间 DataFrame / Series / 标量变量可在下一轮继续使用；
- Observation 会返回轻量 Workspace Summary；
- 最终仍由 LLM 决定何时输出 final_answer。

典型流程：

    Task
      ↓
    Generate pandas code
      ↓
    Execute in persistent workspace
      ↓
    Observation + Workspace Summary
      ↓
    Generate next pandas code using previous variables
      ↓
    Execute in the SAME workspace
      ↓
    ...
      ↓
    Final Answer

第三方依赖仅 pandas。
资源预算由外层 Runtime / Supervisor 管理。
"""

from __future__ import annotations

import ast
import contextlib
import io
import json
from typing import Any

import pandas as pd

_OUTPUT_LIMIT = 6000
_WORKSPACE_SUMMARY_LIMIT = 3000

SYSTEM_PROMPT = """You are a stateful pandas data-analysis agent.

You are given:
1. a task description;
2. a pandas DataFrame named `df`.

The runtime provides a PERSISTENT pandas workspace.

This means:
- variables you create in one execute_pandas action remain available later;
- intermediate DataFrames / Series / scalar results can be reused;
- you do not need to recompute previous transformations from `df` every turn.

At every step choose exactly one action:

1. execute_pandas
   {"action":"execute_pandas","code":"..."}

2. final_answer
   {"action":"final_answer","answer":...}

After each execute_pandas action, the runtime returns:
- the execution Observation;
- a lightweight Workspace Summary listing reusable variables.

Use the workspace deliberately:
- create clear intermediate variables for multi-step analysis;
- reuse existing variables instead of recomputing the same transformation;
- inspect intermediate results when needed;
- keep variable names short and meaningful;
- prefer non-inplace transformations unless mutation is intentional.

Rules:
- `df` is the working DataFrame;
- pandas is available as `pd`;
- do not read files;
- do not access the network;
- do not import modules;
- do not invent values that have not been observed;
- prefer short and verifiable pandas operations;
- return JSON only;
- do not include Markdown fences;
- preserve the exact answer format requested by the task.
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
        "A persistent pandas workspace is ready. `df` and `pd` are available. Begin."
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

    if action == "execute_pandas":
        code = data.get("code")
        if not isinstance(code, str) or not code.strip():
            raise InvalidAgentAction("execute_pandas requires non-empty 'code'")
        return {"action": "execute_pandas", "code": code}

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


def _value_summary(value: Any) -> str:
    if isinstance(value, pd.DataFrame):
        cols = list(value.columns)
        shown = cols[:12]
        suffix = "..." if len(cols) > 12 else ""
        return f"DataFrame(shape={value.shape}, columns={shown}{suffix})"
    if isinstance(value, pd.Series):
        return f"Series(len={len(value)}, dtype={value.dtype}, name={value.name!r})"
    if isinstance(value, pd.Index):
        return f"Index(len={len(value)}, dtype={value.dtype})"
    if isinstance(value, (str, int, float, bool, type(None))):
        text = repr(value)
        if len(text) > 160:
            text = text[:157] + "..."
        return f"{type(value).__name__}({text})"
    if isinstance(value, (list, tuple, set, dict)):
        return f"{type(value).__name__}(len={len(value)})"
    return type(value).__name__


def _workspace_summary(namespace: dict) -> str:
    lines = []
    if "df" in namespace:
        lines.append(f"df: {_value_summary(namespace['df'])}")

    for name in sorted(namespace):
        if name in {"df", "pd", "__builtins__"} or name.startswith("__"):
            continue
        try:
            summary = _value_summary(namespace[name])
        except Exception as exc:
            summary = f"<summary-error {type(exc).__name__}>"
        lines.append(f"{name}: {summary}")

    return ("\n".join(lines) or "<empty workspace>")[:_WORKSPACE_SUMMARY_LIMIT]


class PandasWorkspace:
    """Persistent execution namespace shared across all Agent turns."""

    def __init__(self, df: pd.DataFrame):
        # Protect the caller's original DataFrame from Agent-side mutation.
        self.namespace: dict[str, Any] = {
            "df": df.copy(deep=True),
            "pd": pd,
        }

    def execute(self, code: str) -> dict:
        reason = _forbidden_reason(code)
        if reason is not None:
            return {
                "ok": False,
                "error": f"ForbiddenCode: {reason}",
                "workspace": _workspace_summary(self.namespace),
            }

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
                        self.namespace,
                    )

                value = None
                if last is not None:
                    value = eval(
                        compile(ast.Expression(last.value), "<agent-code>", "eval"),
                        self.namespace,
                    )

        except Exception as exc:
            return {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "workspace": _workspace_summary(self.namespace),
            }

        parts = []
        stdout = buffer.getvalue()
        if stdout:
            parts.append(stdout.rstrip("\n"))
        if value is not None:
            parts.append(repr(value))

        return {
            "ok": True,
            "output": ("\n".join(parts) or "<no output>")[:_OUTPUT_LIMIT],
            "workspace": _workspace_summary(self.namespace),
        }


def _observation_message(observation: dict) -> str:
    workspace = observation.get("workspace", "<workspace unavailable>")

    if observation["ok"]:
        return (
            "Observation:\n"
            f"{observation['output']}\n\n"
            "Persistent Workspace:\n"
            f"{workspace}\n\n"
            "Variables listed above remain available in the next execute_pandas action. "
            "Reuse them when useful."
        )

    return (
        "Observation (error):\n"
        f"{observation['error']}\n\n"
        "Persistent Workspace after the failed execution:\n"
        f"{workspace}\n\n"
        "The workspace is still alive. Fix the code and continue."
    )


def _run_with_trace_internal(task: dict, df: pd.DataFrame, llm, trace: list[dict] | None = None):
    """Stateful Observe -> Decide -> Execute -> ... -> final_answer loop."""

    if trace is None:
        trace = []

    messages = build_initial_messages(task, df)
    workspace = PandasWorkspace(df)
    step = 0

    while True:
        step += 1
        raw = llm.complete(messages)

        try:
            action = parse_action(raw)
        except (InvalidAgentJSON, InvalidAgentAction) as exc:
            trace.append(
                {
                    "step": step,
                    "action": "invalid",
                    "raw": raw,
                    "error": str(exc),
                }
            )
            messages.append({"role": "assistant", "content": raw.strip()})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"Protocol error:\n{exc}\n\n"
                        "Return either execute_pandas or final_answer as valid JSON."
                    ),
                }
            )
            continue

        if action["action"] == "final_answer":
            trace.append(
                {
                    "step": step,
                    "action": "final_answer",
                    "answer": action["answer"],
                    "workspace": _workspace_summary(workspace.namespace),
                }
            )
            return action["answer"]

        observation = workspace.execute(action["code"])

        trace.append(
            {
                "step": step,
                "action": "execute_pandas",
                "code": action["code"],
                "observation": observation,
            }
        )

        messages.append({"role": "assistant", "content": raw.strip()})
        messages.append({"role": "user", "content": _observation_message(observation)})


def run(task: dict, df: pd.DataFrame, llm):
    """Course Main public contract: exactly ``run(task, df, llm)``."""
    return _run_with_trace_internal(task, df, llm, trace=None)


def run_with_trace(task: dict, df: pd.DataFrame, llm):
    trace: list[dict] = []
    answer = _run_with_trace_internal(task, df, llm, trace=trace)
    return answer, trace
