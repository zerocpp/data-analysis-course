"""最终验收工程（Final Acceptance）：触发全部错误路径 + 边界断言。

原则：所有场景走真实执行链（run_project / run_with_budget，
子进程 + Supervisor），不 mock Runner；LLM 注入仅用于离线确定性
（网络/时间/Token 三类错误本身就在 LLM 接口层）。

覆盖矩阵（边界最小化：更小的内存 / 时间 / Token，容易触发）：
  token_budget（0 → 0 次请求；精确相等 → 第 1 次放行第 2 次拦；超限边界；无 usage 估算）
  llm_api（第 1 次成功、第 2 次超时 / 网络错误；失败后重试成功）
  resource_limit（memory-轻负载放行 / 重负载 kill；time=1s kill）
  runtime_crash / reference_error / validation / analysis_failure
"""

from __future__ import annotations

import json
import os
import sys
import textwrap
from pathlib import Path

import pytest

from codesetarena.main.budget import Budget, run_with_budget
from codesetarena.main.llm import LLMError, LLMTimeout
from codesetarena.main.outcome import matrix_label
from codesetarena.main.runner import run_project
from codesetarena.main.validation import ValidationError

TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "src/codesetarena/main/agent_template.py"
TEMPLATE_AGENT = TEMPLATE_PATH.read_text(encoding="utf-8")

DATA = "region,amount\neast,10\nwest,20\neast,5\n"
REFERENCE = 'def reference(df):\n    return int(df["amount"].sum())\n'
ENV_TEXT = (
    "LLM_API_KEY=dummy-key\n"
    "LLM_BASE_URL=https://example.invalid/v1\n"
    "LLM_MODEL=dummy-model\n"
    "LLM_TIMEOUT=60\n"
    "LLM_TOKEN_BUDGET=10000\n"
)

