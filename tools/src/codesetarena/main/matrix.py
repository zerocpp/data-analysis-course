"""Benchmark Matrix Runner —— Teacher / Backend 后台工具（学生不可见）。

学生侧永远只有 ``run`` / ``export`` 两个命令；Matrix 是另一层：把冻结
suite 里的每个 (Agent × Task) 组合组装成 **独立 fresh workspace**，然后
逐个调用与学生完全相同的 ``run_project`` 执行核心（Single Run Logic，
不复制第二套执行 / LLM / budget / sandbox / 判分实现）。

    suite/
    ├── suite.json              # schema + 显式 5 Agent + LLM 课程级配置
    ├── agents/agent_<ID>/agent.py
    └── tasks/<task_id>/{task.json,data.csv,reference.py}

    results/EXP_<ts>/
    ├── matrix_records.jsonl    # machine-readable source of truth
    ├── matrix_records.csv      # human audit table
    └── runs/<agent_id>__<task_id>/<run_id>/   # 每 run 独立 artifacts

隔离保证：每个组合独立 workspace / run_id / 子进程 / budget / token 计数；
不同 run 之间不共享任何可变状态。历史 Experiment 永不覆盖（时间戳目录）。
suite 可持续扩充：新增 Task 包只需放入 tasks/，不改任何 Runner 代码。
"""

from __future__ import annotations

import csv
import json
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from ..constants import FAILURE_MODE_COUNT
from .budget import Budget
from .outcome import matrix_label
from .project import parse_env_file
from .runner import run_project
from .validation import _reference_limits, _validate_expected_failure, check_reference_source

MATRIX_SCHEMA = "benchmark-matrix-v1"
SUITE_FILENAME = "suite.json"
AGENTS_DIRNAME = "agents"
TASKS_DIRNAME = "tasks"
RESULTS_DIRNAME = "results"
RUNS_DIRNAME = "runs"
REQUIRED_TASK_FILES = ("task.json", "data.csv", "reference.py")
AGENT_FILE = "agent.py"
CSV_FIELDS = (
    "experiment_id",
    "run_id",
    "agent_id",
    "task_id",
    "expected_failure_mode",
    "outcome_label",
    "result",
    "declared_gold_answer",
    "correct",
    "outcome_type",
    "internal_error_category",
    "internal_error_detail",
    "termination_source",
    "resource_kind",
    "elapsed_seconds",
    "total_tokens",
    "artifacts_path",
)


class MatrixError(RuntimeError):
    """Suite 发现 / 校验失败（禁止带病执行 Matrix）。"""


# ---------------------------------------------------------------------------
# Suite 发现与校验
# ---------------------------------------------------------------------------


