"""v10.1.10 §18/§44：P1 build 生成 BigData_P1_{student_id}.zip（无 timestamp）。"""
from __future__ import annotations

from bigdata_v10110_helpers import make_workspace, zip_member_names
from codesetarena.submission.local import build_submission


def test_p1_default_name(tmp_path):
    ws = make_workspace(tmp_path / "ws", student_id="20260001", phase="P1")
    record = build_submission(ws, phase="phase1", student_id="20260001")
    assert record["exit_code"] == 0, record
    package = record["package_path"]
    assert package.endswith("BigData_P1_20260001.zip")
    assert "timestamp" not in package


def test_p1_build_invokes_single_package_validator_and_passes(tmp_path):
    from codesetarena.submission.archive import validate_submission_archive
    from pathlib import Path

    ws = make_workspace(tmp_path / "ws", student_id="20260002", phase="P1")
    record = build_submission(ws, phase="phase1", student_id="20260002")
    package = record["package_path"]
    result = validate_submission_archive(package, phase="phase1")
    assert result["status"] == "PASS", result
    names = zip_member_names(package and __import__("pathlib").Path(package))
    assert "manifest.json" in names
    assert "task/task.json" in names
    assert "agent/agent.py" in names


def test_p1_cli_end_to_end(tmp_path):
    from bigdata_v10110_helpers import cli

    ws = make_workspace(tmp_path / "ws", student_id="20260003", phase="P1")
    code, out = cli(["submission", "build", "--input", str(ws),
                     "--student-id", "20260003", "--phase", "P1"], cwd=tmp_path)
    assert code == 0, out
    assert "BigData_P1_20260003.zip" in out
