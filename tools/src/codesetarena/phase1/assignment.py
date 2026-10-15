"""Phase 1 Assignment —— FM-Stratified Sequential Min-Count Sampling（正式 Main 方法）+ Late 增量。

Main（冻结后只执行一次）—— 完全由逐次 sampling 生成，不再使用任何 cyclic/offset 构造::

    reviewers = sorted(main_reviewers); rng.shuffle(reviewers)   # reviewer 顺序随机化
    for reviewer in reviewers:
        Round 1 (FM-stratified): 打乱 FM01..FM14 顺序，每个 FM 采样 1 个 Task
          - 候选必须同时满足：owner(task) != reviewer、task 未在本 reviewer 出现、
            selection_count[task] < cap
        Round 2 (fill): 直到 len(selected) == m
          - 全局候选同三条合法性；优先最低 selection_count；同 min_count 内优先
            reviewer 尚未覆盖的 FM；剩余 seeded random tie-break

正式语义:

- 跨 Reviewer 有放回（Task 可被多个 Reviewer 重复抽到）、Reviewer 内无放回；
- 禁止 self-review（owner(task) != reviewer，硬约束）；
- Task exposure count <= cap = m + 1（不要求每 Task 恰好 m 个 reviewer）；
- 每个 Reviewer 恰好 m 个 Task；m 默认 15，Demo/测试可传 2；
- 全部随机性来自单个 ``random.Random(seed)``：相同 input + 相同 seed
  -> reviewer order / FM order / tie-break 全部一致 -> assignment 完全一致。

Late：frozen main pool 只读。迟交学生复用同一 sampling policy（同样的
FM 分层 / min-count-first / cap）：读取当前 selection_count（= main + 之前 late
的累计 reviewer 数）后，为 Late Reviewer 单独跑一次两轮采样。late Task 允许
0 reviewers；Late 绝不修改 main_assignment.json 中冻结的 main_reviewer_to_tasks。

Legacy（不再作为正式方法，仅供历史复现，正式 build 路径禁止调用）：
balanced cyclic-shift（assign_main_cyclic_legacy）与 late exact/greedy FM-diversity
（pick_late_tasks 旧实现）。新生成 artifact 的 assignment_method 只写
``fm_stratified_min_count_sampling``。
"""
from __future__ import annotations

import hashlib
import json
import random
from datetime import datetime
from pathlib import Path
from typing import Any

from ..constants import HYPOTHESIZED_FM_COUNT, HYPOTHESIZED_FM_ORDER

MAIN_BATCH = "main"
LATE_BATCH = "late"
MAIN_METHOD = "fm_stratified_min_count_sampling"  # 正式 Main Assignment 方法（唯一）
LATE_METHOD = MAIN_METHOD  # late 复用同一 sampling policy
LEGACY_CYCLIC_METHOD = "balanced_cyclic_shift"  # 历史 artifact 方法名（只读兼容）
LEGACY_LOCAL_SEARCH_METHOD = "cyclic_shift_local_search"  # 历史 artifact 方法名
LATE_EXACT_METHOD = "late_exact_fm_diversity"  # 旧版 late 方法名（兼容引用）
LATE_GREEDY_METHOD = "late_greedy_fm_diversity"  # 旧版 late 方法名
DEFAULT_SEED = 42
DEFAULT_REVIEWS_PER_STUDENT = 15  # 正式课程默认 m；Demo/测试可显式传 2
REVIEWS_PER_STUDENT = DEFAULT_REVIEWS_PER_STUDENT  # 兼容别名（现为 production 默认）
MAX_LATE_COMBINATIONS = 100_000  # 旧版 late helper 的组合数阈值（legacy）

MAIN_ASSIGNMENT_JSON = "main_assignment.json"
ASSIGNMENT_RECORDS_JSONL = "assignment_records.jsonl"
REVIEWER_TO_TASKS_JSON = "reviewer_to_tasks.json"
TASK_TO_REVIEWERS_JSON = "task_to_reviewers.json"
TASK_EXPOSURE_CSV = "task_exposure.csv"
ASSIGNMENT_SUMMARY_JSON = "assignment_summary.json"