def load_suite(suite_dir: str | Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """返回 (agents, tasks, llm_spec_source)；任何发现错误立即整体失败。"""

    root = Path(suite_dir)
    config_path = root / SUITE_FILENAME
    if not config_path.is_file():
        raise MatrixError(f"缺少 {SUITE_FILENAME}: {config_path}")
    config = json.loads(config_path.read_text(encoding="utf-8"))

    agents = _discover_agents(root, config)
    tasks = _discover_tasks(root)
    # Initial Frozen Suite 声明了 tasks_per_failure_mode（12×2=24）；未来扩充
    # 的 suite 不声明此键，只要求 FM 编码合法（Runner 不硬编码 24 forever）。
    if config.get("tasks_per_failure_mode") is not None:
        validate_suite_tasks(
            tasks,
            int(config["tasks_per_failure_mode"]),
            expected_fm_count=int(config["expected_failure_mode_count"])
            if config.get("expected_failure_mode_count") is not None
            else None,
        )
    llm_cfg = config.get("llm") or {}
    return agents, tasks, llm_cfg


def _discover_agents(root: Path, config: dict[str, Any]) -> list[dict[str, Any]]:
    entries = config.get("agents")
    if not isinstance(entries, list) or not entries:
        raise MatrixError(f"{SUITE_FILENAME} 缺少显式 agents 列表")
    expected = int(config.get("expected_agent_count") or len(entries))
    agents: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in entries:
        agent_id = str(entry.get("agent_id") or "").strip()
        path = root / str(entry.get("path") or "")
        if not agent_id:
            raise MatrixError(f"agent 缺少 agent_id: {entry}")
        if agent_id in seen:
            raise MatrixError(f"agent_id 重复: {agent_id}")
        if not path.is_file():
            raise MatrixError(f"Agent {agent_id} 文件不存在: {path}")
        seen.add(agent_id)
        agents.append(
            {
                "agent_id": agent_id,
                "agent_name": str(entry.get("agent_name") or agent_id),
                "path": path,
            }
        )
    if len(agents) != expected:
        raise MatrixError(f"Agent 数量 {len(agents)} != expected_agent_count {expected}")
    return agents


def validate_suite_tasks(
    tasks: list[dict[str, Any]], per_fm: int = 2, *, expected_fm_count: int | None = None
) -> None:
    """Suite 契约：distinct FM = 声明数量、每 FM 恰 per_fm 个、无重复 id。

    ``expected_fm_count`` 缺省时按当前正式 taxonomy 总数（FAILURE_MODE_COUNT）
    校验；冻结的 benchmark/suite（12 FM，早于 FM10/FM13 扩展）通过 suite.json
    的 expected_failure_mode_count 声明自身数量。
    """

    ids = [t["task_id"] for t in tasks]
    if len(ids) != len(set(ids)):
        raise MatrixError("task_id 存在重复")
    by_fm: dict[str, list[str]] = {}
    for task in tasks:
        fm = task["expected_failure_mode"]
        by_fm.setdefault(fm, []).append(task["task_id"])
    required = FAILURE_MODE_COUNT if expected_fm_count is None else expected_fm_count
    if len(by_fm) != required:
        raise MatrixError(
            f"distinct failure modes = {len(by_fm)}，必须为 {required}"
        )
    bad = {fm: ids for fm, ids in by_fm.items() if len(ids) != per_fm}
    if bad:
        raise MatrixError(f"每个 Failure Mode 必须 {per_fm} 个 Task，违规: {bad}")


def _discover_tasks(root: Path) -> list[dict[str, Any]]:
    tasks_dir = root / TASKS_DIRNAME
    if not tasks_dir.is_dir():
        raise MatrixError(f"缺少 tasks 目录: {tasks_dir}")
    tasks: list[dict[str, Any]] = []
    for task_dir in sorted(p for p in tasks_dir.iterdir() if p.is_dir()):
        missing = [name for name in REQUIRED_TASK_FILES if not (task_dir / name).is_file()]
        if missing:
            raise MatrixError(f"Task {task_dir.name} 缺少文件: {', '.join(missing)}")
        task = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
        errors: list[str] = []

        def fail(message: str) -> None:
            errors.append(message)

        _validate_expected_failure(task, fail)
        task_id = str(task.get("task_id") or "").strip()
        if not task_id:
            errors.append("task.json 缺少 task_id")
        elif task_id != task_dir.name:
            errors.append(f"task.json task_id={task_id!r} 与目录名 {task_dir.name!r} 不一致")
        if errors:
            raise MatrixError(f"Task {task_dir.name} 校验失败: {'; '.join(errors)}")
        try:
            source = (task_dir / "reference.py").read_text(encoding="utf-8")
            _, max_lines = _reference_limits()
            check_reference_source(source, max_lines)
        except Exception as exc:  # noqa: BLE001 - 教师 gold 资产必须静态合法
            raise MatrixError(f"Task {task_dir.name} reference.py 校验失败: {exc}") from exc
        tasks.append(
            {
                "task_id": task_id,
                "task_dir": task_dir,
                "expected_failure_mode": _expected_failure_of(task),
                "task_name": str(task.get("title") or task.get("capability") or task_id),
                "task": task,
            }
        )
    if not tasks:
        raise MatrixError(f"tasks 目录为空: {tasks_dir}")
    return tasks


def _expected_failure_of(task: dict[str, Any]) -> str | None:
    for key in ("expected_failure_mode", "expected_failure"):
        if task.get(key):
            return str(task[key]).strip()
    return None


# ---------------------------------------------------------------------------
# Staging Workspace（每组合独立 fresh workspace，从课程模板重新组装）
# ---------------------------------------------------------------------------


def prepare_workspace(
    staging_root: Path,
    agent: dict[str, Any],
    task: dict[str, Any],
    llm_cfg: dict[str, Any],
    env_source: Path | None = None,
) -> Path:
    """组装一个全新 workspace：Course Config（LLM/预算）+ Agent + Task。

    只继承课程级公共配置（student_id / protocol / LLM 配置 / 资源预算），
    绝不引用任何其他 workspace 的 Task / Agent / result —— Phase 2 与
    Matrix 都是 Fresh Submission，不存在 lineage。
    """

    workspace = staging_root / f"{agent['agent_id']}__{task['task_id']}"
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    shutil.copyfile(agent["path"], workspace / AGENT_FILE)
    for name in REQUIRED_TASK_FILES:
        shutil.copyfile(task["task_dir"] / name, workspace / name)
    _write_env(workspace, llm_cfg, env_source)
    # .env.example：连接 + 模型选择（v10.1.11：LLM_MODEL 由学生配置）
    (workspace / ".env.example").write_text(
        "LLM_BASE_URL=\nLLM_API_KEY=\nLLM_MODEL=\n",
        encoding="utf-8",
    )
    return workspace


def _write_env(workspace: Path, llm_cfg: dict[str, Any], env_source: Path | None) -> None:
    """解析 suite llm 连接配置，缺省回落到课程级 .env（凭据绝不写入任何 record）。

    v10.1.11：.env 写 Connection Configuration + LLM_MODEL（学生选择）；
    Runtime Policy（timeout / token budget）仍由 policy/runtime_policy.json
    统一决定——即使写进 .env 也不会被读取为正式约束（policy > environment）。
    """

    env: dict[str, str] = {}
    if env_source is not None and Path(env_source).is_file():
        env = parse_env_file(Path(env_source))
    lines = [
        f"LLM_BASE_URL={llm_cfg.get('base_url') or env.get('LLM_BASE_URL', '')}",
        f"LLM_API_KEY={llm_cfg.get('api_key') or env.get('LLM_API_KEY', '')}",
        f"LLM_MODEL={llm_cfg.get('model') or env.get('LLM_MODEL', '')}",
    ]
    (workspace / ".env").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _install_root() -> Path:
    """包安装处的仓库根（src/codesetarena/main/matrix.py 上三级）。"""

    return Path(__file__).resolve().parents[3]


def _resolve_course_env(suite_dir: Path, env_source: str | Path | None) -> Path | None:
    """定位课程级 .env：显式指定 > suite 目录向上逐级 > 包安装处仓库根。"""

    if env_source is not None:
        path = Path(env_source)
        return path if path.is_file() else None
    for base in [suite_dir, *suite_dir.parents[:4], _install_root()]:
        candidate = base / ".env"
        if candidate.is_file():
            env = parse_env_file(candidate)
            if env.get("LLM_BASE_URL", "").strip():
                return candidate
    return None


def _write_offline_env(experiment_dir: Path, llm_cfg: dict[str, Any]) -> Path:
    """离线 fixture 模式：注入 llm_factory 时写占位连接配置（绝不触网）。"""

    path = experiment_dir / "offline.env"
    lines = [
        f"LLM_BASE_URL={llm_cfg.get('base_url') or 'offline-fixture'}",
        f"LLM_API_KEY={llm_cfg.get('api_key') or 'offline-fixture'}",
        f"LLM_MODEL={llm_cfg.get('model') or 'offline-fixture'}",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Matrix 执行
# ---------------------------------------------------------------------------


def _record_to_row(
    experiment_id: str, agent: dict[str, Any], task: dict[str, Any], result: dict[str, Any]
) -> dict[str, Any]:
    gold = result["result"].get("declared_gold_answer")
    return {
        "experiment_id": experiment_id,
        "run_id": result["run_id"],
        "agent_id": agent["agent_id"],
        "task_id": task["task_id"],
        "expected_failure_mode": task["expected_failure_mode"],
        "outcome_label": matrix_label(
            {
                "outcome_type": result["outcome_type"],
                "internal_error_category": result["internal_error_category"],
                "resource_kind": result["resource_kind"],
            }
        ),
        "result": _jsonable(result["result"].get("result")),
        "declared_gold_answer": _jsonable(gold),
        "correct": result["correct"],
        "outcome_type": result["outcome_type"],
        "internal_error_category": result["internal_error_category"],
        "internal_error_detail": result["internal_error_detail"],
        "termination_source": result["termination_source"],
        "resource_kind": result["resource_kind"],
        "elapsed_seconds": result["elapsed_seconds"],
        "total_tokens": result["total_tokens"],
        "artifacts_path": str(Path(result["run_dir"]) / "result.json"),
    }


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return json.dumps(value, ensure_ascii=False)


def run_matrix(
    suite_dir: str | Path,
    *,
    results_root: str | Path | None = None,
    env_source: str | Path | None = None,
    llm_factory: Any = None,
    budget: Budget | None = None,
    experiment_id: str | None = None,
    log: Any = print,
) -> dict[str, Any]:
    """执行完整 Agent × Task Matrix；每个组合独立 fresh workspace + run。

    ``llm_factory`` / ``budget`` 仅用于离线 fixture 注入（测试），生产路径
    两者为 None：LLM 由 workspace .env 构造，Budget 由 policy 推导。
    """

    suite_dir = Path(suite_dir)
    agents, tasks, llm_cfg = load_suite(suite_dir)
    # 结果目录：显式指定 > suite 同级的 results/（benchmark/suite → benchmark/results）
    results_root = Path(results_root) if results_root else suite_dir.parent / RESULTS_DIRNAME
    experiment_id = experiment_id or f"EXP_{time.strftime('%Y%m%d_%H%M%S')}"
    experiment_dir = results_root / experiment_id
    if experiment_dir.exists():
        raise MatrixError(f"Experiment 已存在，禁止覆盖: {experiment_dir}")
    experiment_dir.mkdir(parents=True)

    env_path = _resolve_course_env(suite_dir, env_source)
    if env_path is None:
        if llm_factory is None:
            raise MatrixError(
                "未找到课程级 .env（LLM_BASE_URL/LLM_API_KEY/LLM_MODEL）；"
                "生产 Matrix 必须提供凭据来源（suite llm 配置或 --env 源）"
            )
        env_path = _write_offline_env(experiment_dir, llm_cfg)
    jsonl_path = experiment_dir / "matrix_records.jsonl"
    csv_path = experiment_dir / "matrix_records.csv"
    rows: list[dict[str, Any]] = []
    with jsonl_path.open("w", encoding="utf-8") as ledger, csv_path.open(
        "w", newline="", encoding="utf-8"
    ) as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for agent in agents:
            for task in tasks:
                workspace = prepare_workspace(
                    experiment_dir / RUNS_DIRNAME, agent, task, llm_cfg, env_path
                )
                result = run_project(
                    workspace,
                    llm=llm_factory(agent, task) if llm_factory else None,
                    agent_id=agent["agent_id"],
                    budget=budget,
                )
                row = _record_to_row(experiment_id, agent, task, result)
                rows.append(row)
                ledger.write(json.dumps(row, ensure_ascii=False) + "\n")
                writer.writerow(row)
                csv_file.flush()
                log(
                    f"[matrix] {agent['agent_id']} x {task['task_id']} "
                    f"label={row['outcome_label']} correct={row['correct']} "
                    f"fm={row['expected_failure_mode']}"
                )
    summary = matrix_summary(rows)
    (experiment_dir / "matrix_summary.json").write_text(
        json.dumps({"experiment_id": experiment_id, **summary}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return {
        "experiment_id": experiment_id,
        "experiment_dir": str(experiment_dir),
        "records": rows,
        "csv_path": str(csv_path),
        "jsonl_path": str(jsonl_path),
        "summary": summary,
    }


def matrix_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """矩阵完整性核对：总数 / 每 Agent / 每 Task / 每 FM（不排名不评分）。"""

    agent_ids = sorted({row["agent_id"] for row in rows})
    task_ids = sorted({row["task_id"] for row in rows})
    per_agent = {a: sum(1 for row in rows if row["agent_id"] == a) for a in agent_ids}
    per_task = {t: sum(1 for row in rows if row["task_id"] == t) for t in task_ids}
    per_fm: dict[str, int] = {}
    for row in rows:
        fm = row["expected_failure_mode"]
        if fm:
            per_fm[fm] = per_fm.get(fm, 0) + 1
    labels: dict[str, int] = {}
    for row in rows:
        labels[row["outcome_label"]] = labels.get(row["outcome_label"], 0) + 1
    expected_runs = len(agent_ids) * len(task_ids)
    return {
        "expected_runs": expected_runs,
        "actual_runs": len(rows),
        "complete": len(rows) == expected_runs,
        "agents": agent_ids,
        "tasks": task_ids,
        "runs_per_agent": per_agent,
        "runs_per_task": per_task,
        "runs_per_failure_mode": dict(sorted(per_fm.items())),
        "outcome_labels": dict(sorted(labels.items())),
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


__all__ = [
    "MATRIX_SCHEMA",
    "MatrixError",
    "CSV_FIELDS",
    "load_suite",
    "validate_suite_tasks",
    "prepare_workspace",
    "run_matrix",
    "matrix_summary",
    "matrix_label",
]
