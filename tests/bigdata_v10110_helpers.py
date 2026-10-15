"""Shared helpers for the BigData v10.1.10 contract tests (§47)."""
from __future__ import annotations

import io
import json
import os
import zipfile
from pathlib import Path

SECRET = "sk-TEST-SECRET-DO-NOT-SHIP"

REPO_ROOT = Path(__file__).resolve().parents[1]

DETERMINISTIC_AGENT = '''"""Deterministic smoke agent: run(task, df, llm) -> JSON str."""
import json


def run(task, df, llm):
    total = float(df["amount"].sum()) if "amount" in df.columns else 0
    return json.dumps({"answer": round(total, 2)})
'''

DETERMINISTIC_REFERENCE = '''"""Deterministic reference gold: reference(df) -> JSON str."""
import pandas as pd


def reference(df):
    total = float(df["amount"].sum()) if "amount" in df.columns else 0
    return '{"answer": ' + str(round(total, 2)) + '}'
'''


def make_docx_bytes(text: str = "实验报告") -> bytes:
    """最小合法 docx（zip 容器 + word/document.xml；Validator 只做打开性检查）。"""

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org'
            '/package/2006/content-types"><Default Extension="xml" '
            'ContentType="application/xml"/></Types>',
        )
        bundle.writestr("word/document.xml", f'<w:document><w:body><w:p>{text}</w:p></w:body></w:document>')
    return buf.getvalue()


def make_workspace(root: Path, *, student_id: str = "20260001", phase: str = "P1") -> Path:
    """完整课程 workspace（六件套；P2 额外含 assignment.json + review-comments.json
    + 实验报告.docx，v10.1.14 两阶段协议；旧 reviews/ report/ 已删除）。"""
    root.mkdir(parents=True, exist_ok=True)
    (root / "task.json").write_text(json.dumps({
        "task_id": "T_TEST_" + student_id + ("_V2" if phase == "P2" else "_V1"),
        "description": "统计 amount 总和",
        "task_statement": "data.csv 含 region、amount 两列，输出 amount 总和（JSON）。",
        "student_id": student_id,
        "hypothesized_failure_mode": "FM06_AGGREGATION",
    }, ensure_ascii=False), encoding="utf-8")
    (root / "data.csv").write_text("region,amount\nA,1.5\nB,2.5\n", encoding="utf-8")
    (root / "agent.py").write_text(DETERMINISTIC_AGENT, encoding="utf-8")
    (root / "reference.py").write_text(DETERMINISTIC_REFERENCE, encoding="utf-8")
    (root / ".env").write_text(
        "LLM_BASE_URL=http://localhost:9\nLLM_API_KEY=sk-TEST-SECRET-DO-NOT-SHIP\n"
        "LLM_MODEL=dummy-model\n",
        encoding="utf-8")
    (root / ".env.example").write_text("LLM_BASE_URL=\nLLM_API_KEY=\nLLM_MODEL=\n", encoding="utf-8")
    if phase == "P2":
        assigned = [f"AT{28 + 7 * i:03d}" for i in range(2)]
        (root / "assignment.json").write_text(json.dumps({
            "assignment_id": "AR000001_R000023", "phase": "phase2",
            "anonymous_tasks": assigned,
        }, ensure_ascii=False), encoding="utf-8")
        (root / "review-comments.json").write_text(json.dumps({
            at: {"task_id": at, "score": 4,
                 "comments": f"该任务考察数据清洗边界，Agent 在 {at} 上需要改进。"}
            for at in assigned
        }, ensure_ascii=False), encoding="utf-8")
        (root / "实验报告.docx").write_bytes(make_docx_bytes())
    return root


def data_csv_bytes() -> bytes:
    return b"region,amount\nA,1.5\nB,2.5\n"


def make_exposure_package(
    path: Path,
    *,
    count: int = 10,
    leak_keys: list[str] | None = None,
    include_reference: bool = False,
    include_env: bool = False,
    layout: str = "flat",
    extra_task_fields: dict | None = None,
) -> Path:
    """构造 TA 分发的 Exposure 包（默认恰好 10 个干净匿名 Task）。

    layout="flat"  -> tasks/ATxxx/task.json + tasks/ATxxx/data/（legacy）
    layout="nested"-> tasks/ATxxx/task/task.json + tasks/ATxxx/task/data/（canonical 正式）
    """
    root = path.with_suffix("").name
    tmp = path.parent / ("_pkg_" + root)
    tasks = tmp / "tasks"
    tasks.mkdir(parents=True, exist_ok=True)
    ids = [f"AT{i:03d}" for i in range(1, count + 1)]
    for at in ids:
        d = tasks / at
        body = d / "task" if layout == "nested" else d
        body.mkdir(parents=True, exist_ok=True)
        (body / "data").mkdir(parents=True, exist_ok=True)
        task = {
            "task_id": at,
            "description": f"anonymous task {at}",
            "task_statement": "data.csv 含 region、amount 两列，输出 amount 总和（JSON）。",
        }
        if extra_task_fields:
            task.update(extra_task_fields)
        if leak_keys:
            for key in leak_keys:
                task[key] = "LEAK"
        (body / "task.json").write_text(json.dumps(task, ensure_ascii=False), encoding="utf-8")
        (body / "data" / "data.csv").write_bytes(data_csv_bytes())
    if include_reference:
        ref_at = tasks / ids[0] / ("task" if layout == "nested" else "")
        (ref_at / "reference.py").write_text("def compute(df):\n    return 1\n",
                                             encoding="utf-8")
    if include_env:
        (tmp / ".env").write_text("LLM_API_KEY=sk-LEAK\n", encoding="utf-8")
    (tmp / "assignment.json").write_text(
        json.dumps({"assignment_id": "AR000001_R000023", "phase": "phase2",
                    "anonymous_tasks": ids}, ensure_ascii=False), encoding="utf-8")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as bundle:
        for p in sorted(tmp.rglob("*")):
            if p.is_file():
                arc = p.relative_to(tmp).as_posix()
                info = zipfile.ZipInfo(arc, date_time=(2026, 1, 1, 0, 0, 0))
                bundle.writestr(info, p.read_bytes())
    return path


def cli(args: list[str], *, cwd: Path | None = None) -> "tuple[int, str]":
    """以子进程运行 datac 主入口（等价 `datac ...`；不依赖已安装 console script）。"""
    import subprocess
    import sys

    env = dict(os.environ)
    env.setdefault("CSA_ROOT", str(REPO_ROOT))
    proc = subprocess.run(
        [sys.executable, "-m", "codesetarena.main", *args],
        cwd=str(cwd or REPO_ROOT), env=env, capture_output=True, text=True, timeout=600,
    )
    return proc.returncode, proc.stdout + proc.stderr


def zip_member_names(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as bundle:
        return bundle.namelist()


def zip_bytes(path: Path) -> bytes:
    return path.read_bytes()