ROUND_STRATIFIED = "fm_stratified"
ROUND_FILL = "fill"


class InsufficientMainPoolError(ValueError):
    """Main pool 不足以保证每个 Reviewer 拿到 m 个不同非自有 Task（INSUFFICIENT_MAIN_POOL）。"""


class AssignmentCapacityError(RuntimeError):
    """Fill Round 找不到任何合法候选（CAPACITY_EXHAUSTED，进入人工处理）。"""


# ---------------------------------------------------------------------------
# Main：FM-Stratified Sequential Min-Count Sampling
# ---------------------------------------------------------------------------


def assign_main(
    rows: list[dict[str, Any]],
    *,
    seed: int = DEFAULT_SEED,
    reviews: int = DEFAULT_REVIEWS_PER_STUDENT,
) -> dict[str, Any]:
    """对 frozen main pool 行做 FM 分层顺序采样分配（正式 Main Assignment）。

    输入行必须：task_id 全局唯一、student_id 唯一、每学生恰好一个 task
    （由 Batch Validator 保证）。返回含 reviewer_to_tasks / task_to_reviewers /
    records / selection_count 的结果字典，并执行全部硬约束断言。
    """

    students = sorted({r["student_id"] for r in rows})
    own_task = {r["student_id"]: r["task_id"] for r in rows}
    tasks = sorted(own_task.values())
    assert len(own_task) == len(students) == len(rows), "每学生必须恰好一个 task"

    n = len(students)
    if n - 1 < reviews:
        raise InsufficientMainPoolError(
            f"INSUFFICIENT_MAIN_POOL: main pool 学生数 {n} 不足以保证每人 {reviews} 个"
            f"不同非自有 Task（要求 n >= {reviews + 1}）。请勿降低 m 之外悄悄改参数；"
            f"Demo/测试请显式传 --reviews-per-student 2。"
        )

    owner = {r["task_id"]: r["student_id"] for r in rows}
    task_fm = {r["task_id"]: r["hypothesized_failure_mode"] for r in rows}
    fm_to_tasks: dict[str, list[str]] = {fm: [] for fm in HYPOTHESIZED_FM_ORDER}
    for r in rows:
        fm_to_tasks.setdefault(r["hypothesized_failure_mode"], []).append(r["task_id"])

    selection_count: dict[str, int] = {t: 0 for t in tasks}
    cap = reviews + 1

    rng = random.Random(seed)
    reviewer_order = list(students)
    rng.shuffle(reviewer_order)  # 禁止固定 student_id 顺序分配

    reviewer_to_tasks: dict[str, list[str]] = {}
    records: list[dict[str, Any]] = []
    for reviewer in reviewer_order:
        picked, edges = _sample_single_reviewer(
            reviewer, tasks, fm_to_tasks, task_fm, owner, selection_count,
            m=reviews, cap=cap, rng=rng, batch=MAIN_BATCH, method=MAIN_METHOD, seed=seed,
        )
        reviewer_to_tasks[reviewer] = picked
        records.extend(edges)

    task_to_reviewers: dict[str, list[str]] = {t: [] for t in tasks}
    for reviewer in sorted(reviewer_to_tasks):
        for task_id in reviewer_to_tasks[reviewer]:
            task_to_reviewers[task_id].append(reviewer)

    result = {
        "seed": seed,
        "assignment_batch": MAIN_BATCH,
        "assignment_method": MAIN_METHOD,
        "reviews_per_student": reviews,
        "review_degree": reviews,
        "task_exposure_cap": cap,
        "fm_taxonomy_size": HYPOTHESIZED_FM_COUNT,
        "student_count": n,
        "task_count": len(tasks),
        "selection_count": selection_count,
        "task_fm": task_fm,
        "owner": owner,
        "reviewer_to_tasks": reviewer_to_tasks,
        "task_to_reviewers": task_to_reviewers,
        "records": records,
    }
    _assert_main_invariants(result)
    return result


