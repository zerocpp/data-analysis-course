"""Benchmark Matrix 验收（全离线 fixture，真实 run core）。

覆盖：Initial Suite 契约（12 FM / 每 FM 2 Task / 共 24 / 5 Agent / 120 组合）、
离线 fixture 完整 Matrix（24×5 = 120 条真实 run core 记录）、每 Agent /
每 Task / 每 FM 计数、matrix_records.csv + jsonl 双输出、artifacts 真实
存在、label 区分 CORRECT / WRONG / INTERNAL_*、suite 可扩充（新 Task 无需
改 Runner）、历史 Experiment 不覆盖。
"""

from __future__ import annotations

import csv
import json
import textwrap
from pathlib import Path

import pytest

from codesetarena.constants import (
    FAILURE_MODE_COUNT,
    FAILURE_MODE_OPTIONS,
    EXPECTED_FAILURE_MODE_CODES,
)
from codesetarena.main.budget import BudgetExceeded
from codesetarena.main.llm import LLMError
from codesetarena.main.matrix import (
    MatrixError,
    load_suite,
    matrix_summary,
    run_matrix,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_SUITE = REPO_ROOT / "benchmark" / "suite"

DATA = "region,amount\neast,10\nwest,20\n"


class ApiBrokenLLM:
    def complete(self, messages):
        raise LLMError("URLError: connection refused")


class TokenBrokenLLM:
    def complete(self, messages):
        raise BudgetExceeded("token budget exhausted")


def _fixture_suite(tmp_path: Path) -> Path:
    """12 FM × 2 Task = 24 Task，5 个行为互异的微型 Agent（全部离线）。"""

    suite = tmp_path / "suite"
    (suite / "tasks").mkdir(parents=True)
    (suite / "agents").mkdir(parents=True)

    agents = {
        "A": "def run(task, df, llm):\n    return 42\n",  # CORRECT
        "B": "def run(task, df, llm):\n    return 999\n",  # WRONG → analysis_failure
        "C": "def run(task, df, llm):\n    raise RuntimeError('boom')\n",  # INTERNAL_RUNTIME
        "D": (
            "def run(task, df, llm):\n"
            "    llm.complete([{'role': 'user', 'content': 'hi'}])\n"
            "    return 1\n"
        ),  # 配 llm_factory 按 agent 分发
        "E": (
            "def run(task, df, llm):\n"
            "    llm.complete([{'role': 'user', 'content': 'hi'}])\n"
            "    return 1\n"
        ),
    }
    for agent_id, source in agents.items():
        agent_dir = suite / "agents" / f"agent_{agent_id}"
        agent_dir.mkdir()
        (agent_dir / "agent.py").write_text(source, encoding="utf-8")

    for fm, _name in FAILURE_MODE_OPTIONS:
        for index in (1, 2):
            task_id = f"T_{fm}_{index:02d}"
            task_dir = suite / "tasks" / task_id
            task_dir.mkdir()
            (task_dir / "task.json").write_text(
                json.dumps(
                    {
                        "task_id": task_id,
                        "title": f"fixture {task_id}",
                        "expected_failure_mode": fm,
                        "description": "sum(amount)",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (task_dir / "data.csv").write_text(DATA, encoding="utf-8")
            (task_dir / "reference.py").write_text(
                textwrap.dedent(
                    """
                    import pandas as pd


                    def reference(df):
                        return 42
                    """
                ),
                encoding="utf-8",
            )

    (suite / "suite.json").write_text(
        json.dumps(
            {
                "schema": "benchmark-suite-v1",
                "expected_agent_count": 5,
                "agents": [
                    {"agent_id": aid, "agent_name": f"Fixture {aid}",
                     "path": f"agents/agent_{aid}/agent.py"}
                    for aid in ("A", "B", "C", "D", "E")
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return suite


def _dispatch_llm_factory(agent, task):
    if agent["agent_id"] == "D":
        return ApiBrokenLLM()
    if agent["agent_id"] == "E":
        return TokenBrokenLLM()
    return None


# ---------------------------------------------------------------------------
# 23-27. Initial Suite 契约：冻结 suite = 12 FM（早于 FM10/FM13 扩展）/ 2 Task 每个 / 24 Task / 5 Agent / 120 组合
# ---------------------------------------------------------------------------


def test_initial_suite_real_suite_covers_legacy_12_of_14():
    """taxonomy 现为 14 类；冻结的 benchmark/suite 早于 FM10/FM13 扩展，覆盖旧 12 类。"""

    assert FAILURE_MODE_COUNT == 14
    assert {"FM10", "FM13"} <= set(EXPECTED_FAILURE_MODE_CODES)
    agents, tasks, _ = load_suite(REAL_SUITE)
    modes = {t["expected_failure_mode"] for t in tasks}
    assert len(modes) == 12
    assert modes < set(EXPECTED_FAILURE_MODE_CODES)
    assert modes.isdisjoint({"FM10", "FM13"})


def test_initial_suite_two_tasks_per_failure_mode():
    _, tasks, _ = load_suite(REAL_SUITE)
    by_fm: dict[str, int] = {}
    for task in tasks:
        by_fm[task["expected_failure_mode"]] = by_fm.get(task["expected_failure_mode"], 0) + 1
    assert len(by_fm) == 12
    assert all(count == 2 for count in by_fm.values())


def test_initial_suite_24_tasks_no_duplicates():
    _, tasks, _ = load_suite(REAL_SUITE)
    ids = [t["task_id"] for t in tasks]
    assert len(tasks) == 24
    assert len(ids) == len(set(ids))


def test_initial_suite_exactly_5_agents():
    agents, _, _ = load_suite(REAL_SUITE)
    assert len(agents) == 5
    assert [a["agent_id"] for a in agents] == ["A", "B", "C", "D", "E"]


def test_initial_suite_matrix_combinations_equal_120():
    agents, tasks, _ = load_suite(REAL_SUITE)
    assert len(agents) * len(tasks) == 120


# ---------------------------------------------------------------------------
# 离线完整 Matrix：24 × 5 = 120 条真实 run core 记录
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def full_matrix(tmp_path_factory: pytest.TempPathFactory) -> dict:
    suite = _fixture_suite(tmp_path_factory.mktemp("matrix"))
    return run_matrix(suite, llm_factory=_dispatch_llm_factory, log=lambda *_: None)


def test_full_matrix_140_records(full_matrix):
    summary = full_matrix["summary"]
    assert summary["expected_runs"] == 140
    assert summary["actual_runs"] == 140
    assert summary["complete"] is True


def test_full_matrix_per_agent_28_runs(full_matrix):
    assert full_matrix["summary"]["runs_per_agent"] == {
        "A": 28, "B": 28, "C": 28, "D": 28, "E": 28,
    }


def test_full_matrix_per_task_5_runs(full_matrix):
    per_task = full_matrix["summary"]["runs_per_task"]
    assert len(per_task) == 28
    assert set(per_task.values()) == {5}


def test_full_matrix_per_failure_mode_10_runs(full_matrix):
    per_fm = full_matrix["summary"]["runs_per_failure_mode"]
    assert per_fm == {fm: 10 for fm in EXPECTED_FAILURE_MODE_CODES}


def test_full_matrix_labels_distinguish_all_outcomes(full_matrix):
    labels = full_matrix["summary"]["outcome_labels"]
    assert labels == {
        "CORRECT": 28,
        "WRONG": 28,
        "INTERNAL_API": 28,
        "INTERNAL_TOKEN": 28,
        "INTERNAL_RUNTIME": 28,
    }


def test_matrix_records_csv_and_jsonl(full_matrix):
    csv_path = Path(full_matrix["csv_path"])
    jsonl_path = Path(full_matrix["jsonl_path"])
    with csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 140
    assert set(rows[0]) == {
        "experiment_id", "run_id", "agent_id", "task_id", "expected_failure_mode",
        "outcome_label", "result", "declared_gold_answer", "correct", "outcome_type",
        "internal_error_category", "internal_error_detail", "termination_source",
        "resource_kind", "elapsed_seconds", "total_tokens", "artifacts_path",
    }
    lines = jsonl_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 140
    assert json.loads(lines[0])["run_id"] == rows[0]["run_id"]


def test_matrix_every_record_has_real_artifacts(full_matrix):
    for row in full_matrix["records"]:
        assert Path(row["artifacts_path"]).is_file(), row["run_id"]


def test_matrix_records_distinguish_internal_categories(full_matrix):
    by_label: dict[str, set[str | None]] = {}
    for row in full_matrix["records"]:
        by_label.setdefault(row["outcome_label"], set()).add(row["internal_error_category"])
    assert by_label["WRONG"] == {None}
    assert by_label["CORRECT"] == {None}
    assert by_label["INTERNAL_API"] == {"llm_api"}
    assert by_label["INTERNAL_TOKEN"] == {"token_budget"}
    assert by_label["INTERNAL_RUNTIME"] == {"runtime_crash"}


# ---------------------------------------------------------------------------
# 扩充与隔离
# ---------------------------------------------------------------------------


def test_suite_expansion_needs_no_runner_change(tmp_path_factory: pytest.TempPathFactory):
    """新增 T_FM01_03 只需放入 task pool：发现 25 个 Task，不改任何代码。"""

    suite = _fixture_suite(tmp_path_factory.mktemp("expand"))
    extra = suite / "tasks" / "T_FM01_03"
    extra.mkdir()
    (extra / "task.json").write_text(
        json.dumps({"task_id": "T_FM01_03", "expected_failure_mode": "FM01"}) + "\n",
        encoding="utf-8",
    )
    (extra / "data.csv").write_text(DATA, encoding="utf-8")
    (extra / "reference.py").write_text(
        "import pandas as pd\n\n\ndef reference(df):\n    return 42\n", encoding="utf-8"
    )
    _, tasks, _ = load_suite(suite)
    assert len(tasks) == 29
    with pytest.raises(MatrixError):
        from codesetarena.main.matrix import validate_suite_tasks

        validate_suite_tasks(tasks)  # Initial 契约（2/FM）不再满足，属预期


def test_matrix_never_overwrites_history(tmp_path: Path):
    suite = _fixture_suite(tmp_path / "suite")
    results_root = tmp_path / "results"
    first = run_matrix(
        suite, results_root=results_root, experiment_id="EXP_TEST",
        llm_factory=_dispatch_llm_factory, log=lambda *_: None,
    )
    with pytest.raises(MatrixError):
        run_matrix(
            suite, results_root=results_root, experiment_id="EXP_TEST",
            llm_factory=_dispatch_llm_factory, log=lambda *_: None,
        )
    assert first["experiment_id"] == "EXP_TEST"


def test_matrix_summary_math(tmp_path: Path):
    rows = [
        {"agent_id": "A", "task_id": "T1", "expected_failure_mode": "FM01",
         "outcome_label": "CORRECT"},
        {"agent_id": "A", "task_id": "T2", "expected_failure_mode": "FM01",
         "outcome_label": "WRONG"},
        {"agent_id": "B", "task_id": "T1", "expected_failure_mode": "FM01",
         "outcome_label": "INTERNAL_API"},
    ]
    summary = matrix_summary(rows)
    assert summary == {
        "expected_runs": 4,
        "actual_runs": 3,
        "complete": False,
        "agents": ["A", "B"],
        "tasks": ["T1", "T2"],
        "runs_per_agent": {"A": 2, "B": 1},
        "runs_per_task": {"T1": 2, "T2": 1},
        "runs_per_failure_mode": {"FM01": 3},
        "outcome_labels": {"CORRECT": 1, "INTERNAL_API": 1, "WRONG": 1},
        "generated_at": summary["generated_at"],
    }


def test_real_suite_agents_are_copies_of_canonical_sources():
    """冻结 suite 的 5 个 Agent 来自仓库规范源文件（不发明新 Agent）。"""

    for agent_id in ("A", "B", "C", "D", "E"):
        path = REAL_SUITE / "agents" / f"agent_{agent_id}" / "agent.py"
        assert path.is_file()
        assert "def run(" in path.read_text(encoding="utf-8")
