"""Generic LLM + Pandas Agent（与具体 Task 解耦的通用 agent.py 模板）。

本文件是 workspace 中 ``agent.py`` 的唯一权威模板：换 task.json / data.csv /
reference.py **不需要**修改本文件。对外的唯一接口::

    run(task: dict, df: pd.DataFrame, llm)

协议：LLM 每轮只输出一个 JSON Action（``execute_pandas`` / ``final_answer``）；
``execute_pandas`` 的代码在仅含 ``{df, pd}`` 的命名空间中执行，stdout 与末尾
表达式的值作为 Observation 返回给 LLM；解析失败与执行错误同样是 Observation，
由 LLM 读取后自行修正并继续——这才是完整的 Observe → Decide → Execute 循环。

本文件不管理任何资源预算（时间 / 内存 / 累计 Token）：那是 Agent 外部
Budget 的职责，由工程 Runner 通过 ``run_with_budget`` 承担。
"""

from __future__ import annotations

import ast
import contextlib
import io
import json

import pandas as pd

_OUTPUT_LIMIT = 6000  # Observation 截断，保护上下文长度

SYSTEM_PROMPT = """You are a data-analysis agent operating on a pandas DataFrame.

You are given:
1. a task description;
2. access to a pandas DataFrame named `df`.

You do not read files directly.

You solve the task iteratively.

At every step, choose exactly one action:

1. execute_pandas
   Execute pandas/Python analysis against `df`.

2. final_answer
   Return the final answer when sufficient evidence has been obtained.

For execute_pandas, return valid JSON:

{"action": "execute_pandas", "code": "..."}

The environment executes the code and returns its observation to you.

After receiving the observation:
- inspect the result;
- decide whether additional analysis is required;
- request another execute_pandas action if needed;
- otherwise return final_answer.

For final answer:

{"action": "final_answer", "answer": ...}

Rules:
- use `df` as the input dataset;
- pandas is available as `pd`;
- do not attempt to read files;
- do not access the network;
- do not import external modules;
- do not invent values that have not been observed;
- prefer short and verifiable pandas operations;
- be efficient: your token budget is limited. Solve the task in as few
  execute_pandas steps as possible (often one expression computes the final
  result directly), then return final_answer;
- format the final answer EXACTLY as the task requires:
  * integer result -> plain integer text (e.g. "4");
  * fixed decimals ("保留两位小数" etc.) -> always show that many digits
    after the decimal point, including trailing zeros (85 -> "85.00",
    0.3 -> "0.30");
  * any other explicit format in the task -> follow it literally;
  * return only the value itself, no units, no explanations, no code fences;
- return JSON only;
- do not include Markdown fences.
You may optionally include a very short "summary" field describing the step;
do not output long chain-of-thought."""

_GUARD_FORBIDDEN_CALLS = frozenset({"open", "exec", "eval"})


def build_initial_messages(task: dict, df: pd.DataFrame) -> list[dict[str, str]]:
    """System Prompt + 初始 Task 消息（只给 shape/columns/dtypes，不给数据本身）。"""

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
    """解析 LLM 回复为 Action；只接受严格 JSON，失败返回 Observation 兼容 dict。

    成功::

        {"ok": True, "action": "execute_pandas", "code": "..."}
        {"ok": True, "action": "final_answer", "answer": ...}

    失败::

        {"ok": False, "error": "Invalid JSON Action"}
    """

    try:
        data = json.loads((raw or "").strip())
    except json.JSONDecodeError:
        return {"ok": False, "error": "Invalid JSON Action"}
    if not isinstance(data, dict):
        return {"ok": False, "error": "Invalid JSON Action"}
    action = data.get("action")
    if action == "execute_pandas":
        code = data.get("code")
        if not isinstance(code, str) or not code.strip():
            return {"ok": False, "error": "Missing or invalid 'code'"}
        return {"ok": True, "action": "execute_pandas", "code": code}
    if action == "final_answer":
        if "answer" not in data:
            return {"ok": False, "error": "Missing or invalid 'answer'"}
        return {"ok": True, "action": "final_answer", "answer": data["answer"]}
    return {"ok": False, "error": f"Invalid action: {action!r}"}


def _forbidden_reason(code: str) -> str | None:
    """Minimal Agent Code Guard：仅禁 import / from-import / open / exec / eval。

    这不是沙箱，也不是完整安全边界；语法错误交给执行阶段作为 Observation 返回。
    """

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
    """在 {df, pd} 命名空间中执行模型代码；捕获 stdout + 末尾表达式值 + 异常。

    不管理执行时间：时间属于 Agent 外部 Budget。
    """
    # df 是共享对象引用；查询不会改变它，但原地修改操作可能永久改变本次 Agent Run 中的 DataFrame。
    reason = _forbidden_reason(code)
    if reason is not None:
        return {"ok": False, "error": f"ForbiddenCode: {reason}"}
    namespace: dict = {"df": df, "pd": pd}
    buffer = io.StringIO()
    try:
        # 把 LLM 生成的一整段 Python 字符串，拆成“可以被程序看懂的语法结构”
        # LLM 生成：

        # x = df["score"].mean()
        # x + 10

        # 如果不解析成 tree，你看到的只是一整段字符串：

        # "x = df['score'].mean()\nx + 10"
        tree = ast.parse(code)
        last = tree.body[-1] if tree.body and isinstance(tree.body[-1], ast.Expr) else None
        body = tree.body[:-1] if last is not None else tree.body
        with contextlib.redirect_stdout(buffer):
            if body:
                exec(  # noqa: S102 - 模型代码运行于受限命名空间，见模块 docstring
                    compile(ast.Module(body=body, type_ignores=[]), "<agent-code>", "exec"),
                    namespace,
                )
            value = None
            if last is not None:
                value = eval(  # noqa: S307 - 同上
                    compile(ast.Expression(last.value), "<agent-code>", "eval"), namespace
                )
    except Exception as exc:  # 执行错误是 Observation，不是 Agent 失败
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
        "Please fix the code and continue."
    )


def run(task: dict, df: pd.DataFrame, llm):
    """通用 Agent Loop：Observe → Decide → Execute → … → final_answer。

    唯一终止条件是 LLM 给出 final_answer。资源预算（时间 / 内存 / 累计
    Token）不属于本函数——由 Agent 外部 Budget 负责监控与终止。
    """

    messages = build_initial_messages(task, df)
    while True:
        raw = llm.complete(messages)
        action = parse_action(raw)
        if not action["ok"]:
            messages.append({"role": "assistant", "content": raw.strip()})
            messages.append({"role": "user", "content": _observation_message(action)})
            continue
        if action["action"] == "final_answer":
            return action["answer"]
        observation = execute_pandas(action["code"], df)
        messages.append({"role": "assistant", "content": raw.strip()})
        messages.append({"role": "user", "content": _observation_message(observation)})
