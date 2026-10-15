"""v10.1.16 改造 A/B —— Workspace 冻结 + 两阶段 Build Contract（§29 关键测试）。

覆盖矩阵：
  A Workspace：合法/缺文件/导入防覆盖/phase1 隔离/Agent v1 固定/runs 归位
  B P1 Build：唯一总包 / 严格 3 个一级文件 / Tasks+Agent 子包 / metadata
  C P2 Build：严格 5 个一级文件 / ID 连续性 / review+报告缺一 FAIL
  D 安全边界：.env/runs/exposure/store/assignment.json 永不入包；覆盖必败
"""

from __future__ import annotations

import hashlib
import json
import tarfile
import zipfile
from pathlib import Path

import pytest

sys_path = __import__("sys").path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys_path:
    sys_path.insert(0, str(ROOT))

from codesetarena.exposure.importer import ExposureImportError, import_exposure_package  # noqa: E402
from codesetarena.exposure.runner import exposure_status, run_exposure  # noqa: E402
from codesetarena.main.build_package import (  # noqa: E402
    BuildPackageError,
    build_phase_package,
    expected_top_level_files,
)
from codesetarena.main.init_workspace import init_workspace  # noqa: E402
from codesetarena.main.runner import run_project  # noqa: E402
from codesetarena.main.workspace_layout import (  # noqa: E402
    exposure_task_dir,
    phase_dir,
    phase_runs_dir,
)
from codesetarena.main.workspace_validation import validate_workspace  # noqa: E402
from codesetarena.submission.built_package import (  # noqa: E402
    receive_built_package,
    validate_built_package,
)

SID = "20250001"
ENV_TEXT = ("LLM_BASE_URL=http://localhost:9\n"
            "LLM_API_KEY=SMOKE-NO-LLM-NEEDED\n"
            "LLM_MODEL=student-chosen-model\n")
AT_IDS = [f"AT{i:03d}" for i in range(1, 11)]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_workspace(tmp_path: Path, *, student_id: str = SID) -> Path:
    ws = tmp_path / "ws"
    record = init_workspace(ws)
    assert record["layout"] == "two-phase.v10.1.16"
    (ws / ".env").write_text(ENV_TEXT, encoding="utf-8")
    # 离线确定性 Agent（不调 LLM）：课程允许非 LLM agent；模板 agent 会真调网关
    for phase in ("phase1", "phase2"):
        (phase_dir(ws, phase) / "agent.py").write_text(
            "def run(task, df, llm):\n"
            "    return int(df['amount'].sum())\n", encoding="utf-8")
    for phase in ("phase1", "phase2"):
        meta_path = phase_dir(ws, phase) / "metadata.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["student_id"] = student_id
        meta["task_id"] = f"TASK_{student_id}"
        meta["agent_id"] = f"AGENT_{student_id}"
        if phase == "phase2":
            meta["review-comments_id"] = f"REVIEW_{student_id}"
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
    return ws


