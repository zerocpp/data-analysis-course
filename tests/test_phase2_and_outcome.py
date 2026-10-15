"""Phase 2 = Fresh Submission + 统一 Outcome Model 验收（全离线）。

覆盖：Phase 1 run/export 回归、Fresh Phase 2 workspace 复用完全相同的
run/export、Phase 2 与 Phase 1 零继承（无 lineage / 无读取 Phase 1 资产）、
overdesign 移除（task replace / filegit）、Expected Failure 校验
（只能 12 FM，Internal Error 被拒）、Internal Error 四大类分类
（llm_api / token_budget / resource_limit(memory/time/disk) /
runtime_crash）与 Resource-vs-Crash 严格区分、correct / analysis_failure /
internal_error 三种真实 run record。
"""

from __future__ import annotations

import importlib.util
import json
import sys
import textwrap
from pathlib import Path

import pytest

from codesetarena.constants import EXPECTED_FAILURE_MODE_CODES
from codesetarena.main.budget import Budget, BudgetExceeded, run_with_budget
from codesetarena.main.export import export_project
from codesetarena.main.llm import LLMError, LLMTimeout
from codesetarena.main.outcome import matrix_label
from codesetarena.main.runner import run_project
from codesetarena.main.validation import validate_project

TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "src/codesetarena/main/agent_template.py"

DATA = "region,amount\neast,10\nwest,20\neast,5\n"
REFERENCE = 'def reference(df):\n    return int(df["amount"].sum())\n'
ENV_TEXT = (
    "LLM_API_KEY=dummy-key\n"
    "LLM_BASE_URL=https://example.invalid/v1\n"
    "LLM_MODEL=dummy-model\n"
    "LLM_TIMEOUT=60\n"
    "LLM_TOKEN_BUDGET=10000\n"
)

DIRECT_ANSWER_AGENT = textwrap.dedent(
    """
    def run(task, df, llm):
        return int(df["amount"].sum())
    """
)
CRASH_AGENT = textwrap.dedent(
    """
    def run(task, df, llm):
        raise KeyError("boom")
    """
)
EXIT_AGENT = textwrap.dedent(
    """
    import os

    def run(task, df, llm):
        os._exit(1)
    """
)
SLEEP_AGENT = textwrap.dedent(
    """
    import time

    def run(task, df, llm):
        time.sleep(30)
        return 0
    """
)
EAT_MEMORY_AGENT = textwrap.dedent(
    """
    import time

    def run(task, df, llm):
        blob = bytearray(200 * 1024 * 1024)
        for offset in range(0, len(blob), 4096):
            blob[offset] = 1
        time.sleep(30)
        return 0
    """
)


# 模块级可 pickle 的脚本化 LLM（spawn 子进程按类名重新导入本模块）。
class FinalLLM:
    """回放固定 final answer（可带 Provider usage）。"""

    def __init__(self, answer, usage=None):
        self._answer = answer
        self._usage = usage or {}
        self.calls = 0
        self.last_usage: dict = {}

    def complete(self, messages):
        self.calls += 1
        self.last_usage = dict(self._usage)
        return json.dumps({"action": "final_answer", "answer": self._answer})


class ApiTimeoutLLM:
    def complete(self, messages):
        raise LLMTimeout("llm request exceeded 60s")


class ApiNetworkLLM:
    def complete(self, messages):
        raise LLMError("URLError: connection refused")


class TokenExhaustLLM:
    def complete(self, messages):
        raise BudgetExceeded("token budget 10000 exceeded before request (used=10000)")


def load_project_ws(ws: Path):
    from codesetarena.main.project import load_project

    return load_project(ws)


TEMPLATE_AGENT = TEMPLATE_PATH.read_text(encoding="utf-8")


