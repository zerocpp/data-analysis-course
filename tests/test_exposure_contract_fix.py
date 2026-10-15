"""BigData v10.1.12 Exposure Contract Minimal Fix 回归（§5 Test A–F）。

A: nested canonical layout import PASS
B: hypothesized_failure_mode（作者公开假设）保留并 PASS
C: expected_failure_mode（教师期望标签）→ EXPOSURE_LEAK
D: build_anonymous_task_view 剥离 expected/author_expected，保留 hypothesis
E: 分发中被人为注入 expected_failure_mode → assignment verify 绝不 READY
F: canonical → prepare → verify READY → export → exposure import（10/10, done=0）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bigdata_v10110_helpers import (  # noqa: E402
    make_exposure_package,
    make_workspace,
)

REPO = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- static ----

def test_forbidden_keys_subset_of_strip_fields():
    """共享契约：Exposure FORBIDDEN_TASK_KEYS ⊆ Anonymous TASK_VIEW_STRIP_FIELDS。"""
    from codesetarena.exposure.contract import FORBIDDEN_TASK_KEYS
    from codesetarena.submission.anonymization import TASK_VIEW_STRIP_FIELDS

    assert set(FORBIDDEN_TASK_KEYS) <= set(TASK_VIEW_STRIP_FIELDS)


# ------------------------------------------------------------ Test A ----

def test_nested_canonical_layout_imports(tmp_path):
    ws = make_workspace(tmp_path / "ws")
    package = make_exposure_package(tmp_path / "exp_nested.zip", layout="nested")
    from codesetarena.exposure.importer import import_exposure_package

    state = import_exposure_package(package, ws)
    assert len(state["tasks"]) == 10
    at1 = ws / "exposure" / "tasks" / "AT001"
    assert (at1 / "task" / "task.json").is_file()
    assert (at1 / "task" / "data" / "data.csv").is_file()


def test_legacy_flat_layout_still_imports(tmp_path):
    ws = make_workspace(tmp_path / "ws")
    package = make_exposure_package(tmp_path / "exp_flat.zip", layout="flat")
    from codesetarena.exposure.importer import import_exposure_package

    state = import_exposure_package(package, ws)
    assert len(state["tasks"]) == 10


# ------------------------------------------------------------ Test B ----

def test_hypothesized_failure_mode_is_not_a_leak(tmp_path):
    ws = make_workspace(tmp_path / "ws")
    package = make_exposure_package(
        tmp_path / "exp.zip", layout="nested",
        extra_task_fields={"hypothesized_failure_mode": "FM05_JOIN"},
    )
    from codesetarena.exposure.importer import import_exposure_package

    state = import_exposure_package(package, ws)
    view = json.loads((ws / "exposure" / "tasks" / "AT001" / "task" / "task.json")
                      .read_text(encoding="utf-8"))
    assert view["hypothesized_failure_mode"] == "FM05_JOIN"
    assert len(state["tasks"]) == 10


# ------------------------------------------------------------ Test C ----

def test_expected_failure_mode_is_rejected(tmp_path):
    ws = make_workspace(tmp_path / "ws")
    package = make_exposure_package(
        tmp_path / "exp.zip", layout="nested",
        extra_task_fields={"expected_failure_mode": "FM13"},
    )
    from codesetarena.exposure.contract import EXPOSURE_LEAK
    from codesetarena.exposure.importer import ExposureImportError, import_exposure_package

    with pytest.raises(ExposureImportError) as excinfo:
        import_exposure_package(package, ws)
    assert "EXPOSURE_LEAK" in str(excinfo.value)


# ------------------------------------------------------------ Test D ----

def test_anonymizer_strips_expected_keeps_hypothesis(tmp_path):
    from codesetarena.submission.anonymization import build_anonymous_task_view

    canonical = tmp_path / "T_1"
    (canonical / "task" / "data").mkdir(parents=True)
    (canonical / "task" / "task.json").write_text(json.dumps({
        "task_id": "T_1",
        "student_id": "272401",
        "hypothesized_failure_mode": "FM05_JOIN",
        "expected_failure_mode": "FM13",
        "author_expected": "FM13",
        "task_statement": "x",
    }, ensure_ascii=False), encoding="utf-8")
    (canonical / "task" / "data" / "data.csv").write_text("a,b\n1,2\n", encoding="utf-8")

    out = tmp_path / "view"
    build_anonymous_task_view(canonical, "AT001", out)
    view = json.loads((out / "task" / "task.json").read_text(encoding="utf-8"))
    assert view["hypothesized_failure_mode"] == "FM05_JOIN"
    assert "expected_failure_mode" not in view
    assert "author_expected" not in view
    assert "student_id" not in view


# ------------------------------------------------------------ Test E ----

def _distributed_repo(tmp_path: Path, seed: int = 7) -> "tuple[Path, str]":
    from codesetarena.assignment import run as run_mod
    from test_assignment_pipeline import build_synthetic_repo

    repo, _ = build_synthetic_repo(tmp_path, 20)
    record = run_mod.build_assignment_run(repo, "phase1", seed=seed)
    assert record["status"] == "VERIFIED", record
    done = run_mod.distribute_assignment_run(repo, record["assignment_run_id"])
    assert done["distribution_status"] == "READY", done.get("errors")
    return repo, record["assignment_run_id"]


def test_assignment_verify_gate_blocks_expected_failure_mode(tmp_path):
    """人为注入 expected_failure_mode → verify 重放绝不 READY，export 被阻止。"""
    from codesetarena.assignment import distribution as dist_mod
    from codesetarena.assignment.cli import _verify_existing

    repo, run_id = _distributed_repo(tmp_path / "case_e", seed=11)

    dist_json = next(iter((repo / "distributions" / run_id / "reviewers").rglob("task.json")))
    view = json.loads(dist_json.read_text(encoding="utf-8"))
    view["expected_failure_mode"] = "FM13"
    dist_json.write_text(json.dumps(view, ensure_ascii=False), encoding="utf-8")

    record = _verify_existing(repo, run_id)
    assert record["status"] != "READY"
    assert record["status"] == "DISTRIBUTION_FAILED"
    assert any("expected_failure_mode" in h["keys"] for h in record["exposure_contract_leaks"])

    with pytest.raises(Exception) as excinfo:
        dist_mod.export_reviewer_archives(repo, run_id)
    assert "EXPOSURE_EXPORT_NOT_READY" in str(excinfo.value)


# ------------------------------------------------------------ Test F ----

def test_e2e_repo_prepare_verify_export_import(tmp_path):
    """canonical → prepare(distribute) → verify READY → export → exposure import（10/10, done=0）。"""
    from codesetarena.assignment import distribution as dist_mod
    from codesetarena.assignment import run as run_mod
    from codesetarena.assignment.cli import _verify_existing
    from codesetarena.exposure.importer import import_exposure_package
    from test_assignment_pipeline import build_synthetic_repo

    repo, _ = build_synthetic_repo(tmp_path / "case_f", 20)
    record = run_mod.build_assignment_run(repo, "phase1", seed=23)
    assert record["status"] == "VERIFIED", record
    done = run_mod.distribute_assignment_run(repo, record["assignment_run_id"])
    assert done["distribution_status"] == "READY", done.get("errors")
    run_id = record["assignment_run_id"]

    replay = _verify_existing(repo, run_id)
    assert replay["status"] == "READY", replay
    assert replay["exposure_contract_leaks"] == []

    exported = dist_mod.export_reviewer_archives(repo, run_id)
    assert len(exported) == 20

    ws = make_workspace(tmp_path / "ws")
    state = import_exposure_package(Path(exported[0]), ws)
    assert len(state["tasks"]) == 10
    status_dir = ws / "exposure" / "tasks"
    assert len(list(status_dir.iterdir())) == 10
    assert not (ws / "exposure" / "diagnostics.json").exists()  # done=0（尚未 run）
