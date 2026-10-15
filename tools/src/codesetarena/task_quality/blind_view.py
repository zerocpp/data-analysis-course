"""BlindTaskView 构建 + 实际 prompt 序列化 + 泄漏审计（§3/§4/§32/§33）。

泄漏防护是**代码级**的：本模块的输入参数里根本不存在 answer / reference，
审计函数再对最终发送给 LLM 的完整文本做哨兵检查，双保险、fail-closed。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from .models import BlindTaskView, PROMPT_VERSION

PREVIEW_ROWS = 5


def build_blind_task_view(
    task: dict,
    data_file: Path,
) -> tuple[BlindTaskView, pd.DataFrame]:
    """从 workspace 任务构造 BlindTaskView。

    只读取 task_id / description / task_statement / answer_type 与数据文件；
    **不读取** declared_answer / reference.py——参数里就没有它们的位置。
    """

    df = pd.read_csv(data_file)
    view = BlindTaskView(
        task_id=str(task.get("task_id") or ""),
        description=str(task.get("description") or ""),
        task_statement=str(task.get("task_statement") or task.get("question") or ""),
        answer_type=str(task.get("answer_type") or "auto"),
        data_files=[
            {
                "name": data_file.name,
                "sha256": hashlib.sha256(data_file.read_bytes()).hexdigest(),
                "rows": int(df.shape[0]),
                "columns": list(map(str, df.columns)),
                "dtypes": {str(c): str(t) for c, t in df.dtypes.items()},
                "preview_rows": json.loads(
                    df.head(PREVIEW_ROWS).to_json(orient="records", force_ascii=False)
                ),
            }
        ],
    )
    return view, df


def serialize_blind_prompt(view: BlindTaskView) -> str:
    """把 BlindTaskView 序列化为发给 LLM 的完整 prompt（§25 正式 Blind Prompt）。"""

    system = (
        "You are an independent reference-solution author.\n"
        "You are given only the public task specification and task data "
        "(schema + sample; the full data file is available to your generated program "
        "as ./data.csv at runtime).\n"
        "You must solve the task independently. You must not assume any hidden answer.\n"
        "Identify ambiguity or insufficient data if present: if the question has two "
        "reasonable interpretations with possibly different results, return status "
        "AMBIGUOUS instead of picking one silently; if the data cannot support the "
        "question, return INSUFFICIENT_DATA.\n"
        "Produce an executable Python reference implementation that derives the result "
        "only from the supplied task data. It must define `reference(df)` taking a "
        "pandas DataFrame and returning the final answer. Do not hard-code a guessed "
        "final answer; derive it from the data.\n"
        "Return a single JSON object matching exactly this schema:\n"
        "{\n"
        '  "status": "SOLVED" | "AMBIGUOUS" | "INSUFFICIENT_DATA" | "UNSOLVABLE" | "INTERNAL_ERROR",\n'
        '  "interpretation": "...",\n'
        '  "assumptions": [],\n'
        '  "ambiguities": [],\n'
        '  "data_sufficiency": "SUFFICIENT" | "INSUFFICIENT",\n'
        '  "algorithm_summary": "...",\n'
        '  "generated_reference_code": "...python source defining reference(df)...",\n'
        '  "confidence": 0.0\n'
        "}\n"
        "Return ONLY the JSON object, no markdown fences, no extra text."
    )
    payload = {
        "prompt_version": PROMPT_VERSION,
        "task": view.to_payload(),
        "requirements": {
            "solution_specification": [
                "interpretation of the question",
                "fields used",
                "filter / aggregation / ordering logic",
                "tie handling",
                "missing-value handling",
                "final answer_type",
            ],
            "generated_code_contract": [
                "define reference(df) -> final answer",
                "derive the answer only from the data",
                "no network, no file writes outside the sandbox",
            ],
        },
    }
    return system + "\n\nTASK_JSON:\n" + json.dumps(payload, ensure_ascii=False, indent=2)


def audit_leaks(payload_text: str, secrets: dict[str, str]) -> dict[str, bool]:
    """对**实际发送文本**做哨兵泄漏审计（§32/§33）。

    ``secrets``: {标签: 不允许出现的敏感值}（答案值、reference 源码、学生结果 repr…）。
    返回 {标签: 是否泄漏}。空值跳过。
    """

    return {
        label: (bool(value) and value in payload_text)
        for label, value in secrets.items()
    }


def audit_sandbox_files(sandbox: Path) -> list[str]:
    """Blind Sandbox 文件系统审计（§11）：禁止文件出现即返回非空列表。"""

    from .models import SANDBOX_FORBIDDEN_FILES

    forbidden_names = {n.lower() for n in SANDBOX_FORBIDDEN_FILES}
    return [
        str(p.relative_to(sandbox))
        for p in sorted(sandbox.rglob("*"))
        if p.is_file() and p.name.lower() in forbidden_names
    ]