def make_workspace(
    root: Path,
    *,
    task: dict | None = None,
    agent_source: str = DIRECT_ANSWER_AGENT,
    env: str = ENV_TEXT,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    files = {
        ".env": env,
        ".env.example": "LLM_API_KEY=\nLLM_BASE_URL=\nLLM_MODEL=\n",
        "agent.py": agent_source,
        "task.json": json.dumps(
            task or {"task_id": "T001", "description": "amount 总和"}, ensure_ascii=False
        ),
        "data.csv": DATA,
        "reference.py": REFERENCE,
    }
    for name, content in files.items():
        (root / name).write_text(content, encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# 1-4. Phase 1 run / export 回归 + Fresh Phase 2 使用完全相同 run / export
# ---------------------------------------------------------------------------


def test_phase1_run_still_works(tmp_path: Path):
    result = run_project(make_workspace(tmp_path / "phase1_ws"))
    assert result["status"] == "success"
    assert result["correct"] is True
    assert result["outcome_type"] == "completed"
    assert result["result"]["result"] == 35
    assert Path(result["run_dir"], "result.json").is_file()


def test_phase1_export_still_works(tmp_path: Path):
    record = export_project(make_workspace(tmp_path / "phase1_ws"))
    assert record["status"] == "exported"
    assert record["evaluation"]["result"]["correct"] is True
    assert Path(record["package_path"]).is_file()


def test_phase2_fresh_workspace_same_run(tmp_path: Path):
    """Phase 2 = 从课程模板重新创建的全新 workspace，命令与 Phase 1 完全一致。"""

    ws = make_workspace(
        tmp_path / "system_NEW" / "phase2_ws",
        task={
            "task_id": "T002",
            "description": "amount 总和",
            "submission_phase": 2,
        },
    )
    result = run_project(ws)
    assert result["status"] == "success"
    assert result["outcome_type"] == "completed"
    assert result["result"]["result"] == 35


def test_phase2_fresh_workspace_same_export(tmp_path: Path):
    ws = make_workspace(
        tmp_path / "system_NEW" / "phase2_ws",
        task={"task_id": "T002", "submission_phase": 2},
    )
    record = export_project(ws)
    assert record["status"] == "exported"
    assert record["evaluation"]["result"]["correct"] is True


# ---------------------------------------------------------------------------
# 5-7. Phase 2 与 Phase 1 完全解耦
# ---------------------------------------------------------------------------


def test_phase2_does_not_read_phase1(tmp_path: Path):
    """phase2 workspace 独立可运行；phase1 workspace 不存在也毫无影响。"""

    phase2 = make_workspace(
        tmp_path / "system_NEW" / "phase2_ws",
        agent_source=DIRECT_ANSWER_AGENT,
    )
    (phase2 / "data.csv").write_text("region,amount\neast,7\n", encoding="utf-8")
    result = run_project(phase2)
    assert result["status"] == "success"
    assert result["result"]["result"] == 7


def test_phase2_uses_only_its_own_agent(tmp_path: Path):
    """Phase 2 的 Agent 是 fresh 设计：换掉 agent.py 立即反映在本 workspace。"""

    ws = make_workspace(
        tmp_path / "system_NEW" / "phase2_ws", agent_source=CRASH_AGENT
    )
    result = run_project(ws)
    assert result["internal_error_category"] == "runtime_crash"
    assert "KeyError" in result["error"]


def test_phase2_has_no_lineage_dependency(tmp_path: Path):
    ws = make_workspace(tmp_path / "phase2_ws")
    result = run_project(ws)
    assert result["status"] == "success"
    assert not (ws / ".task_history").exists()
    assert not (ws / ".filegit").exists()
    assert importlib.util.find_spec("codesetarena.main.task_replace") is None


def test_overdesign_commands_removed():
    from codesetarena.main.__main__ import main

    with pytest.raises(SystemExit):
        main(["task", "replace", "--workspace", "x", "--from", "y"])
    with pytest.raises(SystemExit):
        main(["filegit", "draft", "agent_v1.py"])


# ---------------------------------------------------------------------------
# 8-9. Expected Failure 只能是正式 14 FM；Internal Error 一律拒绝
# ---------------------------------------------------------------------------


def test_expected_failure_accepts_only_official_fms(tmp_path: Path):
    assert EXPECTED_FAILURE_MODE_CODES == {
        "FM01", "FM02", "FM03", "FM04", "FM05", "FM06", "FM07",
        "FM08", "FM09", "FM10", "FM11", "FM12", "FM13", "FM14",
    }
    for code in sorted(EXPECTED_FAILURE_MODE_CODES):
        ws = make_workspace(
            tmp_path / f"ws_{code}", task={"task_id": "T", "expected_failure_mode": code}
        )
        assert validate_project(load_project_ws(ws)) == [], code


def test_expected_failure_rejects_fm15_fm00_and_internal_errors(tmp_path: Path):
    for bad in ("FM15", "FM00", "api timeout", "network error", "token exceeded",
                "memory exceeded", "time exceeded", "disk exceeded", "runtime crash"):
        ws = make_workspace(
            tmp_path / "ws_bad", task={"task_id": "T", "expected_failure_mode": bad}
        )
        errors = validate_project(load_project_ws(ws))
        assert errors, bad
        assert "Internal Error" in errors[0] or "不是正式 Failure Mode" in errors[0]


# ---------------------------------------------------------------------------
# 10-12. LLM 接口 in-band 错误：API timeout / network → llm_api；token → token_budget
# ---------------------------------------------------------------------------


def test_llm_api_timeout_classified_llm_api(tmp_path: Path):
    result = run_project(
        make_workspace(tmp_path / "ws", agent_source=TEMPLATE_AGENT), llm=ApiTimeoutLLM()
    )
    assert result["status"] == "internal_error"
    assert result["outcome_type"] == "internal_error"
    assert result["internal_error_category"] == "llm_api"
    assert result["correct"] is None
    assert result["result"]["correct"] is None
    record = json.loads(Path(result["run_dir"], "result.json").read_text(encoding="utf-8"))
    assert record["internal_error_evidence"]["error_type"] == "LLMTimeout"
    assert matrix_label(record) == "INTERNAL_API"


def test_llm_network_error_classified_llm_api(tmp_path: Path):
    result = run_project(
        make_workspace(tmp_path / "ws", agent_source=TEMPLATE_AGENT), llm=ApiNetworkLLM()
    )
    assert result["internal_error_category"] == "llm_api"
    assert matrix_label(json.loads(
        Path(result["run_dir"], "result.json").read_text(encoding="utf-8")
    )) == "INTERNAL_API"


def test_token_budget_classified_token_budget(tmp_path: Path):
    result = run_project(
        make_workspace(tmp_path / "ws", agent_source=TEMPLATE_AGENT), llm=TokenExhaustLLM()
    )
    assert result["status"] == "internal_error"
    assert result["internal_error_category"] == "token_budget"
    assert result["termination_source"] == "agent_process"
    assert "token_budget_exceeded" in result["error"]
    record = json.loads(Path(result["run_dir"], "result.json").read_text(encoding="utf-8"))
    assert matrix_label(record) == "INTERNAL_TOKEN"


# ---------------------------------------------------------------------------
# 13-15. Supervisor 资源 kill：memory / time / disk → resource_limit
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform not in ("linux", "win32"), reason="RSS 采样仅 Linux/Windows")
def test_memory_exceed_killed_by_supervisor(tmp_path: Path):
    result = run_project(
        make_workspace(tmp_path / "ws", agent_source=EAT_MEMORY_AGENT),
        budget=Budget(timeout_seconds=60, max_memory_mb=150, max_tokens=10000),
    )
    assert result["status"] == "internal_error"
    assert result["internal_error_category"] == "resource_limit"
    assert result["resource_kind"] == "memory"
    assert result["termination_source"] == "supervisor"
    assert matrix_label(json.loads(
        Path(result["run_dir"], "result.json").read_text(encoding="utf-8")
    )) == "INTERNAL_MEMORY"


def test_time_exceed_killed_by_supervisor(tmp_path: Path):
    result = run_project(
        make_workspace(tmp_path / "ws", agent_source=SLEEP_AGENT),
        budget=Budget(timeout_seconds=2, max_tokens=10000),
    )
    assert result["status"] == "internal_error"
    assert result["internal_error_category"] == "resource_limit"
    assert result["resource_kind"] == "time"
    assert result["termination_source"] == "supervisor"
    record = json.loads(Path(result["run_dir"], "result.json").read_text(encoding="utf-8"))
    assert matrix_label(record) == "INTERNAL_TIME"
    assert record["internal_error_evidence"]["configured_limit"] == 2


def test_disk_exceed_killed_by_supervisor(tmp_path: Path):
    """disk 预算约束 run scratch 目录占盘；超限由 Supervisor 强制 kill。"""

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / "blob.bin").write_bytes(b"x" * (2 * 1024 * 1024))
    input_data = {"task": {"task_id": "T"}, "data_csv": str(tmp_path / "data.csv"),
                  "scratch_dir": str(scratch)}
    (tmp_path / "data.csv").write_text(DATA, encoding="utf-8")
    agent = str(make_workspace(tmp_path / "ws", agent_source=DIRECT_ANSWER_AGENT) / "agent.py")
    outcome = run_with_budget(
        agent, input_data, Budget(max_disk_mb=1, max_tokens=10000), {"llm": None}
    )
    assert outcome["ok"] is False
    assert outcome["stop_reason"] == "disk_budget_exceeded"
    assert outcome["configured_limit"] == 1
    assert outcome["observed_value"] > 1
    from codesetarena.main.outcome import classify_budget_outcome

    classification = classify_budget_outcome(outcome)
    assert classification["internal_error_category"] == "resource_limit"
    assert classification["resource_kind"] == "disk"
    assert classification["termination_source"] == "supervisor"
    assert matrix_label(classification) == "INTERNAL_DISK"


