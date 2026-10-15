"""Main Assignment 输出层 —— FM-Stratified Min-Count Sampling 的最终落盘 + Audit。

正式架构（不再包含任何 cyclic / offset / local search）::

    Frozen Main Pool
        -> FM-Stratified Sequential Min-Count Sampling（assignment.assign_main）
        -> Hard-Constraint Revalidation（A-G）
        -> main_assignment.json / reviewer_to_tasks.json / task_to_reviewers.json
        -> assignment_records.jsonl（每条 edge 带 sampling_round / review_slot）
        -> assignment_diversity.csv / task_exposure.csv / assignment_summary.json（审计）

Diversity 指标（只读 Task FM，绝不修改 / 合并 / 重分类）::

    own_fm[r] = Reviewer r 自己任务的自声明 FM
    D_r       = |{ fm(t) : t in A_r, fm(t) != own_fm[r] }|   # distinct_non_own_fm_count

Legacy（仅供历史复现，正式路径不调用）：optimize_main_assignment /
_local_search / _enumerate_swaps / write_optimization_summary / build_main_records
对应旧版 cyclic-shift local search 优化层，保留源码但标记 legacy。
"""
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from ..constants import HYPOTHESIZED_FM_COUNT
from .assignment import (
    DEFAULT_REVIEWS_PER_STUDENT,
    DEFAULT_SEED,
    LATE_BATCH,
    MAIN_BATCH,
    ROUND_FILL,
    ROUND_STRATIFIED,
    TASK_EXPOSURE_CSV,
    assign_main,
    check_main_constraints,
    merge_final_mappings,
    task_exposure_stats,
    verify_reverse_consistency,
    write_assignment_outputs,
    write_assignment_summary,
    write_task_exposure_audit,
)

ASSIGNMENT_DIVERSITY_CSV = "assignment_diversity.csv"
ASSIGNMENT_SUMMARY_JSON = "assignment_summary.json"

# legacy 常量（旧版 optimizer 使用；新 artifact 不再写入）
OPTIMIZED_METHOD = "cyclic_shift_local_search"
OPTIMIZER_STDLIB = "stdlib_local_search"
OBJECTIVE_NAME = "lexicographic_min_then_total_distinct_non_own_fm"
ASSIGNMENT_OPTIMIZATION_JSON = "assignment_optimization.json"


# ---------------------------------------------------------------------------
# Diversity 指标（只读 Task FM，绝不修改 / 合并 / 重分类）
# ---------------------------------------------------------------------------


def task_fm_map(rows: list[dict[str, Any]]) -> dict[str, str]:
    return {r["task_id"]: r["hypothesized_failure_mode"] for r in rows}


def own_task_map(rows: list[dict[str, Any]]) -> dict[str, str]:
    return {r["student_id"]: r["task_id"] for r in rows}


def own_fm_map(rows: list[dict[str, Any]]) -> dict[str, str]:
    return {r["student_id"]: r["hypothesized_failure_mode"] for r in rows}


def compute_reviewer_diversity(
    reviewer: str,
    task_ids: list[str],
    task_fm: dict[str, str],
    own_fm: str,
) -> int:
    """D_r = 分配 Task 中「非自己 FM」的 distinct Failure Mode 数量。"""

    return len({task_fm[t] for t in task_ids if task_fm[t] != own_fm})