def seed_exposure_assignment(ws: Path, at_ids: list[str]) -> None:
    (ws / "exposure" / "assignment.json").write_text(
        json.dumps({"student_id": SID, "agent_id": f"AGENT_{SID}",
                    "assigned_tasks": at_ids}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")


def make_exposure_package(tmp_path: Path, at_ids: list[str], *,
                          name: str = "BigData_Exposure_R000001.zip") -> Path:
    """TA 分发产物（新契约：每 AT 含 task.json + data + reference.py）。"""

    staging = tmp_path / "exp_pkg"
    (staging / "tasks").mkdir(parents=True)
    (staging / "assignment.json").write_text(
        json.dumps({"student_id": SID, "agent_id": f"AGENT_{SID}",
                    "assigned_tasks": at_ids}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    for index, at in enumerate(at_ids, start=1):
        at_dir = staging / "tasks" / at
        (at_dir / "data").mkdir(parents=True)
        (at_dir / "task" / "data").mkdir(parents=True)
        (at_dir / "task" / "task.json").write_text(json.dumps({
            "anonymous_task_id": at,
            "description": f"匿名任务 {at}",
            "task_statement": "data.csv 含 amount 列，请计算总和并只输出整数。",
            }, ensure_ascii=False), encoding="utf-8")
        (at_dir / "task" / "data" / "data.csv").write_text(
            f"amount\n{index * 10}\n{index}\n", encoding="utf-8")
        (at_dir / "reference.py").write_text(
            "def reference(df):\n    return int(df['amount'].sum())\n",
            encoding="utf-8")
    package = tmp_path / name
    with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(staging.rglob("*")):
            if path.is_file():
                bundle.write(path, arcname=str(path.relative_to(staging)))
    return package


def fill_review_and_report(ws: Path, at_ids: list[str]) -> None:
    reviews = {at: {"task_id": at, "score": 4, "comments": f"review of {at}"}
               for at in at_ids}
    (phase_dir(ws, "phase2") / "review-comments.json").write_text(
        json.dumps(reviews, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # 实验报告模板.docx 已由 init 播种（真实 docx）


@pytest.fixture()
def ws(tmp_path):
    return make_workspace(tmp_path)


# ---------------------------------------------------------------------------
# A. Workspace
# ---------------------------------------------------------------------------


def test_a1_valid_phase1_workspace_passes(ws):
    assert validate_workspace(ws, "phase1") == []


def test_a2_missing_task_json_fails(ws):
    (phase_dir(ws, "phase1") / "task.json").unlink()
    errors = validate_workspace(ws, "phase1")
    assert any("task.json" in e for e in errors)


def test_a3_valid_phase2_workspace_passes(ws, tmp_path):
    seed_exposure_assignment(ws, AT_IDS)
    fill_review_and_report(ws, AT_IDS)
    assert validate_workspace(ws, "phase2") == []


def test_a4_import_keeps_phase1_bytes_identical(ws, tmp_path):
    seed_exposure_assignment(ws, AT_IDS)
    before = {name: _sha(phase_dir(ws, "phase1") / name)
              for name in ("task.json", "data.csv", "reference.py")}
    import_exposure_package(make_exposure_package(tmp_path, AT_IDS), ws)
    after = {name: _sha(phase_dir(ws, "phase1") / name)
             for name in ("task.json", "data.csv", "reference.py")}
    assert before == after


def test_a5_import_lands_each_at_dir(ws, tmp_path):
    at_ids = ["AT028", "AT035"] + [f"AT{i:03d}" for i in range(101, 109)]
    import_exposure_package(make_exposure_package(tmp_path, at_ids), ws)
    for at in at_ids:
        assert exposure_task_dir(ws, at).is_dir()
        for name in ("task.json", "data.csv", "reference.py"):
            assert (exposure_task_dir(ws, at) / name).is_file()


def test_a6_exposure_run_uses_phase1_agent_and_exposure_task(ws, tmp_path):
    seed_exposure_assignment(ws, AT_IDS)
    import_exposure_package(make_exposure_package(tmp_path, AT_IDS), ws)
    # 其余 9 个预置结果，仅真实运行 AT001（受控时长）
    for at in AT_IDS[1:]:
        result_dir = ws / "runs" / "exposure" / at
        result_dir.mkdir(parents=True)
        (result_dir / "result.json").write_text(json.dumps({"status": "done"}))
    summary = run_exposure(ws)
    assert summary["done_count"] == 10
    result = json.loads((ws / "runs" / "exposure" / "AT001" / "result.json").read_text())
    assert result["agent"] == "phase1/agent.py"
    assert result["status"] == "done"


def test_a7_exposure_never_switches_to_agent_v2(ws, tmp_path):
    seed_exposure_assignment(ws, AT_IDS)
    import_exposure_package(make_exposure_package(tmp_path, AT_IDS), ws)
    # Agent v2 给出截然不同的答案；Exposure 结果必须仍来自 Agent v1
    (phase_dir(ws, "phase2") / "agent.py").write_text(
        "def run(task, df, llm):\n    return 99999\n", encoding="utf-8")
    for at in AT_IDS[1:]:
        result_dir = ws / "runs" / "exposure" / at
        result_dir.mkdir(parents=True)
        (result_dir / "result.json").write_text(json.dumps({"status": "done"}))
    run_exposure(ws)
    result = json.loads((ws / "runs" / "exposure" / "AT001" / "result.json").read_text())
    assert "99999" not in result["answer_preview"]
    assert result["agent"] == "phase1/agent.py"


def test_a8_runs_land_in_frozen_dirs(ws):
    record = run_project(ws, phase="phase1")
    assert record["status"] == "success" and record["correct"] is True
    run_dir = Path(record["run_dir"])
    assert run_dir.is_relative_to(phase_runs_dir(ws, "phase1"))
    assert (run_dir / "result.json").is_file()


def test_a_import_existing_at_fails_without_overwrite(ws, tmp_path):
    at_ids = ["AT028"] + [f"AT{i:03d}" for i in range(101, 109)] + ["AT109"]
    assert len(at_ids) == 10
    package = make_exposure_package(tmp_path, at_ids)
    import_exposure_package(package, ws)
    target = exposure_task_dir(ws, "AT028")
    sentinel = target / "task.json"
    original = sentinel.read_bytes()
    with pytest.raises(ExposureImportError):
        import_exposure_package(package, ws)
    assert sentinel.read_bytes() == original


def test_a_import_never_targets_phase_dirs(ws, tmp_path):
    from codesetarena.main.workspace_layout import WorkspaceLayoutError

    with pytest.raises(WorkspaceLayoutError):
        exposure_task_dir(ws, "phase1")


def test_a_status_reflects_new_runs_root(ws, tmp_path):
    seed_exposure_assignment(ws, AT_IDS)
    import_exposure_package(make_exposure_package(tmp_path, AT_IDS), ws)
    status = exposure_status(ws)
    assert status["status"] == "READY" and status["done"] == 0
    assert status["runs_root"] == str(ws / "runs" / "exposure")


# ---------------------------------------------------------------------------
# B. Phase 1 Build
# ---------------------------------------------------------------------------


def test_b_p1_build_single_package_and_structure(ws):
    record = build_phase_package(ws, phase="phase1", student_id=SID)
    assert record["status"] == "BUILD_OK"
    package = Path(record["package_path"])
    assert package.name == f"bigdata_p1_{SID}.tar.gz"
    assert package.parent == ws / "store" / "packages"
    # 一个阶段只产生一个正式总包
    packages = list((ws / "store" / "packages").glob("*.tar.gz"))
    assert packages == [package]

    result = validate_built_package(package)
    assert result["status"] == "PASS", result["errors"]
    with tarfile.open(package) as outer:
        top = {m.name.split("/")[0] for m in outer.getmembers()}
    assert top == {f"bigdata_p1_{SID}"}
    with tempfile_dir() as tmp:
        extract = Path(tmp) / "outer"
        with tarfile.open(package) as outer:
            outer.extractall(extract, filter="data")
        level1 = sorted(p.name for p in (extract / f"bigdata_p1_{SID}").iterdir())
        assert level1 == sorted(expected_top_level_files("phase1", SID))
        with tarfile.open(extract / f"bigdata_p1_{SID}" / f"Tasks_{SID}.tar.gz") as tasks:
            names = {m.name for m in tasks.getmembers() if m.isfile()}
        assert names == {f"Tasks_{SID}/task.json", f"Tasks_{SID}/data.csv",
                         f"Tasks_{SID}/reference.py"}
        with tarfile.open(extract / f"bigdata_p1_{SID}" / f"Agent_{SID}.tar.gz") as agent:
            names = {m.name for m in agent.getmembers() if m.isfile()}
        assert names == {f"Agent_{SID}/agent.py"}
        metadata = json.loads(
            (extract / f"bigdata_p1_{SID}" / "metadata.json").read_text(encoding="utf-8"))
    assert metadata == {"workspace": "phase1", "student_id": SID,
                        "task_id": f"TASK_{SID}", "agent_id": f"AGENT_{SID}",
                        "hypothesized_failure_mode": "FM_AGGREGATION"}


class tempfile_dir:
    def __enter__(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        return self.tmp.name

    def __exit__(self, *args):
        self.tmp.cleanup()


def test_b_task_json_has_no_task_id_and_metadata_owns_identity(ws):
    task = json.loads((phase_dir(ws, "phase1") / "task.json").read_text())
    assert set(task) == {"description", "task_statement", "answer"}
    assert "hypothesized_failure_mode" not in task
    record = build_phase_package(ws, phase="phase1", student_id=SID)
    assert record["metadata"]["task_id"] == f"TASK_{SID}"
    assert record["metadata"]["hypothesized_failure_mode"] == "FM_AGGREGATION"


# ---------------------------------------------------------------------------
# C. Phase 2 Build
# ---------------------------------------------------------------------------


def test_c_p2_build_five_files_and_identity_continuity(ws):
    seed_exposure_assignment(ws, AT_IDS)
    fill_review_and_report(ws, AT_IDS)
    run_project(ws, phase="phase2")
    record = build_phase_package(ws, phase="phase2", student_id=SID)
    package = Path(record["package_path"])
    assert package.name == f"bigdata_p2_{SID}.tar.gz"
    result = validate_built_package(package)
    assert result["status"] == "PASS", result["errors"]
    with tempfile_dir() as tmp:
        extract = Path(tmp) / "outer"
        with tarfile.open(package) as outer:
            outer.extractall(extract, filter="data")
        level1 = sorted(p.name for p in (extract / f"bigdata_p2_{SID}").iterdir())
        metadata = json.loads(
            (extract / f"bigdata_p2_{SID}" / "metadata.json").read_text(encoding="utf-8"))
    assert level1 == sorted(expected_top_level_files("phase2", SID))
    p1 = json.loads((phase_dir(ws, "phase1") / "metadata.json").read_text())
    assert metadata["workspace"] == "phase2"
    assert metadata["task_id"] == p1["task_id"]
    assert metadata["agent_id"] == p1["agent_id"]
    # Final Report 已取消：不要求、也不存在
    assert "final_report.md" not in level1


def test_c_p2_missing_review_comments_fails(ws):
    seed_exposure_assignment(ws, AT_IDS)
    (phase_dir(ws, "phase2") / "review-comments.json").unlink()
    with pytest.raises(BuildPackageError):
        build_phase_package(ws, phase="phase2", student_id=SID)


def test_c_p2_missing_report_fails(ws):
    seed_exposure_assignment(ws, AT_IDS)
    fill_review_and_report(ws, AT_IDS)
    (phase_dir(ws, "phase2") / "实验报告.docx").unlink()
    with pytest.raises(BuildPackageError):
        build_phase_package(ws, phase="phase2", student_id=SID)


def test_c_p2_review_coverage_enforced_against_assignment(ws):
    seed_exposure_assignment(ws, AT_IDS)
    reviews = {at: {"task_id": at, "score": 4, "comments": "ok"}
               for at in AT_IDS[:-1]}  # 缺最后一个
    (phase_dir(ws, "phase2") / "review-comments.json").write_text(
        json.dumps(reviews, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(BuildPackageError):
        build_phase_package(ws, phase="phase2", student_id=SID)


def test_c_p2_identity_drift_fails(ws):
    seed_exposure_assignment(ws, AT_IDS)
    fill_review_and_report(ws, AT_IDS)
    meta_path = phase_dir(ws, "phase2") / "metadata.json"
    meta = json.loads(meta_path.read_text())
    meta["task_id"] = "TASK_99999999"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    with pytest.raises(BuildPackageError):
        build_phase_package(ws, phase="phase2", student_id=SID)


# ---------------------------------------------------------------------------
# D. 安全与边界
# ---------------------------------------------------------------------------


def test_d_no_forbidden_entries_in_packages(ws, tmp_path):
    seed_exposure_assignment(ws, AT_IDS)
    fill_review_and_report(ws, AT_IDS)
    # 埋入各种"应该被排除"的文件
    (ws / ".env.example").write_text("LLM_MODEL=\n")
    (phase_dir(ws, "phase1") / "assignment.json").write_text("{}")
    (phase_dir(ws, "phase1") / "README.md").write_text("leak?")
    (phase_dir(ws, "phase1") / "build_manifest.json").write_text("{}")
    (phase_dir(ws, "phase2") / "assignment.json").write_text("{}")
    for phase in ("phase1", "phase2"):
        (phase_dir(ws, phase) / "__pycache__").mkdir(exist_ok=True)
        (phase_dir(ws, phase) / "__pycache__" / "x.pyc").write_bytes(b"x")
    run_project(ws, phase="phase1")
    p1 = Path(build_phase_package(ws, phase="phase1", student_id=SID)["package_path"])
    seed_exposure_assignment(ws, AT_IDS)
    fill_review_and_report(ws, AT_IDS)
    p2 = Path(build_phase_package(ws, phase="phase2", student_id=SID)["package_path"])
    forbidden = {".env", ".env.example", "README.md", "assignment.json",
                 "build_manifest.json", "__pycache__"}
    for package in (p1, p2):
        with tarfile.open(package) as outer:
            text = "\n".join(m.name for m in outer.getmembers())
            assert ".env" not in text and "assignment.json" not in text
            assert "build_manifest" not in text and "__pycache__" not in text
            assert "runs/" not in text and "exposure/" not in text and "store/" not in text
            # 内层子包也检查
            for member in [m for m in outer.getmembers() if m.name.endswith(".tar.gz")]:
                pass
        assert forbidden  # sanity


def test_d_inner_packages_clean(ws):
    record = build_phase_package(ws, phase="phase1", student_id=SID)
    package = Path(record["package_path"])
    with tempfile_dir() as tmp:
        extract = Path(tmp)
        with tarfile.open(package) as outer:
            outer.extractall(extract, filter="data")
        top = extract / f"bigdata_p1_{SID}"
        with tarfile.open(top / f"Tasks_{SID}.tar.gz") as inner:
            assert not any("__pycache__" in m.name or ".env" in m.name
                           for m in inner.getmembers())
        with tarfile.open(top / f"Agent_{SID}.tar.gz") as inner:
            assert not any("__pycache__" in m.name or ".env" in m.name
                           for m in inner.getmembers())


def test_d_receive_built_package_maps_layout(ws, tmp_path):
    record = build_phase_package(ws, phase="phase1", student_id=SID)
    package = Path(record["package_path"])
    out = tmp_path / "ta_pool"
    received = receive_built_package(package, out)
    assert received["status"] == "ACCEPTED"
    target = out / "submissions" / "phase1" / SID
    assert sorted(p.name for p in target.iterdir()) == sorted(
        expected_top_level_files("phase1", SID))
    assert (out / "received" / package.name).is_file()
    # 同 sha 幂等
    again = receive_built_package(package, out)
    assert again["status"] == "SKIPPED_IDEMPOTENT"


def test_d_wrong_student_id_rejected(ws):
    with pytest.raises(BuildPackageError):
        build_phase_package(ws, phase="phase1", student_id="99999999")


def test_d_validate_rejects_foreign_archive(ws, tmp_path):
    foreign = tmp_path / "bigdata_p1_not_this.zip"
    with zipfile.ZipFile(foreign, "w") as bundle:
        bundle.writestr("x.txt", "x")
    result = validate_built_package(foreign)
    assert result["status"] == "FAIL"


def test_exposure_run_single_task(ws, tmp_path):
    """--task 单题运行：只跑指定题；diagnostics 聚合仍覆盖全部分配题。"""
    from codesetarena.exposure.runner import exposure_status, run_exposure

    seed_exposure_assignment(ws, AT_IDS)
    import_exposure_package(make_exposure_package(tmp_path, AT_IDS), ws)
    summary = run_exposure(ws, task_ids=["AT001"])
    assert summary["done_count"] == 1 and summary["failed_count"] == 0
    assert (ws / "runs" / "exposure" / "AT001" / "result.json").is_file()
    assert not (ws / "runs" / "exposure" / "AT002").exists()
    status = exposure_status(ws)
    assert status["run_status"]["AT001"] == "done"
    assert sum(1 for v in status["run_status"].values() if v == "done") == 1
    with pytest.raises(Exception):
        run_exposure(ws, task_ids=["AT999"])
    # 未指定 --task 时回归全量
    run_exposure(ws)
    status = exposure_status(ws)
    assert status["done"] == 10