def _sample_single_reviewer(
    reviewer: str,
    main_tasks: list[str],
    fm_to_tasks: dict[str, list[str]],
    task_fm: dict[str, str],
    owner: dict[str, str],
    selection_count: dict[str, int],
    *,
    m: int,
    cap: int,
    rng: random.Random,
    batch: str,
    method: str,
    seed: int,
) -> tuple[list[str], list[dict[str, Any]]]:
    """单 Reviewer 的两轮采样（main 与 late 共用同一策略）。

    Round 1（fm_stratified）：打乱 14 个 FM 顺序，每个 FM 最多取 1 个 Task；
    Round 2（fill）：全局补足到 m。每步优先级固定：

      1. 合法性：owner(task) != reviewer、task 未在本 reviewer 出现、count < cap；
      2. 最低 selection_count；
      3. FM 多样性偏好（仅在同一 min_count 内：优先未覆盖 FM）；
      4. seeded random tie-break（对排序后的候选 choice）。

    返回 (picked, edges)；edges 即带 provenance 的逐条记录
    （review_slot / task_selection_count_after / task_fm / sampling_round）。
    """

    selected: list[str] = []
    selected_set: set[str] = set()
    selected_fms: set[str] = set()
    edges: list[dict[str, Any]] = []

    def commit(chosen: str, fm: str, round_name: str) -> None:
        selected.append(chosen)
        selected_set.add(chosen)
        selected_fms.add(fm)
        selection_count[chosen] += 1
        edges.append(
            {
                "reviewer_id": reviewer,
                "task_id": chosen,
                "assignment_batch": batch,
                "assignment_method": method,
                "seed": seed,
                "review_slot": len(selected),
                "task_selection_count_after": selection_count[chosen],
                "task_fm": fm,
                "sampling_round": round_name,
            }
        )

    # Round 1：FM 分层覆盖（FM 顺序每 Reviewer 独立打乱，避免低编号 FM 永远优先）
    fm_order = list(HYPOTHESIZED_FM_ORDER)
    rng.shuffle(fm_order)
    for fm in fm_order:
        if len(selected) >= m:
            break
        candidates = [
            t
            for t in fm_to_tasks.get(fm, [])
            if owner.get(t) != reviewer
            and t not in selected_set
            and selection_count[t] < cap
        ]
        if not candidates:
            continue  # 该 FM 无合法候选（如 Reviewer 是唯一 owner）→ 直接跳过
        min_count = min(selection_count[t] for t in candidates)
        least_used = sorted(t for t in candidates if selection_count[t] == min_count)
        chosen = rng.choice(least_used)
        commit(chosen, fm, ROUND_STRATIFIED)

    # Round 2：补足到 m=15（不要求新 FM；min-count 相同才看 FM 多样性）
    while len(selected) < m:
        candidates = [
            t
            for t in main_tasks
            if owner.get(t) != reviewer
            and t not in selected_set
            and selection_count[t] < cap
        ]
        if not candidates:
            raise AssignmentCapacityError(
                f"CAPACITY_EXHAUSTED: reviewer {reviewer} 无法凑满 {m} 个合法 Task "
                f"(selected_count={len(selected)}, required_count={m}, "
                f"eligible_remaining=0, cap={cap})"
            )
        min_count = min(selection_count[t] for t in candidates)
        least_used = sorted(t for t in candidates if selection_count[t] == min_count)
        unseen_fm_candidates = [t for t in least_used if task_fm[t] not in selected_fms]
        tie_pool = unseen_fm_candidates if unseen_fm_candidates else least_used
        chosen = rng.choice(tie_pool)
        commit(chosen, task_fm[chosen], ROUND_FILL)

    return selected, edges


