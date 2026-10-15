"""Budget V1 + Agent 契约可替换性验收（全离线，无真实网络）。

覆盖：时间 / 内存 / 累计 Token 三项资源预算、统一返回结构；两个内部实现
完全不同、但都满足固定契约 ``run(task, df, llm) -> answer`` 的 agent.py 走
同一个 ``run_with_budget`` 入口。Budget 超限是工程级终止（agent_error /
*_budget_exceeded），绝不进入 Observation。
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from codesetarena.main.budget import (
    Budget,
    BudgetExceeded,
    BudgetedLLM,
    run_with_budget,
)

BUDGET_PATH = Path(__file__).resolve().parents[1] / "src/codesetarena/main/budget.py"
RUNNER_PATH = Path(__file__).resolve().parents[1] / "src/codesetarena/main/runner.py"

DATA_CSV = "region,amount\neast,10\nwest,20\n"


class UsageLLM:
    """脚本化 LLM：回放文本并报告 Provider 真实 usage（可 pickle，供子进程注入）。"""

    def __init__(self, responses, usage):
        self._responses = list(responses)
        self._usage = usage
        self.calls = 0
        self.last_usage: dict = {}

    def complete(self, messages):
        self.calls += 1
        self.last_usage = dict(self._usage)
        return self._responses.pop(0)


def write_agent(tmp_path: Path, source: str, name: str = "agent") -> str:
    """写一个满足固定契约 run(task, df, llm) 的 agent.py，返回路径。"""

    path = tmp_path / f"{name}.py"
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    return str(path)


def make_input(tmp_path: Path, task: dict | None = None) -> dict:
    data_csv = tmp_path / "data.csv"
    data_csv.write_text(DATA_CSV, encoding="utf-8")
    return {"task": task or {"task_id": "T1"}, "data_csv": str(data_csv)}


# ---------------------------------------------------------------------------
# Budget：正常完成 / 时间 / 内存 / Token / agent 错误
# ---------------------------------------------------------------------------


def test_budget_normal_completion(tmp_path: Path):
    agent = write_agent(
        tmp_path,
        """
        def run(task, df, llm):
            return f"answer:{task['task_id']}:{len(df)}"
        """,
    )
    outcome = run_with_budget(
        agent, make_input(tmp_path, {"task_id": "T9"}), Budget(), {"llm": None}
    )
    assert outcome["ok"] is True
    assert outcome["result"] == "answer:T9:2"
    assert outcome["stop_reason"] == "completed"
    assert outcome["error"] is None
    assert set(outcome["usage"]) == {
        "elapsed_seconds", "peak_memory_mb", "tokens", "calls", "usage_complete"
    }
    assert outcome["usage"]["tokens"] == 0
    assert outcome["usage"]["elapsed_seconds"] >= 0


def test_budget_time_exceeded(tmp_path: Path):
    agent = write_agent(
        tmp_path,
        """
        import time

        def run(task, df, llm):
            time.sleep(30)
        """,
    )
    outcome = run_with_budget(agent, make_input(tmp_path), Budget(timeout_seconds=1), {"llm": None})
    assert outcome["ok"] is False
    assert outcome["result"] is None
    assert outcome["stop_reason"] == "time_budget_exceeded"
    assert 1 <= outcome["usage"]["elapsed_seconds"] < 10


@pytest.mark.skipif(
    sys.platform not in ("linux", "win32"), reason="RSS 采样仅实现 Linux/Windows"
)
def test_budget_memory_exceeded(tmp_path: Path):
    agent = write_agent(
        tmp_path,
        """
        import time

        def run(task, df, llm):
            blob = bytearray(200 * 1024 * 1024)
            for offset in range(0, len(blob), 4096):
                blob[offset] = 1
            time.sleep(30)
        """,
    )
    outcome = run_with_budget(agent, make_input(tmp_path), Budget(max_memory_mb=150), {"llm": None})
    assert outcome["ok"] is False
    assert outcome["stop_reason"] == "memory_budget_exceeded"
    assert outcome["usage"]["peak_memory_mb"] > 150


def test_budget_token_exceeded(tmp_path: Path):
    agent = write_agent(
        tmp_path,
        """
        def run(task, df, llm):
            llm.complete([{"role": "user", "content": "hi"}])
            llm.complete([{"role": "user", "content": "hi"}])
            return "never reached"
        """,
    )
    llm = UsageLLM(["a", "b"], {"prompt_tokens": 700, "completion_tokens": 600})
    outcome = run_with_budget(
        agent, make_input(tmp_path), Budget(max_tokens=2000), {"llm": llm}
    )
    assert outcome["ok"] is False
    assert outcome["result"] is None
    assert outcome["stop_reason"] == "token_budget_exceeded"
    assert "token budget 2000" in outcome["error"]
    assert outcome["usage"]["tokens"] == 2600


def test_budget_agent_error(tmp_path: Path):
    agent = write_agent(
        tmp_path,
        """
        def run(task, df, llm):
            raise ValueError("boom")
        """,
    )
    outcome = run_with_budget(agent, make_input(tmp_path), Budget(), {"llm": None})
    assert outcome["ok"] is False
    assert outcome["stop_reason"] == "agent_error"
    assert "ValueError: boom" in outcome["error"]


# ---------------------------------------------------------------------------
# Agent 契约可替换：两个不同内部实现、同一契约，走同一 Budget 入口
# ---------------------------------------------------------------------------


def test_agent_contract_replacement_same_budget(tmp_path: Path):
    agent_v1 = write_agent(tmp_path, 'def run(task, df, llm):\n    return "v1"\n', "agent_v1")
    agent_v2 = write_agent(tmp_path, 'def run(task, df, llm):\n    return "v2"\n', "agent_v2")
    budget = Budget()
    input_data = make_input(tmp_path)
    assert run_with_budget(agent_v1, input_data, budget, {"llm": None})["result"] == "v1"
    assert run_with_budget(agent_v2, input_data, budget, {"llm": None})["result"] == "v2"


def test_pandas_agent_template_through_same_budget(tmp_path: Path):
    template = (
        Path(__file__).resolve().parents[1] / "src/codesetarena/main/agent_template.py"
    )
    agent = write_agent(tmp_path, template.read_text(encoding="utf-8"))
    llm = UsageLLM(
        ['{"action": "final_answer", "answer": "30"}'],
        {"prompt_tokens": 5, "completion_tokens": 1},
    )
    outcome = run_with_budget(agent, make_input(tmp_path), Budget(), {"llm": llm})
    assert outcome["ok"] is True
    assert outcome["result"] == "30"
    assert outcome["usage"]["tokens"] == 6


def test_budget_and_runner_do_not_know_agent_internals():
    """Budget/Runner 只依赖固定契约，不依赖任何 agent 内部实现符号。"""

    for forbidden in ("parse_action", "execute_pandas", "build_initial_messages", "messages.append"):
        assert forbidden not in BUDGET_PATH.read_text(encoding="utf-8"), forbidden
    runner_source = RUNNER_PATH.read_text(encoding="utf-8")
    for forbidden in ("parse_action", "execute_pandas", "build_initial_messages"):
        assert forbidden not in runner_source, forbidden


# ---------------------------------------------------------------------------
# BudgetedLLM：真实 usage 累计（整个执行级别）
# ---------------------------------------------------------------------------


def test_budgeted_llm_accumulates_real_usage():
    inner = UsageLLM(["a", "b", "c"], {"prompt_tokens": 700, "completion_tokens": 300})
    llm = BudgetedLLM(inner)
    for _ in range(3):
        llm.complete([{"role": "user", "content": "x"}])
    assert llm.calls == 3
    assert llm.total_tokens == 3000


def test_budgeted_llm_accepts_usage_aliases_and_total():
    llm = BudgetedLLM(UsageLLM(["a"], {"input_tokens": 10, "output_tokens": 5}))
    llm.complete([])
    assert llm.total_tokens == 15
    llm = BudgetedLLM(UsageLLM(["a"], {"total_tokens": 7}))
    llm.complete([])
    assert llm.total_tokens == 7


def test_budgeted_llm_estimates_only_without_usage():
    llm = BudgetedLLM(UsageLLM(["hello world"], {}))
    assert llm.complete([{"role": "user", "content": "12345678"}]) == "hello world"
    assert llm.total_tokens >= 1  # 无真实 usage 才允许估算


def test_budgeted_llm_enforces_whole_execution_budget():
    inner = UsageLLM(["a", "b", "c"], {"prompt_tokens": 1000, "completion_tokens": 0})
    llm = BudgetedLLM(inner, max_tokens=2500)
    llm.complete([])
    llm.complete([])
    with pytest.raises(BudgetExceeded):
        llm.complete([])
    assert llm.total_tokens == 3000
    assert llm.calls == 3
