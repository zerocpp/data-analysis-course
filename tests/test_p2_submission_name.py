"""v10.1.14 §18/§44/§45：P2 build = Task v2 + Agent v2 + 实验报告 +
review-comments.json -> BigData_P2_{sid}.zip。"""
from __future__ import annotations

import zipfile

from bigdata_v10110_helpers import make_workspace
from codesetarena.submission.local import build_submission
from codesetarena.submission.archive import validate_submission_archive


def test_p2_default_name_and_content(tmp_path):
    ws = make_workspace(tmp_path / "ws", student_id="20260001", phase="P2")
    record = build_submission(ws, phase="phase2", student_id="20260001")
    assert record["exit_code"] == 0, record
    assert record["package_path"].endswith("BigData_P2_20260001.zip")
    with zipfile.ZipFile(record["package_path"]) as bundle:
        names = bundle.namelist()
    assert "实验报告.docx" in names
    assert "review-comments.json" in names
    assert "assignment.json" in names
    assert "agent/agent.py" in names and "task/task.json" in names
    assert not any(name.startswith("reviews/") for name in names)
    assert not any(name.startswith("report/") for name in names)
    assert "phase2_metadata.json" not in names


def test_p2_validate_standalone(tmp_path):
    """§58：TA/学生都不需要原 workspace 即可独立验证 P2 包。"""
    ws = make_workspace(tmp_path / "ws", student_id="20260004", phase="P2")
    record = build_submission(ws, phase="phase2", student_id="20260004")
    result = validate_submission_archive(record["package_path"], phase="phase2")
    assert result["status"] == "PASS", result
    assert result["submission_phase"] == "phase2"
    assert result["student_id"] == "20260004"


def test_p2_cli_end_to_end(tmp_path):
    from bigdata_v10110_helpers import cli

    ws = make_workspace(tmp_path / "ws", student_id="20260005", phase="P2")
    code, out = cli(["submission", "build", "--input", str(ws),
                     "--student-id", "20260005", "--phase", "P2"], cwd=tmp_path)
    assert code == 0, out
    assert "BigData_P2_20260005.zip" in out


def test_p2_phase_autodetected(tmp_path):
    """v10.1.14：学生不手输 --phase，阶段从 workspace metadata 自动识别。"""
    ws = make_workspace(tmp_path / "ws", student_id="20260006", phase="P2")
    record = build_submission(ws, phase=None, student_id="20260006")
    assert record["exit_code"] == 0, record
    assert record["package_path"].endswith("BigData_P2_20260006.zip")