def compute_diversity_stats(
    reviewer_to_tasks: dict[str, list[str]],
    task_fm: dict[str, str],
    own_fm: dict[str, str],
) -> dict[str, Any]:
    """返回 min/mean/median/max/total 与 per_reviewer 明细（D_r）。"""

    per = {
        r: compute_reviewer_diversity(r, ts, task_fm, own_fm.get(r, ""))
        for r, ts in reviewer_to_tasks.items()
    }
    if not per:
        return {"min": 0, "mean": 0.0, "median": 0.0, "max": 0, "total": 0, "per_reviewer": {}}
    values = sorted(per.values())
    n = len(values)
    median = values[n // 2] if n % 2 else (values[n // 2 - 1] + values[n // 2]) / 2
    return {
        "min": values[0],
        "mean": sum(values) / n,
        "median": median,
        "max": values[-1],
        "total": sum(values),
        "per_reviewer": per,
    }


def compute_assignment_objective(
    reviewer_to_tasks: dict[str, list[str]],
    task_fm: dict[str, str],
    own_fm: dict[str, str],
) -> tuple[int, int]:
    """legacy 兼容：lexicographic objective tuple (min_r D_r, sum_r D_r)。"""

    per = {
        r: compute_reviewer_diversity(r, ts, task_fm, own_fm.get(r, ""))
        for r, ts in reviewer_to_tasks.items()
    }
    return (min(per.values()) if per else 0, sum(per.values()))


def diversity_upper_bounds(rows: list[dict[str, Any]], reviews: int) -> dict[str, int]:
    """每个 Reviewer 的理论 Diversity 上界：
    min(K, 当前 pool 实际存在的非 own FM 数)。own FM 不在 pool 中则按其现有值计算。"""

    present = {r["hypothesized_failure_mode"] for r in rows}
    bounds: dict[str, int] = {}
    for r in rows:
        available = len(present - {r["hypothesized_failure_mode"]})
        bounds[r["student_id"]] = min(reviews, available)
    return bounds


def assert_hard_invariants(
    reviewer_to_tasks: dict[str, list[str]],
    task_to_reviewers: dict[str, list[str]],
    rows: list[dict[str, Any]],
    reviews: int,
) -> None:
    """A-G 硬不变量（新 sampling 契约；task exposure 为 0..cap 而非精确 == K）。"""

    frozen_students = {r["student_id"] for r in rows}
    frozen_tasks = {r["task_id"] for r in rows}
    cap = reviews + 1
    assert set(reviewer_to_tasks) == frozen_students, "所有 Reviewer 必须来自 frozen main pool"
    for reviewer, tasks in reviewer_to_tasks.items():
        assert len(tasks) == reviews, f"{reviewer} workload 必须 == m"
        assert len(set(tasks)) == len(tasks), f"{reviewer} 内不允许重复 Task"
        assert all(t in frozen_tasks for t in tasks), f"{reviewer} 的 Task 必须来自 frozen main pool"
        own = next(r["task_id"] for r in rows if r["student_id"] == reviewer)
        assert own not in tasks, f"{reviewer} 不能看到自己的题 {own}"
    assert set(task_to_reviewers) == frozen_tasks, "所有 Task 必须来自 frozen main pool"
    for task, reviewers in task_to_reviewers.items():
        assert len(reviewers) <= cap, f"main task {task} exposure {len(reviewers)} > cap {cap}"
        assert len(set(reviewers)) == len(reviewers), f"main task {task} reviewer 不允许重复"
    assert verify_reverse_consistency(reviewer_to_tasks, task_to_reviewers) == [], "双向必须一致"


# ---------------------------------------------------------------------------
# Diversity Audit（assignment_diversity.csv，仅审计）
# ---------------------------------------------------------------------------


def write_diversity_audit(
    path: str | Path,
    rows_all: list[dict[str, Any]],
    reviewer_to_tasks: dict[str, list[str]],
    frozen_students: set[str],
    *,
    records: list[dict[str, Any]] | None = None,
    expected_review_count: int | None = None,
) -> Path:
    """assignment_diversity.csv：每 Reviewer 一行（§26 列定义）。

    records 可选：带 sampling_round 的逐条记录，用于统计 fm_coverage_round_count /
    fill_round_count；缺省时两列留空。这张表只用于审计，不是 Phase 2 source。
    """

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    task_fm = task_fm_map(rows_all)
    own_task = own_task_map(rows_all)
    own_fm = own_fm_map(rows_all)
    ub = diversity_upper_bounds(rows_all, max((len(v) for v in reviewer_to_tasks.values()), default=0))
    expected = expected_review_count if expected_review_count is not None else max(
        (len(v) for v in reviewer_to_tasks.values()), default=0
    )
    round_counts: dict[str, Counter] = {}
    if records:
        for record in records:
            reviewer = record.get("reviewer_id")
            if reviewer is None:
                continue
            round_counts.setdefault(reviewer, Counter())[record.get("sampling_round", "")] += 1

    fields = (
        "reviewer_id", "own_task_id", "own_fm", "assigned_task_ids", "assigned_fms",
        "distinct_assigned_fm_count", "distinct_non_own_fm_count", "diversity_upper_bound",
        "normalized_diversity", "is_at_upper_bound", "assignment_batch",
        "review_count", "expected_review_count", "fm_coverage_round_count", "fill_round_count",
    )
    with open(out, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for reviewer in sorted(reviewer_to_tasks):
            tasks = sorted(reviewer_to_tasks[reviewer])
            fms = sorted({task_fm[t] for t in tasks})
            d = compute_reviewer_diversity(reviewer, tasks, task_fm, own_fm.get(reviewer, ""))
            bound = ub.get(reviewer, 0)
            normalized = round(d / bound, 6) if bound else ""
            counts = round_counts.get(reviewer, Counter())
            writer.writerow(
                {
                    "reviewer_id": reviewer,
                    "own_task_id": own_task.get(reviewer, ""),
                    "own_fm": own_fm.get(reviewer, ""),
                    "assigned_task_ids": ";".join(tasks),
                    "assigned_fms": ";".join(fms),
                    "distinct_assigned_fm_count": len(fms),
                    "distinct_non_own_fm_count": d,
                    "diversity_upper_bound": bound,
                    "normalized_diversity": normalized,
                    "is_at_upper_bound": str(d >= bound and bound > 0),
                    "assignment_batch": MAIN_BATCH if reviewer in frozen_students else LATE_BATCH,
                    "review_count": len(tasks),
                    "expected_review_count": expected,
                    "fm_coverage_round_count": counts.get(ROUND_STRATIFIED, 0),
                    "fill_round_count": counts.get(ROUND_FILL, 0),
                }
            )
    return out


# ---------------------------------------------------------------------------
# 共享 Main Assignment runner（build_phase1_pool --assign 与 build_assignment 共用）
# ---------------------------------------------------------------------------


def run_main_assignment_pipeline(
    rows: list[dict[str, Any]],
    snapshot: dict[str, Any],
    assignment_dir: str | Path,
    *,
    seed: int = DEFAULT_SEED,
    reviews: int = DEFAULT_REVIEWS_PER_STUDENT,
) -> dict[str, Any]:
    """FM 分层顺序采样 -> 硬约束校验 -> 全部输出 + 审计文件。

    正式主路径完全由 assignment.assign_main（fm_stratified_min_count_sampling）
    生成；不再调用任何 cyclic / offset / local search 代码。返回 metadata dict
    （paths / stats / method）。Main Assignment 失败时（INSUFFICIENT_MAIN_POOL /
    CAPACITY_EXHAUSTED）不写任何 partial final mappings（异常在 assign_main 内抛）。
    """

    out = Path(assignment_dir)
    out.mkdir(parents=True, exist_ok=True)
    main_result = assign_main(rows, seed=seed, reviews=reviews)

    final_r2t = {s: list(ts) for s, ts in main_result["reviewer_to_tasks"].items()}
    method = main_result["assignment_method"]

    errors = check_main_constraints(main_result)
    assert not errors, f"Main Assignment 硬约束失败: {errors}"

    records = list(main_result["records"])
    frozen_students = {r["student_id"] for r in rows}
    reviewer_to_tasks, task_to_reviewers = merge_final_mappings(main_result)
    paths = write_assignment_outputs(
        out,
        main_result=main_result,
        reviewer_to_tasks=reviewer_to_tasks,
        task_to_reviewers=task_to_reviewers,
        records=records,
        snapshot=snapshot,
        main_mapping={s: list(ts) for s, ts in final_r2t.items()},
        extra_meta=_provenance(main_result),
    )

    audit_path = write_diversity_audit(
        out / ASSIGNMENT_DIVERSITY_CSV, rows, final_r2t, frozen_students,
        records=records, expected_review_count=reviews,
    )
    exposure_path = write_task_exposure_audit(
        out / TASK_EXPOSURE_CSV, rows, main_result["selection_count"],
        main_result["task_exposure_cap"],
    )
    summary_path = write_assignment_summary(out / ASSIGNMENT_SUMMARY_JSON, main_result)

    stats = compute_diversity_stats(final_r2t, task_fm_map(rows), own_fm_map(rows))
    return {
        "paths": paths,
        "audit_path": audit_path,
        "exposure_path": exposure_path,
        "summary_path": summary_path,
        "pool_size": len(rows),
        "review_degree": reviews,
        "seed": seed,
        "method": method,
        "reviewer_fm_diversity": stats,
        "task_exposure": task_exposure_stats(main_result["selection_count"]),
        "task_exposure_cap": main_result["task_exposure_cap"],
        "reviewer_to_tasks": reviewer_to_tasks,
        "task_to_reviewers": task_to_reviewers,
    }


def _provenance(main_result: dict[str, Any]) -> dict[str, Any]:
    """§24 Main provenance：只写新 sampling 语义字段，绝不写 cyclic / local-search。"""

    return {
        "assignment_method": main_result["assignment_method"],
        "sampling_mode": "sequential",
        "replacement_across_reviewers": True,
        "replacement_within_reviewer": False,
        "review_degree": main_result["review_degree"],
        "task_exposure_cap": main_result["task_exposure_cap"],
        "seed": main_result["seed"],
        "fm_taxonomy_size": HYPOTHESIZED_FM_COUNT,
    }


# ---------------------------------------------------------------------------
# Legacy（仅历史复现；正式 build 路径禁止调用）
# ---------------------------------------------------------------------------


def optimize_main_assignment(
    baseline: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    seed: int = DEFAULT_SEED,
    time_limit_seconds: float = 30.0,
) -> dict[str, Any]:
    """LEGACY：旧版 cyclic-shift local search 优化器（已停用，仅供历史复现）。"""

    from .assignment import assign_main_cyclic_legacy  # noqa: F401

    baseline_stats = compute_diversity_stats(
        baseline["reviewer_to_tasks"], task_fm_map(rows), own_fm_map(rows)
    )
    baseline_objective = (baseline_stats["min"], baseline_stats["total"])
    return {
        "optimization_status": "legacy_unused",
        "optimization_error": "cyclic_shift_local_search 已从正式路径移除",
        "accepted_swaps": 0,
        "evaluated_swaps": 0,
        "optimization_time_seconds": 0.0,
        "reviewer_to_tasks": {s: list(ts) for s, ts in baseline["reviewer_to_tasks"].items()},
        "task_to_reviewers": {t: list(rs) for t, rs in baseline["task_to_reviewers"].items()},
        "baseline_stats": baseline_stats,
        "optimized_stats": baseline_stats,
        "baseline_objective": list(baseline_objective),
        "optimized_objective": list(baseline_objective),
        "seed": seed,
    }


def write_optimization_summary(
    path: str | Path,
    **kwargs: Any,
) -> Path:
    """LEGACY：旧版 assignment_optimization.json writer（已停用，仅供历史复现）。"""

    out = Path(path)
    out.write_text(
        json.dumps({"legacy": True, "method": OPTIMIZED_METHOD, **kwargs}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return out


def build_main_records(reviewer_to_tasks: dict[str, list[str]], *, method: str,
                       seed: int) -> list[dict[str, Any]]:
    """LEGACY：旧版 main records builder（已停用；正式记录由 assign_main 生成）。"""

    records: list[dict[str, Any]] = []
    for reviewer in sorted(reviewer_to_tasks):
        for task in reviewer_to_tasks[reviewer]:
            records.append(
                {
                    "reviewer_id": reviewer,
                    "task_id": task,
                    "assignment_batch": MAIN_BATCH,
                    "assignment_method": method,
                    "assignment_seed": seed,
                }
            )
    return records