"""v10.1.17 增量契约 —— 实验报告命名/真实模板 + review 模板纪律 + 无 demo 匿名题。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


sys_path = __import__("sys").path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys_path:
    sys_path.insert(0, str(ROOT))

from codesetarena.main.build_package import build_phase_package  # noqa: E402
from codesetarena.main.init_workspace import init_workspace  # noqa: E402
from codesetarena.main.workspace_layout import (  # noqa: E402
    EXPERIMENT_REPORT_FILENAME,
    phase_dir,
)
from codesetarena.main.workspace_validation import validate_workspace  # noqa: E402

SID = "20250001"
AT_IDS = [f"AT{i:03d}" for i in range(1, 11)]


def make_ws(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    init_workspace(ws)
    (ws / ".env").write_text(
        "LLM_BASE_URL=http://x\nLLM_API_KEY=k\nLLM_MODEL=m\n", encoding="utf-8")
    for phase in ("phase1", "phase2"):
        meta = ws / phase / "metadata.json"
        data = json.loads(meta.read_text())
        data.update({"student_id": SID, "task_id": f"TASK_{SID}",
                     "agent_id": f"AGENT_{SID}",
                     "hypothesized_failure_mode": "FM_AGGREGATION"})
        if phase == "phase2":
            data["review-comments_id"] = f"REVIEW_{SID}"
        meta.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return ws


def seed_assignment(ws: Path, at_ids: list[str]) -> None:
    (ws / "exposure" / "assignment.json").write_text(json.dumps(
        {"student_id": SID, "agent_id": f"AGENT_{SID}", "assigned_tasks": at_ids},
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fill_reviews(ws: Path, at_ids: list[str]) -> None:
    reviews = {at: {"task_id": at, "score": 4, "comments": f"review {at}"}
               for at in at_ids}
    (phase_dir(ws, "phase2") / "review-comments.json").write_text(
        json.dumps(reviews, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_init_seeds_real_course_docx(tmp_path):
    """phase2/实验报告.docx 直接来自仓库 docs/实验报告.docx（字节一致）。"""
    ws = make_ws(tmp_path)
    report = phase_dir(ws, "phase2") / EXPERIMENT_REPORT_FILENAME
    assert report.name == "实验报告.docx"
    source = ROOT / "docs" / EXPERIMENT_REPORT_FILENAME
    assert source.is_file()
    assert hashlib.sha256(report.read_bytes()).hexdigest() == \
        hashlib.sha256(source.read_bytes()).hexdigest()


def test_init_review_comments_has_template_entry(tmp_path):
    """review-comments.json 模板条目强调“每道分配的题写一条”。"""
    ws = make_ws(tmp_path)
    data = json.loads(
        (phase_dir(ws, "phase2") / "review-comments.json").read_text(encoding="utf-8"))
    assert len(data) == 1
    key, entry = next(iter(data.items()))
    assert key == entry["task_id"] and "示例" in key
    assert entry["score"] == 0 and "每道" in entry["comments"]


def test_fresh_phase2_fails_until_review_replaced(tmp_path):
    """未替换模板条目 → 校验 FAIL（逼迫逐题补全）。"""
    ws = make_ws(tmp_path)
    seed_assignment(ws, AT_IDS)
    errors = validate_workspace(ws, "phase2")
    assert any("INVALID_REVIEW_SCORE" in e for e in errors)
    assert any("UNKNOWN_REVIEW_TASK" in e for e in errors)


def test_phase2_passes_with_real_docx_and_full_reviews(tmp_path):
    ws = make_ws(tmp_path)
    seed_assignment(ws, AT_IDS)
    fill_reviews(ws, AT_IDS)
    assert validate_workspace(ws, "phase2") == []


def test_p2_build_top_level_uses_report_docx(tmp_path):
    ws = make_ws(tmp_path)
    seed_assignment(ws, AT_IDS)
    fill_reviews(ws, AT_IDS)
    record = build_phase_package(ws, phase="phase2", student_id=SID)
    assert EXPERIMENT_REPORT_FILENAME in record["top_level_files"]
    assert not any("模板" in name for name in record["top_level_files"])


def test_init_creates_no_demo_anonymous_tasks(tmp_path):
    ws = make_ws(tmp_path)
    assert list((ws / "exposure").glob("AT*")) == []
    assignment = json.loads((ws / "exposure" / "assignment.json").read_text())
    assert assignment["assigned_tasks"] == []
