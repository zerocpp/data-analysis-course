"""Agent-B: Traceable LLM + Pandas Agent Harness.

在 Agent-A Minimal Harness 基础上增加一个非常短的 ``thought`` 字段，用来记录
“这一轮为什么选择这个动作”的决策摘要。

注意：
- thought 只允许一两句短决策理由，不要求、也不保存长篇 chain-of-thought；
- Agent 仍然遵循 Observe -> Decide -> Execute -> Observe 循环；
- 对外仍可直接调用 ``run(task, df, llm)``；
- 若需要拿到完整轨迹，可使用 ``run_with_trace(task, df, llm)``，或向
  ``run(..., trace=some_list)`` 传入一个 list。

资源预算（总时间 / 内存 / 累计 Token）仍由 Agent 外部 Runtime / Supervisor 管理。
"""

from __future__ import annotations

import ast
import contextlib
import io
import json

import pandas as pd

_OUTPUT_LIMIT = 6000

SYSTEM_PROMPT = """You are a traceable data-analysis agent operating on a pandas DataFrame.

You are given:
1. a task description;
2. access to a pandas DataFrame named `df`.

You solve the task iteratively.

At every step, return exactly one JSON object with:
- `thought`: one very short decision rationale (one or two sentences at most);
- `action`: either `execute_pandas` or `final_answer`.

For execute_pandas:
{"thought": "...", "action": "execute_pandas", "code": "..."}

For final_answer:
{"thought": "...", "action": "final_answer", "answer": ...}

The runtime executes pandas/Python code against `df` and returns an Observation.
After an Observation, decide whether more analysis is needed or whether the task is solved.

Rules:
- use `df` as the input dataset;
- pandas is available as `pd`;
- do not read files;
- do not access the network;
- do not import modules;
- do not invent values that have not been observed;
- prefer short and verifiable pandas operations;
- `thought` must be a short decision summary, not a long hidden reasoning trace;
- return JSON only;
- do not include Markdown fences;
- preserve the answer format requested by the task.
"""

_GUARD_FORBIDDEN_CALLS = frozenset({"open", "exec", "eval"})


def build_initial_messages(task: dict, df: pd.DataFrame) -> list[dict[str, str]]:
    """System Prompt + 初始 Task 消息；只暴露 shape / columns / dtypes。"""

    info = (
        f"rows: {len(df)}\n"
        f"columns: {list(df.columns)}\n"
        f"dtypes:\n{df.dtypes.to_string()}"
    )
    user = (
        f"Task:\n{json.dumps(task, ensure_ascii=False, default=str)}\n\n"
        f"DataFrame information:\n{info}\n\n"
        "A pandas DataFrame named `df` is available through execute_pandas. Begin."
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def parse_action(raw: str) -> dict:
    """严格解析 thought + Action；错误返回 Observation 兼容 dict。"""

    try:
        data = json.loads((raw or "").strip())
    except json.JSONDecodeError:
        return {"ok": False, "error": "Invalid JSON Action"}

    if not isinstance(data, dict):
        return {"ok": False, "error": "Invalid JSON Action"}

    thought = data.get("thought", "")
    if not isinstance(thought, str):
        return {"ok": False, "error": "Missing or invalid 'thought'"}
    thought = thought.strip()

    action = data.get("action")

    if action == "execute_pandas":
        code = data.get("code")
        if not isinstance(code, str) or not code.strip():
            return {"ok": False, "error": "Missing or invalid 'code'"}
        return {
            "ok": True,
            "thought": thought,
            "action": "execute_pandas",
            "code": code,
        }

    if action == "final_answer":
        if "answer" not in data:
            return {"ok": False, "error": "Missing or invalid 'answer'"}
        return {
            "ok": True,
            "thought": thought,
            "action": "final_answer",
            "answer": data["answer"],
        }

    return {"ok": False, "error": f"Invalid action: {action!r}"}


def _forbidden_reason(code: str) -> str | None:
    """最小代码 Guard：仅禁 import / from-import / open / exec / eval。"""

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
            and node.func.id in _GUARD_FORBIDDEN_CALLS
        ):
            return f"{node.func.id}() is not allowed"
    return None


def execute_pandas(code: str, df: pd.DataFrame) -> dict:
    """在 {df, pd} 命名空间执行模型代码，返回 stdout + 末尾表达式值。"""

    reason = _forbidden_reason(code)
    if reason is not None:
        return {"ok": False, "error": f"ForbiddenCode: {reason}"}

    namespace: dict = {"df": df, "pd": pd}
    buffer = io.StringIO()

    try:
        tree = ast.parse(code)
        last = tree.body[-1] if tree.body and isinstance(tree.body[-1], ast.Expr) else None
        body = tree.body[:-1] if last is not None else tree.body

        with contextlib.redirect_stdout(buffer):
            if body:
                exec(  # noqa: S102
                    compile(ast.Module(body=body, type_ignores=[]), "<agent-code>", "exec"),
                    namespace,
                )

            value = None
            if last is not None:
                value = eval(  # noqa: S307
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

    output = "\n".join(parts) or "<no output>"
    return {"ok": True, "output": output[:_OUTPUT_LIMIT]}


def _observation_message(observation: dict) -> str:
    if observation["ok"]:
        return f"Observation:\n{observation['output']}"
    return (
        f"Observation (error):\n{observation['error']}\n"
        "Please fix the action and continue."
    )


def _run_with_trace_internal(
    task: dict,
    df: pd.DataFrame,
    llm,
    trace: list[dict] | None = None,
):
    """Thought -> Action -> Observation -> ... -> final_answer.

    ``trace`` 若传入 list，会原地追加每一轮记录。
    """

    if trace is None:
        trace = []

    messages = build_initial_messages(task, df)
    step = 0

    while True:
        step += 1
        raw = llm.complete(messages)
        action = parse_action(raw)

        if not action["ok"]:
            observation = action
            trace.append(
                {
                    "step": step,
                    "thought": "",
                    "action": "invalid_action",
                    "raw": raw,
                    "observation": observation,
                }
            )
            messages.append({"role": "assistant", "content": raw.strip()})
            messages.append({"role": "user", "content": _observation_message(observation)})
            continue

        if action["action"] == "final_answer":
            trace.append(
                {
                    "step": step,
                    "thought": action["thought"],
                    "action": "final_answer",
                    "answer": action["answer"],
                }
            )
            return action["answer"]

        observation = execute_pandas(action["code"], df)

        trace.append(
            {
                "step": step,
                "thought": action["thought"],
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
    """便捷接口：返回 ``(answer, trace)``。"""

    trace: list[dict] = []
    answer = _run_with_trace_internal(task, df, llm, trace=trace)
    return answer, trace