def check_main_constraints(result: dict[str, Any]) -> list[str]:
    """新 Main Assignment 硬约束 A-G 检查，返回错误列表（空 == 全部通过）。

    A. 每个 Reviewer 恰好 m 个 Task；
    B. Reviewer 内无重复 Task；
    C. 无 self-assignment（owner(task) != reviewer）；
    D. 每个 Task 累计 selection_count <= cap（= m+1），不再要求恰好 m；
    E. 所有 assigned task 来自 frozen main pool；
    F. 所有 reviewer 来自 frozen main pool；
    G. reviewer_to_tasks 与 task_to_reviewers 完全互逆。
    """

    errors: list[str] = []
    reviews = result["review_degree"]
    cap = result["task_exposure_cap"]
    reviewer_to_tasks = result["reviewer_to_tasks"]
    task_to_reviewers = result["task_to_reviewers"]
    owner = result.get("owner", {})
    pool_students = set(owner.values())
    pool_tasks = set(owner)

    for reviewer, tasks in sorted(reviewer_to_tasks.items()):
        if reviewer not in pool_students:
            errors.append(f"F: reviewer {reviewer} 不在 frozen main pool")
        if len(tasks) != reviews:
            errors.append(f"A: {reviewer} workload {len(tasks)} != {reviews}")
        if len(set(tasks)) != len(tasks):
            errors.append(f"B: {reviewer} 内不允许重复 Task")
        for task_id in tasks:
            if task_id not in pool_tasks:
                errors.append(f"E: {reviewer} 的 Task {task_id} 不在 frozen main pool")
            if owner.get(task_id) == reviewer:
                errors.append(f"C: {reviewer} 不能看到自己的题 {task_id}")
    for task_id, reviewers in sorted(task_to_reviewers.items()):
        if task_id not in pool_tasks:
            errors.append(f"E: Task {task_id} 不在 frozen main pool")
        if len(reviewers) > cap:
            errors.append(f"D: Task {task_id} exposure {len(reviewers)} > cap {cap}")
        if len(set(reviewers)) != len(reviewers):
            errors.append(f"D: Task {task_id} reviewer 不允许重复")
    errors.extend(verify_reverse_consistency(reviewer_to_tasks, task_to_reviewers))
    return errors


def _assert_main_invariants(result: dict[str, Any]) -> None:
    errors = check_main_constraints(result)
    assert not errors, f"Main Assignment 硬约束失败: {errors}"


# ---------------------------------------------------------------------------
# Late：复用同一 sampling policy（frozen main pool 只读）
# ---------------------------------------------------------------------------


def assign_late(
    late_student_id: str,
    frozen_rows: list[dict[str, Any]],
    *,
    seed: int = DEFAULT_SEED,
    reviews: int = DEFAULT_REVIEWS_PER_STUDENT,
    task_load: dict[str, int] | None = None,
    own_fm: str | None = None,
    return_method: bool = False,
    return_edges: bool = False,
):
    """为迟交学生从 frozen main pool 按同一采样策略选 m 题。

    规则与 Main 完全一致：FM 分层 Round 1 + fill Round 2、min-count-first、
    cap = m+1、无 self-review、Reviewer 内不重复。task_load 为 Task 当前累计
    reviewer 数（main + 之前 late），作为本次采样的初始 selection_count。
    late Task 允许 0 reviewers；绝不修改任何 Main Reviewer 的分配。

    兼容旧签名：默认返回 list[str]；``return_method=True`` 返回 ``(picked, method)``；
    ``return_edges=True`` 返回 ``(picked, method, edges)``（edges 带 sampling_round）。
    """

    picked, method, edges = pick_late_tasks(
        late_student_id, frozen_rows, seed=seed, reviews=reviews, task_load=task_load,
    )
    if return_edges:
        return picked, method, edges
    if return_method:
        return picked, method
    return picked