ONE_LLM_CALL_AGENT = textwrap.dedent(
    """
    def run(task, df, llm):
        reply = llm.complete([{"role": "user", "content": "hi"}])
        return 1
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
WRONG_AGENT = textwrap.dedent(
    """
    def run(task, df, llm):
        return 999
    """
)
ANSWER_AGENT = textwrap.dedent(
    """
    def run(task, df, llm):
        return int(df["amount"].sum())
    """
)
CALL_TWICE_AGENT = textwrap.dedent(
    """
    def run(task, df, llm):
        llm.complete([{"role": "user", "content": "step 1"}])
        llm.complete([{"role": "user", "content": "step 2"}])
        return 35
    """
)
RETRY_AGENT = textwrap.dedent(
    """
    def run(task, df, llm):
        reply = None
        for _ in range(3):
            try:
                reply = llm.complete([{"role": "user", "content": "hi"}])
                break
            except Exception:
                continue
        return 35 if reply is not None else 0
    """
)
BROKEN_REFERENCE = 'def reference(df):\n    raise ValueError("gold calculator broken")\n'


# 可 pickle 的脚本化 LLM（spawn 子进程按类名重新导入本模块）。
class UsageLLM:
    """每次请求带 Provider 真实 usage（200 tokens/次）。"""

    def __init__(self):
        self.calls = 0
        self.last_usage: dict = {}

    def complete(self, messages):
        self.calls += 1
        self.last_usage = {"prompt_tokens": 100, "completion_tokens": 100}
        return json.dumps({"action": "final_answer", "answer": 35})


class ApiTimeoutLLM:
    def complete(self, messages):
        raise LLMTimeout("llm request exceeded deadline")


class ApiNetworkLLM:
    def complete(self, messages):
        raise LLMError("URLError: connection refused")


class SeqLLM:
    """按调用序号回放行为（可 pickle，供 spawn 子进程）。

    behaviors 元素：
      ("ok", payload)    —— 返回 payload，并累计 usage（prompt=N0）
      ("error", name)    —— 抛 LLMTimeout / LLMError（name 匹配）
    用于“第 1 次成功、第 2 次强制失败”等序列边界。
    """

    _ERRORS = {"LLMTimeout": LLMTimeout, "LLMError": LLMError}

    def __init__(self, behaviors, usage_prompt=30):
        self._behaviors = list(behaviors)
        self._usage_prompt = usage_prompt
        self.calls = 0
        self.last_usage: dict = {}

    def complete(self, messages):
        idx = self.calls
        self.calls += 1
        if idx >= len(self._behaviors):
            raise LLMError(f"unexpected call #{idx + 1}")
        kind, payload = self._behaviors[idx]
        if kind == "error":
            exc = self._ERRORS[payload](payload)
            exc.request_index = idx + 1
            exc.retries = 0
            raise exc
        self.last_usage = {"prompt_tokens": self._usage_prompt, "completion_tokens": 0}
        return payload


class NoUsageLLM:
    """Provider 不返回 usage：预算退化为字符估算（usage_complete=False）。"""

    def complete(self, messages):
        return "x" * 600  # 估算 tokens 明显大于最小预算


def make_workspace(root: Path, *, agent_source: str = ONE_LLM_CALL_AGENT,
                   reference: str = REFERENCE, task: dict | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    files = {
        ".env": ENV_TEXT,
        ".env.example": "LLM_API_KEY=\nLLM_BASE_URL=\nLLM_MODEL=\n",
        "agent.py": agent_source,
        "task.json": json.dumps(
            task or {"task_id": "ACCEPT", "description": "amount 总和"}, ensure_ascii=False
        ),
        "data.csv": DATA,
        "reference.py": reference,
    }
    for name, content in files.items():
        (root / name).write_text(content, encoding="utf-8")
    return root


def read_record(result: dict) -> dict:
    return json.loads(Path(result["run_dir"], "result.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Token 边界：小预算必须“及时”报错
# ---------------------------------------------------------------------------


def test_acceptance_token_budget_zero_reports_before_any_request(tmp_path: Path):
    """max_tokens=0：第一次 LLM 请求前即抛 BudgetExceeded，0 次请求。"""

    result = run_project(
        make_workspace(tmp_path / "ws"), budget=Budget(timeout_seconds=60, max_tokens=0)
    )
    record = read_record(result)
    assert result["status"] == "internal_error"
    assert result["internal_error_category"] == "token_budget"
    assert result["correct"] is None
    assert record["budget"]["calls"] == 0, "预算 0 时不得发出任何 LLM 请求"
    assert record["budget"]["elapsed_seconds"] < 10, "必须及时报错（无请求、无等待）"
    assert "token budget 0 exceeded before request" in record["error"]
    assert matrix_label(record) == "INTERNAL_TOKEN"


def test_acceptance_tiny_token_budget_stops_after_exactly_one_request(tmp_path: Path):
    """max_tokens=1：允许第 1 次请求，随后 usage 即超限 → 恰好 1 次、及时终止。"""

    result = run_project(
        make_workspace(tmp_path / "ws"), llm=UsageLLM(),
        budget=Budget(timeout_seconds=60, max_tokens=1),
    )
    record = read_record(result)
    assert result["internal_error_category"] == "token_budget"
    assert record["budget"]["calls"] == 1, "小预算下最多 1 次请求即终止"
    assert record["budget"]["tokens"] >= 200
    assert record["budget"]["elapsed_seconds"] < 20
    assert matrix_label(record) == "INTERNAL_TOKEN"


# ---------------------------------------------------------------------------
# LLM 接口错误：timeout / network
# ---------------------------------------------------------------------------


def test_acceptance_llm_timeout_maps_to_llm_api(tmp_path: Path):
    result = run_project(
        make_workspace(tmp_path / "ws"), llm=ApiTimeoutLLM(),
        budget=Budget(timeout_seconds=60, max_tokens=10000),
    )
    record = read_record(result)
    assert result["internal_error_category"] == "llm_api"
    assert record["internal_error_evidence"]["error_type"] == "LLMTimeout"
    assert matrix_label(record) == "INTERNAL_API"


def test_acceptance_llm_network_error_maps_to_llm_api(tmp_path: Path):
    result = run_project(
        make_workspace(tmp_path / "ws"), llm=ApiNetworkLLM(),
        budget=Budget(timeout_seconds=60, max_tokens=10000),
    )
    assert result["internal_error_category"] == "llm_api"
    assert matrix_label(read_record(result)) == "INTERNAL_API"


# ---------------------------------------------------------------------------
# Supervisor 资源 kill：memory / time / disk
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform not in ("linux", "win32"), reason="RSS 采样仅 Linux/Windows")
def test_acceptance_memory_kill_by_supervisor(tmp_path: Path):
    result = run_project(
        make_workspace(tmp_path / "ws", agent_source=EAT_MEMORY_AGENT),
        budget=Budget(timeout_seconds=60, max_memory_mb=150, max_tokens=10000),
    )
    record = read_record(result)
    assert result["internal_error_category"] == "resource_limit"
    assert result["resource_kind"] == "memory"
    assert result["termination_source"] == "supervisor"
    assert record["internal_error_evidence"]["observed_value"] > 150
    assert matrix_label(record) == "INTERNAL_MEMORY"


def test_acceptance_time_kill_by_supervisor(tmp_path: Path):
    result = run_project(
        make_workspace(tmp_path / "ws", agent_source=SLEEP_AGENT),
        budget=Budget(timeout_seconds=2, max_tokens=10000),
    )
    record = read_record(result)
    assert result["internal_error_category"] == "resource_limit"
    assert result["resource_kind"] == "time"
    assert result["termination_source"] == "supervisor"
    assert record["internal_error_evidence"]["configured_limit"] == 2
    assert record["elapsed_seconds"] < 15, "墙钟超限必须及时被 Supervisor 终止"
    assert matrix_label(record) == "INTERNAL_TIME"


def test_acceptance_disk_kill_by_supervisor(tmp_path: Path):
    """disk 预算约束 scratch 目录；超限 Supervisor kill（不依赖 agent）。"""

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / "blob.bin").write_bytes(b"x" * (2 * 1024 * 1024))
    (tmp_path / "data.csv").write_text(DATA, encoding="utf-8")
    input_data = {"task": {"task_id": "T"}, "data_csv": str(tmp_path / "data.csv"),
                  "scratch_dir": str(scratch)}
    agent = str(make_workspace(tmp_path / "ws", agent_source=ONE_LLM_CALL_AGENT) / "agent.py")
    outcome = run_with_budget(agent, input_data, Budget(max_disk_mb=1, max_tokens=10000),
                              {"llm": None})
    assert outcome["ok"] is False
    assert outcome["stop_reason"] == "disk_budget_exceeded"
    assert outcome["observed_value"] > 1
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from codesetarena.main.outcome import classify_budget_outcome

    classification = classify_budget_outcome(outcome)
    assert classification["internal_error_category"] == "resource_limit"
    assert classification["resource_kind"] == "disk"
    assert classification["termination_source"] == "supervisor"
    assert matrix_label(classification) == "INTERNAL_DISK"


# ---------------------------------------------------------------------------
# Runtime Crash：Agent 自身异常 / 异常退出
# ---------------------------------------------------------------------------


def test_acceptance_unhandled_exception_is_runtime_crash(tmp_path: Path):
    result = run_project(make_workspace(tmp_path / "ws", agent_source=CRASH_AGENT))
    record = read_record(result)
    assert result["internal_error_category"] == "runtime_crash"
    assert result["termination_source"] == "agent_process"
    assert "KeyError" in result["internal_error_detail"]
    assert matrix_label(record) == "INTERNAL_RUNTIME"


def test_acceptance_abnormal_exit_is_runtime_crash(tmp_path: Path):
    result = run_project(make_workspace(tmp_path / "ws", agent_source=EXIT_AGENT))
    record = read_record(result)
    assert result["internal_error_category"] == "runtime_crash"
    assert record["internal_error_evidence"]["exit_code"] == 1


# ---------------------------------------------------------------------------
# Reference Error：教师侧 gold 失败（≠ Agent 错误）
# ---------------------------------------------------------------------------


def test_acceptance_reference_error_classified_separately(tmp_path: Path):
    result = run_project(
        make_workspace(tmp_path / "ws", agent_source=ANSWER_AGENT, reference=BROKEN_REFERENCE)
    )
    record = read_record(result)
    assert result["status"] == "internal_error"
    assert result["internal_error_category"] == "reference_error"
    assert result["termination_source"] == "runner"
    assert result["correct"] is None
    assert "ValueError" in result["internal_error_detail"]


# ---------------------------------------------------------------------------
# Validation：早期失败，不执行、不落 run 目录
# ---------------------------------------------------------------------------


def test_acceptance_validation_early_stop_no_run_dir(tmp_path: Path):
    ws = make_workspace(tmp_path / "ws")
    (ws / "reference.py").unlink()
    with pytest.raises(ValidationError):
        run_project(ws)
    assert not (ws / "runs").exists(), "校验失败不得创建任何 run 目录"


# ---------------------------------------------------------------------------
# Wrong Answer：analysis_failure，不是 Internal Error
# ---------------------------------------------------------------------------


def test_acceptance_wrong_answer_is_analysis_failure_not_internal(tmp_path: Path):
    result = run_project(make_workspace(tmp_path / "ws", agent_source=WRONG_AGENT))
    record = read_record(result)
    assert result["status"] == "success"
    assert result["outcome_type"] == "analysis_failure"
    assert result["correct"] is False
    assert result["internal_error_category"] is None
    assert matrix_label(record) == "WRONG"


# ===========================================================================
# 边界矩阵（Boundary Matrix）：最小化阈值，覆盖学生可能触发的每种情况
# ===========================================================================


# -- Token 边界 --------------------------------------------------------------


def test_boundary_token_exact_equal_allows_call1_blocks_call2(tmp_path: Path):
    """max_tokens == 单次 usage（精确相等）：第 1 次放行，第 2 次请求前拦截。"""

    result = run_project(
        make_workspace(tmp_path / "ws", agent_source=CALL_TWICE_AGENT),
        llm=SeqLLM([("ok", "a"), ("ok", "b")], usage_prompt=30),
        budget=Budget(timeout_seconds=60, max_tokens=30),
    )
    record = read_record(result)
    assert result["internal_error_category"] == "token_budget"
    assert record["budget"]["calls"] == 1, "第 2 次调用必须在请求前被拦截"
    assert record["budget"]["tokens"] == 30
    assert "exceeded before request" in record["error"]
    assert matrix_label(record) == "INTERNAL_TOKEN"


def test_boundary_token_exceeded_after_second_call_post_check(tmp_path: Path):
    """max_tokens=40：第 1 次 ok(30) 放行，第 2 次 ok(30) 后置检查超限。"""

    result = run_project(
        make_workspace(tmp_path / "ws", agent_source=CALL_TWICE_AGENT),
        llm=SeqLLM([("ok", "a"), ("ok", "b")], usage_prompt=30),
        budget=Budget(timeout_seconds=60, max_tokens=40),
    )
    record = read_record(result)
    assert result["internal_error_category"] == "token_budget"
    assert record["budget"]["calls"] == 2, "两次请求都已发出，但第二次结束后即终止"
    assert record["budget"]["tokens"] == 60
    assert "exceeded after request" in record["error"]


def test_boundary_token_provider_without_usage_estimated(tmp_path: Path):
    """Provider 不返回 usage：退化为字符估算仍强制预算（usage_complete=False）。"""

    result = run_project(
        make_workspace(tmp_path / "ws", agent_source=ONE_LLM_CALL_AGENT),
        llm=NoUsageLLM(),
        budget=Budget(timeout_seconds=60, max_tokens=10),
    )
    record = read_record(result)
    assert result["internal_error_category"] == "token_budget"
    assert record["budget"]["usage_complete"] is False
    assert record["budget"]["tokens"] >= 10
    assert matrix_label(record) == "INTERNAL_TOKEN"


# -- LLM 序列边界：第 1 次成功，第 2 次强制失败 ---------------------------------


def test_boundary_llm_first_ok_second_timeout(tmp_path: Path):
    result = run_project(
        make_workspace(tmp_path / "ws", agent_source=CALL_TWICE_AGENT),
        llm=SeqLLM([("ok", "a"), ("error", "LLMTimeout")]),
        budget=Budget(timeout_seconds=60, max_tokens=10000),
    )
    record = read_record(result)
    assert result["internal_error_category"] == "llm_api"
    assert record["internal_error_evidence"]["error_type"] == "LLMTimeout"
    assert record["internal_error_evidence"]["request_index"] == 2, "失败发生在第 2 次请求"
    calls = json.loads(Path(result["run_dir"], "llm_calls.json").read_text(encoding="utf-8"))
    assert calls[0]["error"] is None and calls[1]["error"], "第 1 次成功、第 2 次失败"
    assert matrix_label(record) == "INTERNAL_API"


def test_boundary_llm_first_ok_second_network_error(tmp_path: Path):
    result = run_project(
        make_workspace(tmp_path / "ws", agent_source=CALL_TWICE_AGENT),
        llm=SeqLLM([("ok", "a"), ("error", "LLMError")]),
        budget=Budget(timeout_seconds=60, max_tokens=10000),
    )
    record = read_record(result)
    assert result["internal_error_category"] == "llm_api"
    assert record["internal_error_evidence"]["request_index"] == 2
    assert matrix_label(record) == "INTERNAL_API"


def test_boundary_llm_first_fails_agent_retry_recovers(tmp_path: Path):
    """in-band 错误可恢复：第 1 次超时 → Agent 重试 → 第 2 次成功 → completed。"""

    result = run_project(
        make_workspace(tmp_path / "ws", agent_source=RETRY_AGENT),
        llm=SeqLLM([("error", "LLMTimeout"), ("ok", "final")]),
        budget=Budget(timeout_seconds=60, max_tokens=10000),
    )
    record = read_record(result)
    assert result["status"] == "success"
    assert result["outcome_type"] == "completed"
    assert result["correct"] is True
    calls = json.loads(Path(result["run_dir"], "llm_calls.json").read_text(encoding="utf-8"))
    assert len(calls) == 2 and calls[0]["error"] and calls[1]["error"] is None


# -- 内存边界（阈值调小） -------------------------------------------------------


@pytest.mark.skipif(sys.platform not in ("linux", "win32"), reason="RSS 采样仅 Linux/Windows")
def test_boundary_memory_light_agent_passes_at_130mb(tmp_path: Path):
    """轻负载 Agent 在 130MB 下必须正常完成（基线 RSS ~70MB，留足余量）。"""

    result = run_project(
        make_workspace(tmp_path / "ws"), llm=UsageLLM(),
        budget=Budget(timeout_seconds=60, max_memory_mb=130, max_tokens=10000),
    )
    assert result["status"] == "success", "轻负载不得被内存限额误杀"


@pytest.mark.skipif(sys.platform not in ("linux", "win32"), reason="RSS 采样仅 Linux/Windows")
def test_boundary_memory_heavy_agent_killed_at_100mb(tmp_path: Path):
    """200MB 分配 + 100MB 限额：必然触发 Supervisor 内存 kill（小阈值易触发）。"""

    result = run_project(
        make_workspace(tmp_path / "ws", agent_source=EAT_MEMORY_AGENT),
        budget=Budget(timeout_seconds=60, max_memory_mb=100, max_tokens=10000),
    )
    record = read_record(result)
    assert result["internal_error_category"] == "resource_limit"
    assert result["resource_kind"] == "memory"
    assert result["termination_source"] == "supervisor"
    assert record["internal_error_evidence"]["observed_value"] >= 100
    assert matrix_label(record) == "INTERNAL_MEMORY"


# -- 时间边界（阈值调小） -------------------------------------------------------


def test_boundary_time_one_second_kills_sleeping_agent(tmp_path: Path):
    """timeout=1s + Agent sleep 30s：1 秒即被 Supervisor 终止（最小阈值）。"""

    result = run_project(
        make_workspace(tmp_path / "ws", agent_source=SLEEP_AGENT),
        budget=Budget(timeout_seconds=1, max_tokens=10000),
    )
    record = read_record(result)
    assert result["internal_error_category"] == "resource_limit"
    assert result["resource_kind"] == "time"
    assert result["termination_source"] == "supervisor"
    assert record["elapsed_seconds"] < 10, "1s 阈值必须及时终止，不得挂起"
    assert matrix_label(record) == "INTERNAL_TIME"