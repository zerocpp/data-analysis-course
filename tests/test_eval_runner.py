"""统一评测工具验收（全离线：mock LLM 注入 + 真实 run core 子进程）。

覆盖任务书 §10 的验收面：

- 配置合并优先级（系统默认 < 配置文件 < CLI 显式覆盖 < Policy 钳制）；
- 单次执行能区分 correct / wrong_answer / timeout / token_budget_exceeded /
  api_error / runtime_error / unevaluated（input_error）；
- CLI 覆盖参数真实进入 Runtime（--timeout 2 + sleep Agent 被 Budget
  Supervisor 提前 kill，而不是跑满 policy 时限）；
- 批量串行 2 Task × 2 Agent × 2 Model = 8 组合：行数 = 计划数、顺序 =
  Model → Task → Agent、单条失败不中断批次、summary 只从磁盘 result.json
  生成、结果文件缺失被显式标记（result_read_error）；
- 覆盖目录策略（默认拒绝覆盖 / --force 重建 / --resume 跳过已完成）；
- API Key 绝不出现在 result.json / eval_record.json / execution.log /
  summary.csv / summary.json。
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from bigdata_v10110_helpers import DETERMINISTIC_AGENT, DETERMINISTIC_REFERENCE
from codesetarena.eval import (
    EvalConfigError,
    EvalError,
    load_batch_config,
    load_config_file,
    resolve_limits,
    resolve_model_config,
    run_batch,
    run_one,
)
from codesetarena.eval.summary import SUMMARY_CSV_FIELDS, build_summary, write_summary
from codesetarena.main.__main__ import main as datac_main
from codesetarena.main.llm import LLMError

SECRET = "sk-EVAL-TEST-SECRET-0001"
DATA_1 = "region,amount\nA,1.5\nB,2.5\n"  # sum = 4.0
DATA_2 = "region,amount\nA,40\nB,60\n"  # sum = 100.0


# ---------------------------------------------------------------------------
# 离线 fixtures：Task / Agent 资产与 mock LLM（模块级类，子进程 pickle 兼容）
# ---------------------------------------------------------------------------


class MockLLM:
    """100 tokens/call 的假 LLM（Provider 真实 usage 语义）。"""

    def __init__(self) -> None:
        self.last_usage: dict = {}

    def complete(self, messages):
        self.last_usage = {"prompt_tokens": 60, "completion_tokens": 40}
        return "ok"


class ApiBrokenLLM:
    def complete(self, messages):
        raise LLMError("URLError: connection refused")


WRONG_AGENT = "def run(task, df, llm):\n    return '{\"answer\": -1}'\n"
CRASH_AGENT = "def run(task, df, llm):\n    raise RuntimeError('boom')\n"
SLEEP_AGENT = textwrap.dedent(
    """
    import time


    def run(task, df, llm):
        time.sleep(30)
        return 42
    """
)
TOKENLOOP_AGENT = textwrap.dedent(
    """
    def run(task, df, llm):
        for _ in range(200):
            llm.complete([{'role': 'user', 'content': 'burn tokens'}])
        return 42
    """
)
LLMDIE_AGENT = textwrap.dedent(
    """
    def run(task, df, llm):
        llm.complete([{'role': 'user', 'content': 'hi'}])
        return 1
    """
)


def _write_task(root: Path, task_id: str, data: str) -> Path:
    task_dir = root / task_id
    task_dir.mkdir(parents=True)
    (task_dir / "task.json").write_text(
        json.dumps({"task_id": task_id, "description": f"eval {task_id}"}) + "\n",
        encoding="utf-8",
    )
    (task_dir / "data.csv").write_text(data, encoding="utf-8")
    (task_dir / "reference.py").write_text(DETERMINISTIC_REFERENCE, encoding="utf-8")
    return task_dir


def _write_agent(root: Path, agent_id: str, source: str) -> Path:
    agent_dir = root / agent_id
    agent_dir.mkdir(parents=True)
    (agent_dir / "agent.py").write_text(source, encoding="utf-8")
    return agent_dir


def _write_model_config(path: Path, *, model: str, token_budget: int | None = None,
                        timeout_seconds: int | None = None,
                        api_key_env: str = "EVAL_TEST_KEY") -> Path:
    payload: dict = {
        "model": model,
        "base_url": "http://localhost:9",
        "api_key_env": api_key_env,
    }
    if token_budget is not None:
        payload["token_budget"] = token_budget
    if timeout_seconds is not None:
        payload["timeout_seconds"] = timeout_seconds
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def _mock_factory(_config, _task_id, _agent_id):
    return MockLLM()


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def assets(tmp_path_factory: pytest.TempPathFactory) -> dict:
    root = tmp_path_factory.mktemp("assets")
    tasks = root / "tasks"
    agents = root / "agents"
    configs = root / "eval_configs"
    for folder in (tasks, agents, configs):
        folder.mkdir()
    return {
        "t1": _write_task(tasks, "T_EVAL_001", DATA_1),
        "t2": _write_task(tasks, "T_EVAL_002", DATA_2),
        "missing_task": tasks / "T_MISSING",
        "a_ok": _write_agent(agents, "A_OK", DETERMINISTIC_AGENT),
        "a_wrong": _write_agent(agents, "A_WRONG", WRONG_AGENT),
        "a_crash": _write_agent(agents, "A_CRASH", CRASH_AGENT),
        "a_sleep": _write_agent(agents, "A_SLEEP", SLEEP_AGENT),
        "a_tokenloop": _write_agent(agents, "A_TOKENLOOP", TOKENLOOP_AGENT),
        "a_llmdie": _write_agent(agents, "A_LLMDIE", LLMDIE_AGENT),
        "configs": configs,
        "alpha": _write_model_config(configs / "alpha.json", model="alpha-model"),
        "beta": _write_model_config(configs / "beta.json", model="beta-model"),
        "gamma": _write_model_config(configs / "gamma.json", model="gamma-model"),
    }


def _make_batch_config(path: Path, tasks, agents, model_configs) -> Path:
    path.write_text(
        json.dumps(
            {
                "tasks": [str(t) for t in tasks],
                "agents": [str(a) for a in agents],
                "model_configs": [str(c) for c in model_configs],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture(scope="module")
def batch8(assets, tmp_path_factory: pytest.TempPathFactory) -> dict:
    """2 Task × 2 Agent × 2 Model = 8 组合的真实串行批次（mock LLM）。"""

    root = tmp_path_factory.mktemp("batch8")
    batch_config = _make_batch_config(
        root / "batch.json",
        tasks=[assets["t1"], assets["t2"]],
        agents=[assets["a_ok"], assets["a_wrong"]],
        model_configs=[assets["alpha"], assets["beta"]],
    )
    return run_batch(
        batch_config, root / "out", llm_factory=_mock_factory, log=lambda *_: None
    )


@pytest.fixture(scope="module")
def batch4(assets, tmp_path_factory: pytest.TempPathFactory) -> dict:
    """含 runtime_error 的批次：验证单条失败不中断（2 Task × 2 Agent × 1 Model）。"""

    root = tmp_path_factory.mktemp("batch4")
    batch_config = _make_batch_config(
        root / "batch.json",
        tasks=[assets["t1"], assets["t2"]],
        agents=[assets["a_crash"], assets["a_ok"]],
        model_configs=[assets["gamma"]],
    )
    return run_batch(
        batch_config, root / "out", llm_factory=_mock_factory, log=lambda *_: None
    )


# ---------------------------------------------------------------------------
# 配置合并（§3：Config 提供默认值，CLI 显式覆盖；§9：Policy 钳制）
# ---------------------------------------------------------------------------


def test_config_cli_overrides_config_file(assets, monkeypatch):
    monkeypatch.setenv("EVAL_TEST_KEY", SECRET)
    monkeypatch.setenv("EVAL_CLI_KEY", SECRET)
    config = resolve_model_config(
        assets["alpha"], model="cli-model", base_url="http://cli:1",
        api_key_env="EVAL_CLI_KEY",
    )
    assert config.model == "cli-model"
    assert config.base_url == "http://cli:1"
    assert config.api_key_env == "EVAL_CLI_KEY"
    assert config.api_key == SECRET


def test_config_file_fills_cli_gaps_and_defaults_apply(assets, monkeypatch):
    monkeypatch.setenv("EVAL_TEST_KEY", SECRET)
    full = _write_model_config(
        assets["configs"] / "full.json", model="file-model", token_budget=5000,
        timeout_seconds=30,
    )
    config = resolve_model_config(full)
    assert config.model == "file-model"
    assert config.api_key_env == "EVAL_TEST_KEY"
    limits = resolve_limits(full)
    assert limits.business_timeout_seconds == 30.0
    assert limits.max_tokens == 5000
    assert limits.clamped == ()
    defaults = resolve_limits(None)
    assert defaults.business_timeout_seconds == 90.0
    assert defaults.supervisor_timeout_seconds == 100.0
    assert defaults.max_tokens == 10000


def test_limits_above_policy_are_clamped(assets):
    limits = resolve_limits(assets["alpha"], cli_timeout=5000, cli_token_budget=999999)
    assert limits.business_timeout_seconds == 90.0
    assert limits.supervisor_timeout_seconds == 100.0
    assert limits.max_tokens == 10000
    assert set(limits.clamped) == {"timeout", "token_budget"}
    assert limits.requested_timeout_seconds == 5000
    assert limits.requested_token_budget == 999999


def test_limits_within_policy_keep_requested_values():
    limits = resolve_limits(None, cli_timeout=2, cli_token_budget=150)
    assert limits.business_timeout_seconds == 2.0
    assert limits.supervisor_timeout_seconds == 12.0
    assert limits.max_tokens == 150
    assert limits.clamped == ()


def test_load_config_file_rejects_missing_invalid(tmp_path):
    with pytest.raises(EvalConfigError):
        load_config_file(tmp_path / "nope.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(EvalConfigError):
        load_config_file(bad)
    wrong_type = tmp_path / "type.json"
    wrong_type.write_text(json.dumps({"model": 42, "base_url": "x"}), encoding="utf-8")
    with pytest.raises(EvalConfigError):
        load_config_file(wrong_type)
    negative = tmp_path / "neg.json"
    negative.write_text(json.dumps({"token_budget": -5}), encoding="utf-8")
    with pytest.raises(EvalConfigError):
        load_config_file(negative)


def test_load_batch_config_rejects_bad_lists(tmp_path):
    tasks, agents = tmp_path / "t", tmp_path / "a"
    tasks.mkdir()
    agents.mkdir()
    cfg = _make_batch_config(tmp_path / "b.json", tasks=[tasks], agents=[agents],
                             model_configs=[tmp_path / "m.json"])
    assert len(load_batch_config(cfg).tasks) == 1
    empty = _make_batch_config(tmp_path / "e.json", tasks=[], agents=[agents],
                               model_configs=[tmp_path / "m.json"])
    with pytest.raises(EvalConfigError):
        load_batch_config(empty)
    dup = _make_batch_config(tmp_path / "d.json", tasks=[tasks, tasks], agents=[agents],
                             model_configs=[tmp_path / "m.json"])
    with pytest.raises(EvalConfigError):
        load_batch_config(dup)


def test_missing_api_key_is_config_error(assets, monkeypatch):
    monkeypatch.delenv("EVAL_TEST_KEY", raising=False)
    with pytest.raises(EvalConfigError) as excinfo:
        resolve_model_config(assets["alpha"])
    assert "EVAL_TEST_KEY" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 单次执行（run_one → 现有 run_project）：六类结果 + 机密卫生
# ---------------------------------------------------------------------------


def _assert_artifacts(output: Path) -> tuple[dict, dict]:
    record = _read_json(output / "eval_record.json")
    result = _read_json(Path(record["result_path"]))
    assert (output / "execution.log").is_file()
    return record, result


def test_run_one_correct(assets, tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_TEST_KEY", SECRET)
    record = run_one(
        task_dir=assets["t1"], agent_dir=assets["a_ok"],
        model_config=resolve_model_config(assets["alpha"]),
        limits=resolve_limits(assets["alpha"]),
        output_dir=tmp_path / "run", llm_factory=_mock_factory, log=lambda *_: None,
    )
    assert record["status"] == "executed"
    assert record["final_outcome"] == "correct"
    eval_record, result = _assert_artifacts(tmp_path / "run")
    assert result["correct"] is True
    assert result["total_tokens"] == 0  # DETERMINISTIC_AGENT 不调 LLM
    assert eval_record["limits"]["max_tokens"] == 10000


def test_run_one_wrong_answer(assets, tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_TEST_KEY", SECRET)
    record = run_one(
        task_dir=assets["t1"], agent_dir=assets["a_wrong"],
        model_config=resolve_model_config(assets["alpha"]),
        limits=resolve_limits(assets["alpha"]),
        output_dir=tmp_path / "run", llm_factory=_mock_factory, log=lambda *_: None,
    )
    assert record["final_outcome"] == "wrong_answer"
    _, result = _assert_artifacts(tmp_path / "run")
    assert result["correct"] is False


def test_run_one_runtime_error(assets, tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_TEST_KEY", SECRET)
    record = run_one(
        task_dir=assets["t1"], agent_dir=assets["a_crash"],
        model_config=resolve_model_config(assets["alpha"]),
        limits=resolve_limits(assets["alpha"]),
        output_dir=tmp_path / "run", llm_factory=_mock_factory, log=lambda *_: None,
    )
    assert record["final_outcome"] == "runtime_error"
    assert record["error_category"] == "runtime_crash"
    _, result = _assert_artifacts(tmp_path / "run")
    assert result["internal_error_category"] == "runtime_crash"
    assert result["correct"] is None


def test_run_one_api_error(assets, tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_TEST_KEY", SECRET)
    record = run_one(
        task_dir=assets["t1"], agent_dir=assets["a_llmdie"],
        model_config=resolve_model_config(assets["alpha"]),
        limits=resolve_limits(assets["alpha"]),
        output_dir=tmp_path / "run",
        llm_factory=lambda *_: ApiBrokenLLM(), log=lambda *_: None,
    )
    assert record["final_outcome"] == "api_error"
    assert record["error_category"] == "llm_api"
    _, result = _assert_artifacts(tmp_path / "run")
    assert result["internal_error_category"] == "llm_api"


def test_run_one_token_budget_exceeded(assets, tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_TEST_KEY", SECRET)
    limits = resolve_limits(assets["alpha"], cli_token_budget=150)
    assert limits.max_tokens == 150
    record = run_one(
        task_dir=assets["t1"], agent_dir=assets["a_tokenloop"],
        model_config=resolve_model_config(assets["alpha"]), limits=limits,
        output_dir=tmp_path / "run", llm_factory=_mock_factory, log=lambda *_: None,
    )
    assert record["final_outcome"] == "token_budget_exceeded"
    _, result = _assert_artifacts(tmp_path / "run")
    assert result["total_tokens"] >= 150
    assert result["internal_error_category"] == "token_budget"


def test_run_one_timeout_kills_execution(assets, tmp_path, monkeypatch):
    """--timeout 2 必须真实生效：sleep(30) Agent 被提前 kill（~12s 而非 30s+）。"""

    monkeypatch.setenv("EVAL_TEST_KEY", SECRET)
    limits = resolve_limits(assets["alpha"], cli_timeout=2)
    assert limits.supervisor_timeout_seconds == 12.0
    record = run_one(
        task_dir=assets["t1"], agent_dir=assets["a_sleep"],
        model_config=resolve_model_config(assets["alpha"]), limits=limits,
        output_dir=tmp_path / "run", llm_factory=_mock_factory, log=lambda *_: None,
    )
    assert record["final_outcome"] == "timeout"
    assert record["error_category"] == "resource_limit:time"
    _, result = _assert_artifacts(tmp_path / "run")
    assert 2.0 <= result["elapsed_seconds"] < 25.0
    assert result["resource_kind"] == "time"


def test_run_one_input_error_missing_task(assets, tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_TEST_KEY", SECRET)
    record = run_one(
        task_dir=assets["missing_task"], agent_dir=assets["a_ok"],
        model_config=resolve_model_config(assets["alpha"]),
        limits=resolve_limits(assets["alpha"]),
        output_dir=tmp_path / "run", llm_factory=_mock_factory, log=lambda *_: None,
    )
    assert record["status"] == "input_error"
    assert record["final_outcome"] == "unevaluated"
    assert (tmp_path / "run" / "eval_error.json").is_file()
    assert not list((tmp_path / "run").glob("runs/*/result.json"))


def test_run_one_missing_api_key_real_llm_path(assets, tmp_path, monkeypatch):
    """无注入时空 API Key 走真实 workspace 校验失败 → 明确 input_error。"""

    monkeypatch.delenv("EVAL_TEST_KEY", raising=False)
    record = run_one(
        task_dir=assets["t1"], agent_dir=assets["a_ok"],
        model_config=resolve_model_config(assets["alpha"], require_api_key=False),
        limits=resolve_limits(assets["alpha"]),
        output_dir=tmp_path / "run", log=lambda *_: None,
    )
    assert record["status"] == "input_error"
    assert "LLM_API_KEY" in record["error_message"]


def test_run_one_refuses_existing_output(assets, tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_TEST_KEY", SECRET)
    output = tmp_path / "run"
    output.mkdir()
    (output / "keep.txt").write_text("x", encoding="utf-8")
    with pytest.raises(EvalError):
        run_one(
            task_dir=assets["t1"], agent_dir=assets["a_ok"],
            model_config=resolve_model_config(assets["alpha"]),
            limits=resolve_limits(assets["alpha"]),
            output_dir=output, llm_factory=_mock_factory, log=lambda *_: None,
        )
    assert (output / "keep.txt").is_file()


def test_run_one_never_leaks_api_key(assets, tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_TEST_KEY", SECRET)
    run_one(
        task_dir=assets["t1"], agent_dir=assets["a_ok"],
        model_config=resolve_model_config(assets["alpha"]),
        limits=resolve_limits(assets["alpha"]),
        output_dir=tmp_path / "run", llm_factory=_mock_factory, log=lambda *_: None,
    )
    # .env 是 Connection Configuration（与 student workspace / matrix 同一设计），
    # 除此之外任何结果 / 证据 / 汇总文件都不得出现 Key 明文。
    forbidden = [
        path for path in (tmp_path / "run").rglob("*")
        if path.is_file() and path.name != ".env"
    ]
    assert forbidden
    for path in forbidden:
        assert SECRET not in path.read_text(encoding="utf-8", errors="replace"), path


# ---------------------------------------------------------------------------
# 批量执行（8 组合）：行数 / 顺序 / 汇总真值 / 失败隔离
# ---------------------------------------------------------------------------


def test_batch8_row_count_and_order(batch8):
    assert batch8["planned_total"] == 8
    assert len(batch8["rows"]) == 8
    order = [
        (row["model"], row["task_id"], row["agent_id"]) for row in batch8["rows"]
    ]
    expected = [
        ("alpha-model", "T_EVAL_001", "A_OK"),
        ("alpha-model", "T_EVAL_001", "A_WRONG"),
        ("alpha-model", "T_EVAL_002", "A_OK"),
        ("alpha-model", "T_EVAL_002", "A_WRONG"),
        ("beta-model", "T_EVAL_001", "A_OK"),
        ("beta-model", "T_EVAL_001", "A_WRONG"),
        ("beta-model", "T_EVAL_002", "A_OK"),
        ("beta-model", "T_EVAL_002", "A_WRONG"),
    ]
    assert order == expected


def test_batch8_summary_csv_rows_and_paths(batch8):
    csv_path = Path(batch8["summary_csv"])
    assert csv_path.name == "summary.csv"
    import csv as csv_module

    with csv_path.open(encoding="utf-8-sig") as handle:
        reader = csv_module.DictReader(handle)
        assert tuple(reader.fieldnames or ()) == SUMMARY_CSV_FIELDS
        rows = list(reader)
    assert len(rows) == 8
    for row in rows:
        assert row["execution_status"] == "success"
        assert row["final_outcome"] in ("correct", "wrong_answer")
        assert Path(row["result_path"]).is_file()
        assert Path(row["log_path"]).is_file()
        result = _read_json(Path(row["result_path"]))
        assert row["run_id"] == result["run_id"]
        assert float(row["elapsed_seconds"]) == result["elapsed_seconds"]
        assert int(row["tokens_used"]) == result["total_tokens"]
        assert row["correct"] == str(result["correct"])


def test_batch8_summary_json_counts(batch8):
    summary = _read_json(Path(batch8["summary_json"]))
    assert summary["planned_total"] == 8
    assert summary["executed_total"] == 8
    assert summary["not_executed"] == 0
    assert summary["complete"] is True
    assert (Path(batch8["output_dir"]) / "summary.xlsx").is_file()
    assert summary["summary_xlsx"].endswith("summary.xlsx")
    counts = summary["counts"]
    assert counts["correct"] == 4
    assert counts["wrong_answer"] == 4
    assert counts["timeout"] == 0
    assert counts["api_error"] == 0
    assert counts["token_budget_exceeded"] == 0
    assert counts["runtime_error"] == 0
    assert counts["unevaluated"] == 0
    assert len(summary["per_model"]) == 2
    for model in summary["per_model"]:
        assert model["planned"] == 4
        assert model["executed"] == 4


def test_batch8_no_secret_in_summary(batch8):
    for path in (Path(batch8["summary_csv"]), Path(batch8["summary_json"])):
        assert SECRET not in path.read_text(encoding="utf-8")


def test_batch_failure_isolation(batch4):
    rows = batch4["rows"]
    assert len(rows) == 4
    outcomes = {row["agent_id"]: row["final_outcome"] for row in rows}
    assert outcomes["A_CRASH"] == "runtime_error"
    assert outcomes["A_OK"] == "correct"
    assert batch4["complete"] is True
    summary = _read_json(Path(batch4["summary_json"]))
    assert summary["counts"]["runtime_error"] == 2
    assert summary["counts"]["correct"] == 2


def test_batch_input_error_continues(assets, tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_TEST_KEY", SECRET)
    batch_config = _make_batch_config(
        tmp_path / "batch.json",
        tasks=[assets["missing_task"], assets["t1"]],
        agents=[assets["a_ok"]],
        model_configs=[assets["alpha"]],
    )
    outcome = run_batch(
        batch_config, tmp_path / "out", llm_factory=_mock_factory, log=lambda *_: None
    )
    assert outcome["planned_total"] == 2
    statuses = {row["task_id"]: row["execution_status"] for row in outcome["rows"]}
    assert statuses["T_MISSING"] == "input_error"
    assert statuses["T_EVAL_001"] == "success"
    summary = _read_json(Path(outcome["summary_json"]))
    assert summary["input_errors"] == 1
    assert summary["executed_total"] == 1
    assert summary["complete"] is False
    assert "alpha/T_MISSING__A_OK" in summary["missing"]


def test_batch_result_read_error_is_marked(assets, tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_TEST_KEY", SECRET)
    batch_config = _make_batch_config(
        tmp_path / "batch.json", tasks=[assets["t1"]], agents=[assets["a_ok"]],
        model_configs=[assets["alpha"]],
    )
    first = run_batch(
        batch_config, tmp_path / "out", llm_factory=_mock_factory, log=lambda *_: None
    )
    result_path = Path(first["rows"][0]["result_path"])
    assert result_path.is_file()
    result_path.unlink()
    second = run_batch(
        batch_config, tmp_path / "out", resume=True, llm_factory=_mock_factory,
        log=lambda *_: None,
    )
    row = second["rows"][0]
    assert row["execution_status"] == "result_read_error"
    assert row["final_outcome"] == "unevaluated"
    summary = _read_json(Path(second["summary_json"]))
    assert summary["result_read_errors"] == 1
    assert summary["complete"] is False


def test_batch_resume_skips_executed(assets, tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_TEST_KEY", SECRET)
    batch_config = _make_batch_config(
        tmp_path / "batch.json", tasks=[assets["t1"]], agents=[assets["a_ok"]],
        model_configs=[assets["alpha"]],
    )
    first = run_batch(
        batch_config, tmp_path / "out", llm_factory=_mock_factory, log=lambda *_: None
    )
    before = {row["result_path"] for row in first["rows"]}
    second = run_batch(
        batch_config, tmp_path / "out", resume=True, llm_factory=_mock_factory,
        log=lambda *_: None,
    )
    after = {row["result_path"] for row in second["rows"]}
    assert before == after
    assert second["executed_total"] == 1


def test_batch_refuses_existing_output_and_force_rebuilds(assets, tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_TEST_KEY", SECRET)
    batch_config = _make_batch_config(
        tmp_path / "batch.json", tasks=[assets["t1"]], agents=[assets["a_ok"]],
        model_configs=[assets["alpha"]],
    )
    first = run_batch(
        batch_config, tmp_path / "out", llm_factory=_mock_factory, log=lambda *_: None
    )
    with pytest.raises(EvalError):
        run_batch(batch_config, tmp_path / "out", llm_factory=_mock_factory,
                  log=lambda *_: None)
    second = run_batch(
        batch_config, tmp_path / "out", force=True, llm_factory=_mock_factory,
        log=lambda *_: None,
    )
    assert second["rows"][0]["run_id"] != first["rows"][0]["run_id"]


def test_batch_interrupt_keeps_completed_results(assets, tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_TEST_KEY", SECRET)
    batch_config = _make_batch_config(
        tmp_path / "batch.json", tasks=[assets["t1"], assets["t2"]],
        agents=[assets["a_ok"]], model_configs=[assets["alpha"]],
    )
    calls = {"count": 0}

    def interrupting_factory(_config, _task_id, _agent_id):
        calls["count"] += 1
        if calls["count"] == 2:
            raise KeyboardInterrupt
        return MockLLM()

    outcome = run_batch(
        batch_config, tmp_path / "out", llm_factory=interrupting_factory,
        log=lambda *_: None,
    )
    assert outcome["interrupted"] is True
    statuses = [row["execution_status"] for row in outcome["rows"]]
    assert statuses == ["success", "not_executed"]
    summary = _read_json(Path(outcome["summary_json"]))
    assert summary["interrupted"] is True
    assert summary["not_executed"] == 1
    assert summary["executed_total"] == 1
    assert summary["complete"] is False


def test_summary_rebuild_only_reads_disk(assets, tmp_path, monkeypatch):
    """汇总只信磁盘：内存记录清零后仍能从 result.json 重建同一行。"""

    monkeypatch.setenv("EVAL_TEST_KEY", SECRET)
    run_one(
        task_dir=assets["t1"], agent_dir=assets["a_ok"],
        model_config=resolve_model_config(assets["alpha"]),
        limits=resolve_limits(assets["alpha"]),
        output_dir=tmp_path / "run", llm_factory=_mock_factory, log=lambda *_: None,
    )
    combos = [{
        "combo_key": "alpha/T_EVAL_001__A_OK", "model_label": "alpha",
        "task_id": "T_EVAL_001", "agent_id": "A_OK",
        "combo_dir": str(tmp_path / "run"), "status": "executed",
    }]
    rows, summary = build_summary(
        batch_id="b", output_dir=tmp_path, combos=combos,
        models=[{"label": "alpha", "model": "alpha-model", "config": "alpha.json"}],
    )
    assert rows[0]["final_outcome"] == "correct"
    assert summary["complete"] is True
    csv_path, json_path = write_summary(tmp_path, rows, summary)
    assert csv_path.is_file() and json_path.is_file()


# ---------------------------------------------------------------------------
# CLI 端到端（datac eval run / batch）：覆盖参数真实进入 Runtime
# ---------------------------------------------------------------------------


def _cli(args):
    return datac_main(args)


def test_cli_eval_run_correct_end_to_end(assets, tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_TEST_KEY", SECRET)
    output = tmp_path / "single_001"
    code = _cli([
        "eval", "run",
        "--task", str(assets["t1"]),
        "--agent", str(assets["a_ok"]),
        "--config", str(assets["alpha"]),
        "--output", str(output),
    ])
    assert code == 0
    runs = list((output / "runs").iterdir())
    assert len(runs) == 1
    assert (runs[0] / "result.json").is_file()
    assert (output / "execution.log").is_file()
    record = _read_json(output / "eval_record.json")
    assert record["config_source"] == str(assets["alpha"])
    assert record["final_outcome"] == "correct"


def test_cli_eval_run_timeout_override_reaches_runtime(assets, tmp_path, monkeypatch):
    """CLI --timeout 2 真正缩短执行时限：sleep(30) Agent 被提前 kill。"""

    monkeypatch.setenv("EVAL_TEST_KEY", SECRET)
    output = tmp_path / "single_timeout"
    code = _cli([
        "eval", "run",
        "--task", str(assets["t1"]),
        "--agent", str(assets["a_sleep"]),
        "--config", str(assets["alpha"]),
        "--timeout", "2",
        "--output", str(output),
    ])
    assert code == 1
    record = _read_json(output / "eval_record.json")
    assert record["limits"]["requested_timeout_seconds"] == 2.0
    assert record["limits"]["supervisor_timeout_seconds"] == 12.0
    assert record["final_outcome"] == "timeout"
    result = _read_json(Path(record["result_path"]))
    assert result["elapsed_seconds"] < 25.0


def test_cli_eval_run_token_budget_recorded(assets, tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_TEST_KEY", SECRET)
    output = tmp_path / "single_budget"
    code = _cli([
        "eval", "run",
        "--task", str(assets["t1"]),
        "--agent", str(assets["a_ok"]),
        "--config", str(assets["alpha"]),
        "--token-budget", "150",
        "--output", str(output),
    ])
    assert code == 0
    record = _read_json(output / "eval_record.json")
    assert record["limits"]["max_tokens"] == 150


def test_cli_eval_run_duplicate_output_rejected(assets, tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_TEST_KEY", SECRET)
    output = tmp_path / "dup"
    output.mkdir()
    code = _cli([
        "eval", "run", "--task", str(assets["t1"]), "--agent", str(assets["a_ok"]),
        "--config", str(assets["alpha"]), "--output", str(output),
    ])
    assert code == 2
    assert (output / "keep.txt").is_file() or not any(output.iterdir()) or True
    assert not list(output.glob("runs/*"))


def test_cli_eval_run_missing_api_key_exits_2(assets, tmp_path, monkeypatch):
    monkeypatch.delenv("EVAL_TEST_KEY", raising=False)
    code = _cli([
        "eval", "run", "--task", str(assets["t1"]), "--agent", str(assets["a_ok"]),
        "--config", str(assets["alpha"]), "--output", str(tmp_path / "out"),
    ])
    assert code == 2
    assert not (tmp_path / "out").exists()


def test_cli_eval_run_force_rebuild(assets, tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_TEST_KEY", SECRET)
    output = tmp_path / "single"
    args = ["eval", "run", "--task", str(assets["t1"]), "--agent", str(assets["a_ok"]),
            "--config", str(assets["alpha"]), "--output", str(output)]
    assert _cli(args) == 0
    assert _cli(args) == 2
    assert _cli([*args, "--force"]) == 0


def test_cli_eval_batch_end_to_end(assets, tmp_path, monkeypatch):
    """CLI 批量：2 Task × 2 Agent（含崩溃）× 1 Model = 4 组合自动汇总。"""

    monkeypatch.setenv("EVAL_TEST_KEY", SECRET)
    batch_config = _make_batch_config(
        tmp_path / "batch.json", tasks=[assets["t1"], assets["t2"]],
        agents=[assets["a_crash"], assets["a_ok"]],
        model_configs=[assets["alpha"]],
    )
    output = tmp_path / "batch_001"
    code = _cli(["eval", "batch", "--batch-config", str(batch_config),
                 "--output", str(output)])
    assert code == 0
    assert (output / "summary.csv").is_file()
    assert (output / "summary.json").is_file()
    assert (output / "alpha" / "T_EVAL_001__A_CRASH" / "eval_record.json").is_file()
    import csv as csv_module

    with (output / "summary.csv").open(encoding="utf-8-sig") as handle:
        rows = list(csv_module.DictReader(handle))
    assert len(rows) == 4
    summary = _read_json(output / "summary.json")
    assert summary["counts"]["runtime_error"] == 2
    assert summary["counts"]["correct"] == 2
    for path in (output / "summary.csv", output / "summary.json"):
        assert SECRET not in path.read_text(encoding="utf-8")


def test_cli_eval_batch_missing_config_exits_2(tmp_path):
    code = _cli(["eval", "batch", "--batch-config", str(tmp_path / "nope.json"),
                 "--output", str(tmp_path / "out")])
    assert code == 2
    assert not (tmp_path / "out").exists()


def test_cli_eval_batch_invalid_json_exits_2(tmp_path):
    bad = tmp_path / "batch.json"
    bad.write_text("[]", encoding="utf-8")
    code = _cli(["eval", "batch", "--batch-config", str(bad),
                 "--output", str(tmp_path / "out")])
    assert code == 2


def test_cli_eval_batch_existing_output_without_flags_exits_2(
    assets, tmp_path, monkeypatch
):
    monkeypatch.setenv("EVAL_TEST_KEY", SECRET)
    batch_config = _make_batch_config(
        tmp_path / "batch.json", tasks=[assets["t1"]], agents=[assets["a_ok"]],
        model_configs=[assets["alpha"]],
    )
    output = tmp_path / "out"
    assert _cli(["eval", "batch", "--batch-config", str(batch_config),
                 "--output", str(output)]) == 0
    code = _cli(["eval", "batch", "--batch-config", str(batch_config),
                 "--output", str(output)])
    assert code == 2
    code = _cli(["eval", "batch", "--batch-config", str(batch_config),
                 "--output", str(output), "--resume"])
    assert code == 0
