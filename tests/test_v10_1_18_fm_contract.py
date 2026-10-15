"""v10.1.18 —— hypothesized_failure_mode 契约修复（§19 T1–T14 全覆盖）。

- task.json 只允许 {description, task_statement, answer}；出现 FM 即 FAIL；
- metadata.json 必须携带 canonical FM（无数字编号），缺失/数字/自定义均 FAIL；
- P1 Build：Tasks 内层 task.json 无 FM；外层 metadata.json 携带 FM；
- Exposure 导入：含 FM 的匿名包被拒；TA 端 canonical 仍保留 hypothesis；
- 全部 14 个 canonical FM 逐一 PASS；全部 legacy 数字形态逐一被拒。
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

sys_path = __import__("sys").path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys_path:
    sys_path.insert(0, str(ROOT))

from codesetarena.assignment.category import normalize_category_code  # noqa: E402
from codesetarena.exposure.importer import ExposureImportError  # noqa: E402
from codesetarena.exposure.importer import import_exposure_package  # noqa: E402
from codesetarena.main.build_package import build_phase_package  # noqa: E402
from codesetarena.main.workspace_contract import (  # noqa: E402
    INVALID_FAILURE_MODE_KEY,
    TASK_JSON_FORBIDDEN_HYPOTHESIZED_FAILURE_MODE,
)
from codesetarena.main.workspace_layout import phase_dir  # noqa: E402
from codesetarena.main.workspace_validation import validate_workspace  # noqa: E402
from codesetarena.submission.ingest import ingest_submissions  # noqa: E402
from codesetarena.submission.registry import SubmissionRegistry  # noqa: E402

SID = "20250001"
FMS = [
    "FM_REQUIREMENT", "FM_SCHEMA", "FM_DATA_TYPE", "FM_FILTER_SORT",
    "FM_JOIN", "FM_AGGREGATION", "FM_TIME_TIMEZONE", "FM_MISSING_VALUE",
    "FM_OUTLIER_BOUNDARY", "FM_DUPLICATE_RECORD_HANDLING",
    "FM_PLANNING_DECOMPOSITION", "FM_VERIFICATION",
    "FM_NUMERICAL_PRECISION_ROUNDING", "FM_PROMPT_INJECTION",
]
LEGACY_FORMS = [
    "FM01_REQUIREMENT", "FM_01_REQUIREMENT", "FM02_SCHEMA", "FM_02_SCHEMA",
    "FM03_DATA_TYPE", "FM_03_DATA_TYPE", "FM04_FILTER_SORT", "FM_04_FILTER_SORT",
    "FM05_JOIN", "FM_05_JOIN", "FM06_AGGREGATION", "FM_06_AGGREGATION",
    "FM07_TIME_TIMEZONE", "FM_07_TIME_TIMEZONE", "FM08_MISSING_VALUE",
    "FM_08_MISSING_VALUE", "FM09_OUTLIER_BOUNDARY", "FM_09_OUTLIER_BOUNDARY",
    "FM10_DUPLICATE_RECORD_HANDLING", "FM_10_DUPLICATE_RECORD_HANDLING",
    "FM11_PLANNING_DECOMPOSITION", "FM_11_PLANNING_DECOMPOSITION",
    "FM12_VERIFICATION", "FM_12_VERIFICATION",
    "FM13_NUMERICAL_PRECISION_ROUNDING", "FM_13_NUMERICAL_PRECISION_ROUNDING",
    "FM14_PROMPT_INJECTION", "FM_14_PROMPT_INJECTION",
]


def make_ws(tmp_path: Path) -> Path:
    from codesetarena.main.init_workspace import init_workspace

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


def set_metadata_fm(ws: Path, fm: str | None) -> None:
    meta_path = phase_dir(ws, "phase1") / "metadata.json"
    data = json.loads(meta_path.read_text())
    if fm is None:
        data.pop("hypothesized_failure_mode", None)
    else:
        data["hypothesized_failure_mode"] = fm
    meta_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def build_p1(ws: Path) -> Path:
    record = build_phase_package(ws, phase="phase1", student_id=SID)
    return Path(record["package_path"])


# ---------------------------------------------------------------------------
# T1–T7：workspace 层
# ---------------------------------------------------------------------------


def test_t1_three_field_task_with_canonical_metadata_passes(tmp_path):
    ws = make_ws(tmp_path)
    assert validate_workspace(ws, "phase1") == []


def test_t2_task_json_with_fm_fails(tmp_path):
    ws = make_ws(tmp_path)
    task_path = phase_dir(ws, "phase1") / "task.json"
    task = json.loads(task_path.read_text())
    task["hypothesized_failure_mode"] = "FM_AGGREGATION"
    task_path.write_text(json.dumps(task, ensure_ascii=False), encoding="utf-8")
    errors = validate_workspace(ws, "phase1")
    assert any(TASK_JSON_FORBIDDEN_HYPOTHESIZED_FAILURE_MODE in e for e in errors)
    assert any("move it to metadata.json" in e for e in errors)


def test_t3_metadata_canonical_fm_passes(tmp_path):
    ws = make_ws(tmp_path)
    set_metadata_fm(ws, "FM_AGGREGATION")
    assert validate_workspace(ws, "phase1") == []


@pytest.mark.parametrize("bad", ["FM_06_AGGREGATION", "FM06_AGGREGATION",
                                 "FM_CUSTOM", "FM_15_OUTPUT", "AGGREGATION", "FM_AGG"])
def test_t4_to_t6_metadata_invalid_fm_fails(tmp_path, bad):
    ws = make_ws(tmp_path)
    set_metadata_fm(ws, bad)
    errors = validate_workspace(ws, "phase1")
    assert any(INVALID_FAILURE_MODE_KEY in e for e in errors), (bad, errors)


def test_t7_metadata_missing_fm_fails(tmp_path):
    ws = make_ws(tmp_path)
    set_metadata_fm(ws, None)
    errors = validate_workspace(ws, "phase1")
    assert any("METADATA_MISSING_HYPOTHESIZED_FAILURE_MODE" in e for e in errors)


# ---------------------------------------------------------------------------
# T8–T9：P1 Build（内层无 FM；外层 metadata 有 FM）
# ---------------------------------------------------------------------------


def test_t8_p1_tasks_inner_has_no_fm(tmp_path):
    import tarfile

    ws = make_ws(tmp_path)
    package = build_p1(ws)
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(package) as outer:
            outer.extractall(tmp, filter="data")
        top = Path(tmp) / f"bigdata_p1_{SID}"
        with tarfile.open(top / f"Tasks_{SID}.tar.gz") as inner:
            inner.extractall(Path(tmp) / "tasks", filter="data")
        task_text = (Path(tmp) / "tasks" / f"Tasks_{SID}" / "task.json").read_text()
    assert "hypothesized_failure_mode" not in task_text


def test_t9_p1_outer_metadata_has_fm(tmp_path):
    import tarfile

    ws = make_ws(tmp_path)
    package = build_p1(ws)
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(package) as outer:
            outer.extractall(tmp, filter="data")
        metadata = json.loads(
            (Path(tmp) / f"bigdata_p1_{SID}" / "metadata.json").read_text())
    assert metadata["hypothesized_failure_mode"] == "FM_AGGREGATION"


# ---------------------------------------------------------------------------
# T10–T12：Exposure 拒绝 FM；TA 端保留；FM 可被下游读取
# ---------------------------------------------------------------------------


def _ingest_one_built_package(ws_with_pkg: Path, repo: Path) -> None:
    received = ws_with_pkg / "store" / "packages" / f"bigdata_p1_{SID}.tar.gz"
    ingest_submissions(repo, received.parent)


def test_t10_exposure_package_with_fm_rejected(tmp_path):
    """匿名包 task.json 含 hypothesized_failure_mode → 导入拒绝。"""
    import zipfile

    ws = make_ws(tmp_path / "student")
    package = tmp_path / "pkg" / "BigData_Exposure_R000001.zip"
    package.parent.mkdir(parents=True)
    at_ids = [f"AT{i:03d}" for i in range(1, 11)]
    staging = tmp_path / "pkg" / "stage"
    (staging / "tasks").mkdir(parents=True)
    (staging / "assignment.json").write_text(json.dumps({
        "student_id": SID, "agent_id": f"AGENT_{SID}",
        "assigned_tasks": at_ids}), encoding="utf-8")
    for at in at_ids:
        d = staging / "tasks" / at
        d.mkdir(parents=True)
        (d / "task.json").write_text(json.dumps({
            "anonymous_task_id": at, "description": "demo",
            "task_statement": "求和。",
            "hypothesized_failure_mode": "FM_AGGREGATION"}), encoding="utf-8")
        (d / "data.csv").write_text("amount\n10\n", encoding="utf-8")
        (d / "reference.py").write_text("def reference(df):\n    return 10\n",
                                        encoding="utf-8")
    with zipfile.ZipFile(package, "w") as bundle:
        for file in sorted(staging.rglob("*")):
            if file.is_file():
                bundle.write(file, str(file.relative_to(staging)))
    with pytest.raises(ExposureImportError) as exc:
        import_exposure_package(package, ws)
    assert "hypothesized_failure_mode" in str(exc.value)


def test_t11_ta_side_retains_hypothesis(tmp_path):
    """ingest 后 TA 端 canonical 仍保留 AT↔TASK↔FM 对照（私有）。"""
    ws = make_ws(tmp_path / "student")
    build_p1(ws)
    repo = tmp_path / "repository"
    _ingest_one_built_package(ws, repo)
    registry = SubmissionRegistry(repo)
    assert len(registry.rows) == 1
    task_id = registry.rows[0]["task_id"]
    canonical_meta = json.loads(
        (repo / "tasks" / task_id / "metadata.json").read_text(encoding="utf-8"))
    assert canonical_meta["failure_mode"] == "FM_AGGREGATION"
    # canonical task.json（TA 私有）携带 FM 供内部链路；学生匿名视图不会复制
    canonical_task = json.loads(
        (repo / "tasks" / task_id / "task" / "task.json").read_text(encoding="utf-8"))
    assert canonical_task.get("hypothesized_failure_mode") == "FM_AGGREGATION"


def test_t12_downstream_reads_fm_from_metadata_layer(tmp_path):
    """category 归一化直接接受 canonical Key（assignment/质量评审读取入口）。"""
    for fm in FMS:
        assert normalize_category_code(fm) is not None


# ---------------------------------------------------------------------------
# T13–T14：canonical 全通过 / legacy 全拒绝
# ---------------------------------------------------------------------------


def test_t13_all_canonical_fm_keys_pass(tmp_path):
    ws = make_ws(tmp_path)
    for fm in FMS:
        set_metadata_fm(ws, fm)
        assert validate_workspace(ws, "phase1") == [], fm


def test_t14_all_legacy_numeric_forms_fail(tmp_path):
    for legacy in LEGACY_FORMS:
        ws = make_ws(tmp_path)
        set_metadata_fm(ws, legacy)
        errors = validate_workspace(ws, "phase1")
        assert any(INVALID_FAILURE_MODE_KEY in e for e in errors), legacy
