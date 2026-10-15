"""Runtime Resource Limits Teacher Demo（§25 自动测试）。

覆盖：四案例真实 Runtime 结果、phase1/phase2/正式 policy 完整性、
demo 不进正式 Submission、独立命令可重复执行。
"""

from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest

sys_path = __import__("sys").path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys_path:
    sys_path.insert(0, str(ROOT))

from codesetarena.demo import run_all  # noqa: E402
from codesetarena.main.build_package import build_phase_package  # noqa: E402
from codesetarena.main.init_workspace import init_workspace  # noqa: E402

SID = "20250001"


@pytest.fixture(scope="module")
def demo_ws(tmp_path_factory):
    """完整两阶段 workspace（init 播种 + 学号填好），供 demo 与完整性测试复用。"""
    ws = tmp_path_factory.mktemp("demo_ws") / "ws"
    init_workspace(ws)
    (ws / ".env").write_text(
        "LLM_BASE_URL=http://x\nLLM_API_KEY=k\nLLM_MODEL=m\n", encoding="utf-8")
    for phase in ("phase1", "phase2"):
        meta = ws / phase / "metadata.json"
        data = json.loads(meta.read_text())
        data.update({"student_id": SID, "task_id": f"TASK_{SID}",
                     "agent_id": f"AGENT_{SID}"})
        meta.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return ws


@pytest.fixture(scope="module")
def demo_result(demo_ws):
    return run_all(demo_ws, quiet=True)


def _case(demo_result, case):
    return demo_result["cases"][case]


def test_demo_too_loose_timeout(demo_result):
    record = _case(demo_result, "too-loose")
    assert record["status"] == "PASS" and record["timed_out"] is True
    assert record["timeout_seconds"] == 10
    assert record["classification"]["internal_error_category"] == "resource_limit"
    assert record["classification"]["resource_kind"] == "time"


def test_demo_too_strict_timeout(demo_result):
    record = _case(demo_result, "too-strict")
    assert record["status"] == "PASS" and record["timed_out"] is True
    assert record["timeout_seconds"] == 2
    assert record["elapsed_seconds"] < 10


def test_demo_normal_pass(demo_result):
    record = _case(demo_result, "normal")
    assert record["status"] == "PASS" and record["timed_out"] is False
    assert record["answer"] == 60 and record["correct"] is True
    assert record["reference_answer"] == 60


def test_demo_timeout_isolation(demo_result):
    record = _case(demo_result, "timeout-isolation")
    assert record["status"] == "PASS" and record["timed_out"] is True
    assert record["timeout_seconds"] == 3
    assert demo_result["health_check"]["status"] == "PASS"
    assert demo_result["health_check"]["correct"] is True


def test_demo_same_agent_control_variables(demo_ws, demo_result):
    """控制变量：01 与 04 同一坏 agent；02 与 03 同一好 agent。"""
    rl = demo_ws / "demos" / "runtime_limits"
    bad = (rl / "01_too_loose_bad_agent" / "agent.py").read_bytes() == \
        (rl / "04_reasonable_bad_agent" / "agent.py").read_bytes()
    good = (rl / "02_too_strict_good_agent" / "agent.py").read_bytes() == \
        (rl / "03_reasonable_good_agent" / "agent.py").read_bytes()
    assert bad and good


def test_demo_does_not_modify_phase1_and_phase2(demo_ws, demo_result):
    integrity = demo_result["integrity"]
    assert integrity["phase1_modified"] is False
    assert integrity["phase2_modified"] is False
    assert integrity["policy_modified"] is False


def test_demo_not_in_submission(demo_ws):
    """demo 目录永不进入 P1/P2 提交包（builder 只读 phase 目录）。"""
    (demo_ws / "demos" / "runtime_limits" / "leak_marker.txt").write_text("x")
    record = build_phase_package(demo_ws, phase="phase1", student_id=SID)
    with tarfile.open(record["package_path"]) as outer:
        names = [m.name for m in outer.getmembers()]
    assert all("demos" not in name and "leak_marker" not in name for name in names)
    with tarfile.open(Path(record["package_path"])) as outer:
        extract = Path(record["package_path"]).parent / "chk"
        outer.extractall(extract, filter="data")
        top = next(p for p in extract.iterdir() if p.is_dir())
        with tarfile.open(top / f"Tasks_{SID}.tar.gz") as inner:
            inner_names = [m.name for m in inner.getmembers()]
    assert all("demos" not in n for n in inner_names)


def test_demo_runs_evidence_written(demo_ws):
    runs = demo_ws / "demos" / "runtime_limits" / "runs"
    for name in ("demo_01", "demo_02", "demo_03", "demo_04", "health_check"):
        assert (runs / name / "result.json").is_file(), name
    assert (runs / "demo_summary.json").is_file()
    summary = json.loads((runs / "demo_summary.json").read_text())
    assert summary["demo_status"] == "PASS"
    assert summary["real_llm_called"] is False
