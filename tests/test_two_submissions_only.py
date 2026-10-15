"""v10.1.10 §17/§64：Two Submissions only —— 不存在 P3，不存在 Exposure Submission。"""
from __future__ import annotations

import pytest

from bigdata_v10110_helpers import make_workspace
from codesetarena.submission import contract as C
from codesetarena.submission.identity import IdentityError, parse_archive_name


def test_valid_phases_are_exactly_phase1_phase2():
    assert C.VALID_PHASES == ("phase1", "phase2")


def test_archive_pattern_rejects_p3():
    assert C.ARCHIVE_NAME_PATTERN.match("BigData_P1_20260001.zip")
    assert C.ARCHIVE_NAME_PATTERN.match("BigData_P2_20260001.zip")
    assert not C.ARCHIVE_NAME_PATTERN.match("BigData_P3_20260001.zip")


def test_parse_archive_name_maps_only_p1_p2():
    assert parse_archive_name("BigData_P1_20260001.zip")["phase"] == "phase1"
    assert parse_archive_name("BigData_P2_20260001.zip")["phase"] == "phase2"
    with pytest.raises(IdentityError):
        parse_archive_name("BigData_P3_20260001.zip")


def test_build_rejects_p3_phase(tmp_path):
    from codesetarena.submission.local import SubmissionBuildError, build_submission

    ws = make_workspace(tmp_path / "ws", student_id="20260001")
    with pytest.raises((SubmissionBuildError, ValueError)):
        build_submission(ws, phase="phase3", student_id="20260001")


def test_cli_rejects_p3(tmp_path):
    from bigdata_v10110_helpers import cli

    code, out = cli(["submission", "build", "--input", str(tmp_path),
                     "--student-id", "20260001", "--phase", "P3"])
    assert code != 0
    assert "P1 / P2" in out or "invalid" in out.lower() or "非法" in out