def pick_late_tasks(
    late_student_id: str,
    frozen_rows: list[dict[str, Any]],
    *,
    seed: int = DEFAULT_SEED,
    reviews: int = DEFAULT_REVIEWS_PER_STUDENT,
    task_load: dict[str, int] | None = None,
    own_fm: str | None = None,
) -> tuple[list[str], str, list[dict[str, Any]]]:
    """late 采样的完整入口：同一 FM 分层策略，返回 (picked, method, edges)。"""

    tasks = sorted({r["task_id"] for r in frozen_rows})
    owner = {r["task_id"]: r["student_id"] for r in frozen_rows}
    task_fm = {r["task_id"]: r["hypothesized_failure_mode"] for r in frozen_rows}
    if len(tasks) - 1 < reviews:
        raise InsufficientMainPoolError(
            f"INSUFFICIENT_MAIN_POOL: late student {late_student_id} 需要 {reviews} 个"
            f"非自有 Task，但 frozen main pool 仅 {len(tasks) - 1} 个可用（n >= {reviews + 1}）"
        )
    fm_to_tasks: dict[str, list[str]] = {fm: [] for fm in HYPOTHESIZED_FM_ORDER}
    for r in frozen_rows:
        fm_to_tasks.setdefault(r["hypothesized_failure_mode"], []).append(r["task_id"])

    selection_count: dict[str, int] = {
        t: int(task_load.get(t, 0)) if task_load else 0 for t in tasks
    }
    cap = reviews + 1
    rng = random.Random(_student_seed(late_student_id, seed))
    picked, edges = _sample_single_reviewer(
        late_student_id, tasks, fm_to_tasks, task_fm, owner, selection_count,
        m=reviews, cap=cap, rng=rng, batch=LATE_BATCH, method=LATE_METHOD, seed=seed,
    )
    return picked, LATE_METHOD, edges


def _student_seed(student_id: str, seed: int) -> int:
    """每个 late student 独立的确定性 seed（同学生+同 seed 必可复现；不同学生去相关）。"""

    stable = int(hashlib.sha256(str(student_id).encode("utf-8")).hexdigest(), 16) % (2**32)
    return (int(seed) + stable) % (2**32)


# ---------------------------------------------------------------------------
# 最终合并视图 + 输出
# ---------------------------------------------------------------------------