# ---------------------------------------------------------------------------
# 16-20. Runtime Crash（与 Resource Kill 严格分开）；内部错误都不是 FM
# ---------------------------------------------------------------------------


def test_unhandled_exception_classified_runtime_crash(tmp_path: Path):
    result = run_project(make_workspace(tmp_path / "ws", agent_source=CRASH_AGENT))
    assert result["status"] == "internal_error"
    assert result["internal_error_category"] == "runtime_crash"
    assert result["termination_source"] == "agent_process"
    assert "KeyError" in result["internal_error_detail"]


def test_abnormal_exit_without_message_is_runtime_crash(tmp_path: Path):
    result = run_project(make_workspace(tmp_path / "ws", agent_source=EXIT_AGENT))
    assert result["internal_error_category"] == "runtime_crash"
    assert result["termination_source"] == "agent_process"
    record = json.loads(Path(result["run_dir"], "result.json").read_text(encoding="utf-8"))
    assert record["internal_error_evidence"]["exit_code"] == 1


@pytest.mark.skipif(sys.platform not in ("linux", "win32"), reason="RSS 采样仅 Linux/Windows")
def test_resource_kill_is_not_runtime_crash(tmp_path: Path):
    result = run_project(
        make_workspace(tmp_path / "ws", agent_source=SLEEP_AGENT),
        budget=Budget(timeout_seconds=2, max_tokens=10000),
    )
    assert result["internal_error_category"] == "resource_limit"
    assert result["internal_error_category"] != "runtime_crash"


