"""BigData v10.1.12 Submission Resubmission Minimal Fix 回归（§10 Test A–G）。

raw 单文件 active 槽位语义：REJECTED 不占槽、DUPLICATE_ARCHIVE 仅同 SHA、
--supersede 显式替换、FROZEN 冻结边界、append-only attempts 审计。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bigdata_v10110_helpers import make_workspace  # noqa: E402

from codesetarena.submission.local import build_submission  # noqa: E402
from codesetarena.submission.receiver import receive_submission  # noqa: E402

ROSTER_IDS = ["20260201", "20260202", "272401"]


def _roster(tmp_path: Path) -> Path:
    p = tmp_path / "roster.csv"
    p.write_text("student_id\n" + "\n".join(ROSTER_IDS) + "\n", encoding="utf-8")
    return p


def _package(tmp_path: Path, sid: str) -> Path:
    ws = make_workspace(tmp_path / f"ws_{sid}", student_id=sid)
    out = tmp_path / f"pkg_{sid}" / f"BigData_P1_{sid}.zip"  # 输出名必须符合命名协议
    record = build_submission(ws, phase="phase1", student_id=sid, output_path=out)
    assert record["exit_code"] == 0, record
    return out


def _mismatched_package(tmp_path: Path) -> Path:
    """内层 student_id 与包名不一致（外层 20260201 / 内层 272401）。"""
    out = _package(tmp_path, "272401")
    target = tmp_path / "BigData_P1_20260201.zip"
    out.rename(target)
    return target


def _audit_lines(pool: Path) -> list[dict]:
    log = pool / "audit" / "submission_attempts.jsonl"
    if not log.is_file():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


# ------------------------------------------------------------ Test A ----

def test_rejected_package_does_not_occupy_active_slot(tmp_path):
    pool = tmp_path / "pool"
    bad = _mismatched_package(tmp_path)

    record = receive_submission(bad, pool, _roster(tmp_path))

    assert record["status"] == "rejected"
    assert record["error_code"] == "STUDENT_ID_MISMATCH"
    assert not (pool / "raw" / "BigData_P1_20260201.zip").exists()  # 槽位为空
    rejected_dir = pool / "history" / "rejected"
    saved = list(rejected_dir.iterdir())
    assert len(saved) == 1 and saved[0].name.startswith("BigData_P1_20260201__")
    lines = _audit_lines(pool)
    assert lines[-1]["status"] == "REJECTED"
    assert lines[-1]["error_code"] == "STUDENT_ID_MISMATCH"
    assert lines[-1]["sha256"]


# ------------------------------------------------------------ Test B ----

def test_corrected_same_filename_receives_active(tmp_path):
    pool = tmp_path / "pool"
    receive_submission(_mismatched_package(tmp_path), pool, _roster(tmp_path))

    good = _package(tmp_path, "20260201")  # 同名 BigData_P1_20260201.zip，不同 SHA
    record = receive_submission(good, pool, _roster(tmp_path))

    assert record["status"] == "accepted", record
    assert record["error_code"] != "DUPLICATE_ARCHIVE"
    assert (pool / "raw" / "BigData_P1_20260201.zip").is_file()
    lines = _audit_lines(pool)
    assert lines[-1]["status"] == "ACTIVE"
    assert lines[-1]["supersedes_sha256"] is None  # 此前槽位为空，非替换


# ------------------------------------------------------------ Test C ----

def test_identical_sha_is_duplicate_archive(tmp_path):
    pool = tmp_path / "pool"
    good = _package(tmp_path, "20260201")
    receive_submission(good, pool, _roster(tmp_path))

    record = receive_submission(good, pool, _roster(tmp_path))

    assert record["status"] == "rejected"
    assert record["error_code"] == "DUPLICATE_ARCHIVE"
    assert (pool / "raw" / "BigData_P1_20260201.zip").is_file()  # active 不变
    assert _audit_lines(pool)[-1]["status"] == "DUPLICATE_ARCHIVE"


# ------------------------------------------------------------ Test D ----

def test_active_exists_without_supersede_refuses(tmp_path):
    pool = tmp_path / "pool"
    active = _package(tmp_path, "20260201")
    receive_submission(active, pool, _roster(tmp_path))
    active_sha = active.read_bytes()

    # 同槽位名、内层同为 20260201、但数据不同（不同 SHA）的有效新包
    ws = make_workspace(tmp_path / "ws_v2", student_id="20260201")
    (ws / "data.csv").write_text("region,amount\nA,9.5\nB,0.5\n", encoding="utf-8")
    from codesetarena.submission.local import build_submission

    new_dir = tmp_path / "newpkg"
    new_dir.mkdir(exist_ok=True)
    newer = new_dir / "BigData_P1_20260201.zip"
    record = build_submission(ws, phase="phase1", student_id="20260201", output_path=newer)
    assert record["exit_code"] == 0
    assert newer.read_bytes() != active_sha  # 确为不同内容

    record = receive_submission(newer, pool, _roster(tmp_path))

    assert record["status"] == "rejected"
    assert record["error_code"] == "ACTIVE_SUBMISSION_EXISTS"
    assert (pool / "raw" / "BigData_P1_20260201.zip").read_bytes() == active_sha  # 旧 active 不变
    assert _audit_lines(pool)[-1]["status"] == "REFUSED_SUPERSEDE"


# ------------------------------------------------------------ Test E ----

def test_supersede_replaces_active(tmp_path):
    pool = tmp_path / "pool"
    active = _package(tmp_path, "20260201")
    receive_submission(active, pool, _roster(tmp_path))
    old_sha = active.read_bytes()
    old_name = active.name

    # 构造"通过校验"的同槽位新包：内层 20260201、数据不同（不同 SHA）
    ws = make_workspace(tmp_path / "ws_v2", student_id="20260201")
    (ws / "data.csv").write_text("region,amount\nA,9.5\nB,0.5\n", encoding="utf-8")
    from codesetarena.submission.local import build_submission

    new_pkg_dir = tmp_path / "newpkg"
    new_pkg_dir.mkdir(exist_ok=True)
    new_pkg = new_pkg_dir / "BigData_P1_20260201.zip"
    record = build_submission(ws, phase="phase1", student_id="20260201", output_path=new_pkg)
    assert record["exit_code"] == 0

    result = receive_submission(new_pkg, pool, _roster(tmp_path), supersede=True)

    assert result["status"] == "accepted", result
    slot = pool / "raw" / "BigData_P1_20260201.zip"
    assert slot.is_file() and slot.read_bytes() != old_sha
    superseded = list((pool / "history" / "superseded").iterdir())
    assert len(superseded) == 1 and superseded[0].name.startswith("BigData_P1_20260201__")
    lines = _audit_lines(pool)
    assert lines[-1]["status"] == "ACTIVE"
    assert lines[-1]["supersedes_sha256"]
    del old_name


# ------------------------------------------------------------ Test F ----

def test_supersede_with_failing_new_package_keeps_active(tmp_path):
    pool = tmp_path / "pool"
    active = _package(tmp_path, "20260201")
    receive_submission(active, pool, _roster(tmp_path))
    active_sha = active.read_bytes()

    bad = _mismatched_package(tmp_path)  # 同槽位名、内层身份错误 → FAIL
    record = receive_submission(bad, pool, _roster(tmp_path), supersede=True)

    assert record["status"] == "rejected"
    assert (pool / "raw" / "BigData_P1_20260201.zip").read_bytes() == active_sha  # 旧 active 完全不变
    assert len(list((pool / "history" / "superseded").iterdir())) == 0
    assert _audit_lines(pool)[-1]["status"] == "REJECTED"


# ------------------------------------------------------------ Test G ----

def test_frozen_pool_receives_are_refused(tmp_path):
    pool = tmp_path / "pool"
    good = _package(tmp_path, "20260201")
    receive_submission(good, pool, _roster(tmp_path))
    active_sha = (pool / "raw" / "BigData_P1_20260201.zip").read_bytes()
    (pool / "FROZEN").write_text("", encoding="utf-8")  # 冻结标记

    for kwargs in ({}, {"supersede": True}):
        newer = _package(tmp_path, "20260202")
        target = tmp_path / "BigData_P1_20260201_frozen.zip"
        newer.rename(target)
        record = receive_submission(target, pool, _roster(tmp_path), **kwargs)

        assert record["status"] == "rejected"
        assert record["error_code"] == "PHASE_FROZEN"

    assert (pool / "raw" / "BigData_P1_20260201.zip").read_bytes() == active_sha