def merge_final_mappings(
    main_result: dict[str, Any],
    late_assignments: dict[str, list[str]] | None = None,
    *,
    late_owned_tasks: list[str] | None = None,
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """合并 main + late 为最终 reviewer_to_tasks / task_to_reviewers（正反向一致）。

    late_owned_tasks：迟交学生自己的 task_id 列表（其 task 允许 0 reviewers）。
    main mapping 完全不动；只对 late reviewer 追加记录、对晚交 task 建空列表。
    """

    reviewer_to_tasks = {s: list(ts) for s, ts in main_result["reviewer_to_tasks"].items()}
    task_to_reviewers = {t: list(rs) for t, rs in main_result["task_to_reviewers"].items()}

    for late_student, tasks in (late_assignments or {}).items():
        assert late_student not in reviewer_to_tasks, "late student 不得覆盖 main reviewer"
        for task_id in tasks:
            task_to_reviewers.setdefault(task_id, []).append(late_student)
        reviewer_to_tasks[late_student] = list(tasks)

    for late_task in late_owned_tasks or []:
        task_to_reviewers.setdefault(late_task, [])

    # 最终视图同样受 exposure cap 约束（late 复用同一 cap=m+1）
    cap = main_result.get("task_exposure_cap")
    if cap is not None:
        for task_id, reviewers in task_to_reviewers.items():
            assert len(reviewers) <= cap, (
                f"Task {task_id} exposure {len(reviewers)} > cap {cap}（late 不得突破 cap）"
            )

    errors = verify_reverse_consistency(reviewer_to_tasks, task_to_reviewers)
    assert not errors, f"最终 mapping 不一致: {errors}"
    return reviewer_to_tasks, task_to_reviewers


def verify_reverse_consistency(
    reviewer_to_tasks: dict[str, list[str]],
    task_to_reviewers: dict[str, list[str]],
) -> list[str]:
    """校验两组 mapping 互为一致的正反向索引。"""

    errors: list[str] = []
    for reviewer, tasks in reviewer_to_tasks.items():
        for task in tasks:
            if reviewer not in task_to_reviewers.get(task, []):
                errors.append(f"{reviewer} -> {task} 缺失于 task_to_reviewers")
    for task, reviewers in task_to_reviewers.items():
        for reviewer in reviewers:
            if task not in reviewer_to_tasks.get(reviewer, []):
                errors.append(f"{task} -> {reviewer} 缺失于 reviewer_to_tasks")
    return errors


def write_assignment_outputs(
    assignment_dir: str | Path,
    *,
    main_result: dict[str, Any],
    reviewer_to_tasks: dict[str, list[str]],
    task_to_reviewers: dict[str, list[str]],
    records: list[dict[str, Any]],
    snapshot: dict[str, Any] | None = None,
    main_mapping: dict[str, list[str]] | None = None,
    extra_meta: dict[str, Any] | None = None,
) -> dict[str, Path]:
    """写 main_assignment.json / assignment_records.jsonl / 两个最终 mapping。

    main_mapping：main-only reviewer->tasks（冻结的是这个）；extra_meta：provenance
    扩展（§24 新字段，main_assignment.json 保留旧字段兼容下游读取）。
    """

    out = Path(assignment_dir)
    out.mkdir(parents=True, exist_ok=True)

    main_assignment = {
        "seed": main_result["seed"],
        "assignment_batch": MAIN_BATCH,
        "assignment_method": main_result["assignment_method"],
        "reviews_per_student": main_result["reviews_per_student"],
        "student_count": main_result["student_count"],
        "task_count": main_result["task_count"],
        "anonymized": True,
        "frozen_snapshot": snapshot,
    }
    if main_mapping is not None:
        main_assignment["main_reviewer_to_tasks"] = main_mapping
    if extra_meta:
        main_assignment.update(extra_meta)
    main_path = out / MAIN_ASSIGNMENT_JSON
    main_path.write_text(
        json.dumps(main_assignment, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    records_path = out / ASSIGNMENT_RECORDS_JSONL
    with open(records_path, "w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    reviewer_path = out / REVIEWER_TO_TASKS_JSON
    reviewer_path.write_text(
        json.dumps(reviewer_to_tasks, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    task_path = out / TASK_TO_REVIEWERS_JSON
    task_path.write_text(
        json.dumps(task_to_reviewers, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    return {
        "main_assignment": main_path,
        "records": records_path,
        "reviewer_to_tasks": reviewer_path,
        "task_to_reviewers": task_path,
    }


def verify_main_unchanged(
    main_mapping: dict[str, list[str]],
    final_reviewer_to_tasks: dict[str, list[str]],
) -> list[str]:
    """校验 final reviewer_to_tasks 中 main 学生的分配与 frozen main 完全一致。

    main_mapping 来自 main_assignment.json 的 main_reviewer_to_tasks（冻结快照）。
    """

    errors: list[str] = []
    for reviewer, tasks in main_mapping.items():
        if final_reviewer_to_tasks.get(reviewer) != tasks:
            errors.append(
                f"main reviewer {reviewer} 的分配被修改: {tasks} != "
                f"{final_reviewer_to_tasks.get(reviewer)}"
            )
    return errors


def records_after_main(
    main_result: dict[str, Any],
    late_assignments: dict[str, list[str]],
    *,
    seed: int = DEFAULT_SEED,
    late_methods: dict[str, str] | None = None,
    late_edges: dict[str, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """main records + late records（供 assignment_records.jsonl 写入）。

    late_edges：每 late 学生本次采样产生的带 sampling_round / task_fm /
    task_selection_count_after 的逐条记录；缺省时按旧字段补 minimal 记录。
    """

    records = [dict(r) for r in main_result["records"]]
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    for late_student, tasks in late_assignments.items():
        method = (late_methods or {}).get(late_student, LATE_METHOD)
        edges = (late_edges or {}).get(late_student)
        if edges:
            for edge in edges:
                records.append(dict(edge) | {"assigned_at": now})
        else:
            for task_id in tasks:
                records.append(
                    {
                        "reviewer_id": late_student,
                        "task_id": task_id,
                        "assignment_batch": LATE_BATCH,
                        "assignment_method": method,
                        "seed": seed,
                        "assigned_at": now,
                    }
                )
    return records


# ---------------------------------------------------------------------------
# Task Exposure Audit + Assignment Summary（仅审计，非 Phase 2 assignment source）
# ---------------------------------------------------------------------------


def task_exposure_stats(selection_count: dict[str, int]) -> dict[str, Any]:
    """Task exposure 汇总：min / mean / median / max。"""

    if not selection_count:
        return {"min": 0, "mean": 0.0, "median": 0.0, "max": 0}
    values = sorted(selection_count.values())
    n = len(values)
    median = values[n // 2] if n % 2 else (values[n // 2 - 1] + values[n // 2]) / 2
    return {
        "min": values[0],
        "mean": sum(values) / n,
        "median": median,
        "max": values[-1],
    }


def write_task_exposure_audit(
    path: str | Path,
    rows: list[dict[str, Any]],
    selection_count: dict[str, int],
    cap: int,
) -> Path:
    """task_exposure.csv：每个 Main Task 一行（task_id/owner/fm/reviewer_count/cap/at_cap）。"""

    import csv

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    owner = {r["task_id"]: r["student_id"] for r in rows}
    task_fm = {r["task_id"]: r["hypothesized_failure_mode"] for r in rows}
    fields = ("task_id", "owner_id", "fm", "reviewer_count", "exposure_cap", "at_cap")
    with open(out, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for task_id in sorted(selection_count):
            count = selection_count[task_id]
            writer.writerow(
                {
                    "task_id": task_id,
                    "owner_id": owner.get(task_id, ""),
                    "fm": task_fm.get(task_id, ""),
                    "reviewer_count": count,
                    "exposure_cap": cap,
                    "at_cap": "true" if count >= cap else "false",
                }
            )
    return out


def write_assignment_summary(
    path: str | Path,
    main_result: dict[str, Any],
) -> Path:
    """assignment_summary.json：方法 / 参数 / task exposure / reviewer FM diversity 汇总。"""

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    reviewer_to_tasks = main_result["reviewer_to_tasks"]
    task_fm = main_result.get("task_fm", {})
    per = {
        r: len({task_fm[t] for t in ts if t in task_fm})
        for r, ts in reviewer_to_tasks.items()
    }
    diversity_stats = task_exposure_stats(per) if per else {"min": 0, "mean": 0.0, "median": 0.0, "max": 0}
    summary = {
        "method": main_result["assignment_method"],
        "seed": main_result["seed"],
        "main_pool_size": main_result["student_count"],
        "review_degree": main_result["review_degree"],
        "task_exposure_cap": main_result["task_exposure_cap"],
        "total_assignment_edges": sum(len(ts) for ts in reviewer_to_tasks.values()),
        "task_exposure": task_exposure_stats(main_result["selection_count"]),
        "reviewer_fm_diversity": diversity_stats,
    }
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# Legacy（仅历史复现；正式 build 路径禁止调用）
# ---------------------------------------------------------------------------


def assign_main_cyclic_legacy(
    rows: list[dict[str, Any]],
    *,
    seed: int = DEFAULT_SEED,
    reviews: int = 2,
) -> dict[str, Any]:
    """LEGACY：balanced cyclic-shift（旧版 Main 方法，已停用）。

    仅供复现 assignment_method == "balanced_cyclic_shift" 的历史 artifact。
    正式 Main Assignment 使用 assign_main（fm_stratified_min_count_sampling）。
    正式 build_assignment / pipeline 禁止调用本函数。
    """

    students = sorted({r["student_id"] for r in rows})
    own_task = {r["student_id"]: r["task_id"] for r in rows}
    tasks = sorted(own_task.values())
    assert len(own_task) == len(students) == len(rows), "每学生必须恰好一个 task"

    n = len(students)
    if n < reviews + 1:
        raise ValueError(f"main pool 学生数 {n} 必须大于 reviews={reviews} 才能无自分配")

    rng = random.Random(seed)
    pi = list(range(n))
    rng.shuffle(pi)
    reviewer_at = [None] * n
    for i, s in enumerate(students):
        reviewer_at[pi[i]] = s

    reviewer_to_tasks: dict[str, list[str]] = {}
    task_to_reviewers: dict[str, list[str]] = {t: [] for t in tasks}
    records: list[dict[str, Any]] = []
    for i, s in enumerate(students):
        pos = pi[i]
        got: list[str] = []
        for shift in range(1, reviews + 1):
            owner_student = reviewer_at[(pos + shift) % n]
            task_id = own_task[owner_student]
            got.append(task_id)
            task_to_reviewers[task_id].append(s)
            records.append(
                {
                    "reviewer_id": s,
                    "task_id": task_id,
                    "assignment_batch": MAIN_BATCH,
                    "assignment_method": LEGACY_CYCLIC_METHOD,
                    "assignment_seed": seed,
                }
            )
        reviewer_to_tasks[s] = got

    return {
        "seed": seed,
        "assignment_batch": MAIN_BATCH,
        "assignment_method": LEGACY_CYCLIC_METHOD,
        "reviews_per_student": reviews,
        "student_count": n,
        "task_count": len(tasks),
        "reviewer_to_tasks": reviewer_to_tasks,
        "task_to_reviewers": task_to_reviewers,
        "records": records,
    }


def pick_late_tasks_legacy(
    late_student_id: str,
    frozen_rows: list[dict[str, Any]],
    *,
    seed: int = DEFAULT_SEED,
    reviews: int = 2,
    task_load: dict[str, int] | None = None,
    own_fm: str | None = None,
) -> tuple[list[str], str]:
    """LEGACY：旧版 late（exact/greedy FM-diversity），已停用。正式 late 使用 pick_late_tasks。"""

    import math

    candidates = [r for r in frozen_rows if r["student_id"] != late_student_id]
    if len(candidates) < reviews:
        raise ValueError(
            f"frozen main pool 可选题不足：late student {late_student_id} 需要 {reviews} 题，"
            f"候选仅 {len(candidates)} 题"
        )
    if own_fm is None:
        own_fm = next(
            (r["hypothesized_failure_mode"] for r in frozen_rows
             if r["student_id"] == late_student_id),
            "",
        )
    load = dict(task_load or {})
    student_seed = _student_seed(late_student_id, seed)
    if math.comb(len(candidates), reviews) <= MAX_LATE_COMBINATIONS:
        return _late_exact(candidates, own_fm, reviews, student_seed, load), LATE_EXACT_METHOD
    return _late_greedy(candidates, own_fm, reviews, student_seed, load), LATE_GREEDY_METHOD


def _late_exact(candidates: list[dict[str, Any]], own_fm: str, reviews: int,
                seed: int, load: dict[str, int]) -> list[str]:
    """LEGACY helper：精确枚举 K-combination（旧版 late 方法内部实现）。"""

    import itertools

    rng = random.Random(seed)
    pool = list(candidates)
    rng.shuffle(pool)
    best: list[str] | None = None
    best_key: tuple[int, int] | None = None
    for combo in itertools.combinations(pool, reviews):
        distinct = len({c["hypothesized_failure_mode"] for c in combo} - {own_fm})
        load_sum = sum(load.get(c["task_id"], 0) for c in combo)
        key = (-distinct, load_sum)
        if best_key is None or key < best_key:
            best_key = key
            best = [c["task_id"] for c in combo]
    assert best is not None
    assert len(set(best)) == len(best), "late reviewer 不允许重复 Task"
    return best


def _late_greedy(candidates: list[dict[str, Any]], own_fm: str, reviews: int,
                 seed: int, load: dict[str, int]) -> list[str]:
    """LEGACY helper：greedy fallback（旧版 late 方法内部实现）。"""

    rng = random.Random(seed)
    pool = list(candidates)
    rng.shuffle(pool)
    picked: list[str] = []
    picked_fms: set[str] = set()
    for _ in range(reviews):
        remaining = [c for c in pool if c["task_id"] not in picked]
        fresh = [c for c in remaining if c["hypothesized_failure_mode"] not in picked_fms]
        choosable = fresh if fresh else remaining
        best_idx = min(
            range(len(choosable)),
            key=lambda i: (load.get(choosable[i]["task_id"], 0), i),
        )
        pick = choosable[best_idx]
        picked.append(pick["task_id"])
        picked_fms.add(pick["hypothesized_failure_mode"])
    assert len(set(picked)) == len(picked), "late reviewer 不允许重复 Task"
    return picked