def test_internal_errors_are_never_failure_modes(tmp_path: Path):
    """runtime_crash / llm_api / token_budget 的 outcome 都不是 analysis_failure。"""

    cases = [
        (make_workspace(tmp_path / "ws1", agent_source=CRASH_AGENT), None),
        (make_workspace(tmp_path / "ws2", agent_source=TEMPLATE_AGENT), ApiTimeoutLLM()),
        (make_workspace(tmp_path / "ws3", agent_source=TEMPLATE_AGENT), TokenExhaustLLM()),
    ]
    for ws, llm in cases:
        result = run_project(ws, llm=llm)
        assert result["outcome_type"] == "internal_error"
        assert result["outcome_type"] != "analysis_failure"
        assert result["correct"] is None


# ---------------------------------------------------------------------------
# 21-22. 正常答案：wrong → analysis_failure；correct → completed
# ---------------------------------------------------------------------------


def test_wrong_answer_is_analysis_failure(tmp_path: Path):
    result = run_project(
        make_workspace(tmp_path / "ws", agent_source=TEMPLATE_AGENT), llm=FinalLLM(999)
    )
    assert result["status"] == "success"
    assert result["outcome_type"] == "analysis_failure"
    assert result["correct"] is False
    assert result["result"]["result"] == 999
    assert result["result"]["declared_gold_answer"] == 35
    assert result["internal_error_category"] is None
    assert result["termination_source"] is None


def test_correct_answer_is_completed(tmp_path: Path):
    result = run_project(
        make_workspace(tmp_path / "ws", agent_source=TEMPLATE_AGENT), llm=FinalLLM(35)
    )
    assert result["status"] == "success"
    assert result["outcome_type"] == "completed"
    assert result["correct"] is True
    assert matrix_label(result) == "CORRECT"


def test_run_record_schema_shape(tmp_path: Path):
    """§22 统一 Run Record：三种 outcome 共用同一 schema。"""

    result = run_project(
        make_workspace(tmp_path / "ws", agent_source=TEMPLATE_AGENT), llm=FinalLLM(999)
    )
    assert set(result) >= {
        "run_id", "agent_id", "task_id", "outcome_type", "correct",
        "internal_error_category", "internal_error_detail", "termination_source",
        "elapsed_seconds", "total_tokens", "run_dir",
    }
    record = json.loads(Path(result["run_dir"], "result.json").read_text(encoding="utf-8"))
    assert set(record) >= {
        "run_id", "agent_id", "task_id", "expected_failure_mode", "outcome_type",
        "result", "correct", "internal_error_category", "internal_error_detail",
        "termination_source", "elapsed_seconds", "total_tokens",
    }
