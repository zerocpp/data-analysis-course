#!/usr/bin/env python3
"""Late Submission 处理：加入 canonical -> 全量重校验 -> Registry 更新 -> current FM Distribution -> incremental assignment。

关键约束（硬）：

- 每加入一个 late submission，都对「当前全部 active submissions」重跑全局校验
  （task_id 唯一 / active student_id 唯一 / identity consistency），不是只查新包；
- main assignment 绝不重排（frozen main pool 只读）：late reviewer 从冻结池取
  K 题（FM-aware diversity，退化 seeded random）；late Task 允许 0 reviewers；
- final reviewer_to_tasks / task_to_reviewers 合并 main + late 并校验反向一致。

用法::

    .venv/bin/python scripts/add_late_submission.py --late-dir <dir>
    .venv/bin/python scripts/add_late_submission.py --late-zip <x.zip> --pool-root phase1 --seed 42
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SRC = ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from codesetarena.phase1 import (  # noqa: E402
    DEFAULT_REVIEWS_PER_STUDENT,
    append_registry_rows,
    assign_late,
    build_fm_distribution,
    merge_final_mappings,
    prepare_canonical,
    records_after_main,
    scan_canonical,
    validate_batch,
    verify_main_unchanged,
    verify_reverse_consistency,
)
from codesetarena.phase1.layout import ensure_pool_layout  # noqa: E402
from codesetarena.phase1.snapshot import load_main_pool  # noqa: E402
from codesetarena.phase1.validation import write_validation_report  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="add_late_submission",
        description="Phase 1 Late Submission: canonical -> full revalidation -> registry -> coverage -> incremental assignment",
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--late-dir", help="late 提交目录（含 task.json 的 workspace）")
    src.add_argument("--late-zip", help="late 提交 ZIP（Export 产物）")
    parser.add_argument("--pool-root", default=str(Path("phase1")), help="pool 根目录（默认 ./phase1）")
    parser.add_argument("--seed", type=int, default=None, help="覆盖快照 seed（默认用快照 seed）")
    parser.add_argument(
        "--reviews-per-student", type=int, default=None,
        help="覆盖快照 reviews-per-student（默认用快照值）",
    )
    args = parser.parse_args(argv)

    layout = ensure_pool_layout(args.pool_root)
    try:
        frozen_rows, snapshot = load_main_pool(layout / "assignment")
    except FileNotFoundError as exc:
        print(f"[late] {exc}", file=sys.stderr)
        print("[late] 请先运行 scripts/build_phase1_pool.py 冻结 main pool", file=sys.stderr)
        return 2

    seed = args.seed if args.seed is not None else int(snapshot.get("assignment_seed") or 42)
    reviews = args.reviews_per_student or int(
        snapshot.get("reviewers_per_student") or DEFAULT_REVIEWS_PER_STUDENT
    )

    # [1] 加入 canonical candidate（raw 永不修改；生成 T_ ID 记录到 audit）
    source = Path(args.late_dir) if args.late_dir else Path(args.late_zip)
    audit_log = layout / "audit" / "task_id_corrections.jsonl"
    canonical_dir = prepare_canonical(source, layout / "canonical", audit_log=audit_log)
    if canonical_dir is None:
        print("[ERROR] late 提交无法 canonicalize（缺 task.json / student_id / 非合法 JSON）", file=sys.stderr)
        return 1

    # [2] 全量重校验（当前全部 active submissions，不是只查新包）
    package_dirs = scan_canonical(layout / "canonical")
    report = validate_batch(package_dirs)
    write_validation_report(report, layout / "validation")
    if report["status"] != "PASS":
        print("[ERROR] 全量重校验失败（含既有提交）", file=sys.stderr)
        for error in report["errors"]:
            print(f"[ERROR] {error['code']}: {error.get('detail') or ''}", file=sys.stderr)
        print("STOP: registry / coverage / assignment 未更新。", file=sys.stderr)
        return 1
    print(f"[PASS] full registry revalidated: {report['package_count']}")

    # [3] Registry 更新（只追加新 task_id，main 行永不修改）
    new_rows = [
        {
            "task_id": p["task_id"],
            "student_id": p["student_id"],
            "hypothesized_failure_mode": p["hypothesized_failure_mode"],
            "task_path": p["dir"],
            "submission_batch": "late",
            "package_sha256": p.get("sha256", ""),
        }
        for p in report["packages"]
        if p["task_id"] and not any(r["task_id"] == p["task_id"] for r in frozen_rows)
    ]
    current_rows = append_registry_rows(layout / "registry", new_rows, submission_batch="late")
    print(f"[PASS] registry updated: {len(current_rows)} rows")

    # [4] current FM Distribution 更新（main 快照不覆盖）
    fm_current = build_fm_distribution(current_rows, layout / "coverage", snapshot_tag="current")
    print(f"[PASS] current FM distribution updated -> {fm_current['csv']}")

    # [5] incremental assignment（frozen main pool 只读；main mapping 必须未变）
    # main 冻结映射以 main_assignment.json 的 main_reviewer_to_tasks 为准（可能已
    # 被 FM diversity optimizer 优化过，不能用重新计算的 cyclic baseline 替代）。
    loaded_main = _load_json(layout / "assignment" / "main_assignment.json") or {}
    main_mapping: dict[str, list[str]] = loaded_main.get("main_reviewer_to_tasks")
    if not main_mapping:
        print("[ERROR] main_assignment.json 缺少 main_reviewer_to_tasks（请先运行 "
              "scripts/build_assignment.py 生成冻结 main）。", file=sys.stderr)
        return 1

    loaded_final = _load_json(layout / "assignment" / "reviewer_to_tasks.json")
    late_assignments: dict[str, list[str]] = {}
    late_methods: dict[str, str] = {}
    late_edges: dict[str, list[dict]] = {}
    frozen_students = {r["student_id"] for r in frozen_rows}
    late_students = sorted({r["student_id"] for r in current_rows} - frozen_students)
    late_owned: list[str] = []
    for row in current_rows:
        if row["student_id"] not in frozen_students:
            late_owned.append(row["task_id"])

    # task_load：当前每个 main task 已有 reviewer 数（read task_to_reviewers，不是
    # reviewer_to_tasks；main m + 先前 late 追加）。多名 late student 依次分配时
    # 实时更新，保证任何 Task 累计 exposure 不超 cap。
    loaded_t2r = _load_json(layout / "assignment" / "task_to_reviewers.json")
    task_load = {t: len(rs) for t, rs in (loaded_t2r or {}).items()}
    registry_own_fm = {r["student_id"]: r["hypothesized_failure_mode"] for r in current_rows}

    for student in late_students:
        picked, method, edges = assign_late(
            student, frozen_rows, seed=seed, reviews=reviews,
            task_load=task_load, own_fm=registry_own_fm.get(student, ""),
            return_method=True, return_edges=True,
        )
        late_assignments[student] = picked
        late_methods[student] = method
        late_edges[student] = edges
        for task_id in picked:
            task_load[task_id] = task_load.get(task_id, 0) + 1  # 后续 late 可见新增 exposure
        print(f"[PASS] late reviewer {student} -> {picked} (method={method})")

    if loaded_final:
        unchanged = verify_main_unchanged(main_mapping, loaded_final)
        if unchanged:
            print("[ERROR] main assignment 被修改，late 流程中止：", file=sys.stderr)
            for line in unchanged:
                print(f"  {line}", file=sys.stderr)
            return 1

    # 以存储的 main 映射为 merge 基准（保持 frozen main 原样）
    main_proxy = {
        "seed": loaded_main.get("seed", seed),
        "assignment_batch": "main",
        "assignment_method": loaded_main.get("assignment_method", "balanced_cyclic_shift"),
        "reviews_per_student": loaded_main.get("reviews_per_student", reviews),
        "review_degree": loaded_main.get("review_degree", reviews),
        "task_exposure_cap": loaded_main.get("task_exposure_cap", reviews + 1),
        "student_count": len(main_mapping),
        "task_count": len(main_mapping),
        "reviewer_to_tasks": main_mapping,
        "task_to_reviewers": _t2r_from_mapping(main_mapping),
    }
    existing_main_records = [r for r in _load_jsonl(layout / "assignment" / "assignment_records.jsonl")
                             if r.get("assignment_batch") == "main"]
    main_proxy["records"] = existing_main_records or [
        {
            "reviewer_id": r, "task_id": t, "assignment_batch": "main",
            "assignment_method": main_proxy["assignment_method"], "assignment_seed": seed,
        }
        for r in sorted(main_mapping) for t in main_mapping[r]
    ]

    reviewer_to_tasks, task_to_reviewers = merge_final_mappings(
        main_proxy, late_assignments, late_owned_tasks=late_owned
    )
    records = records_after_main(
        main_proxy, late_assignments, seed=seed, late_methods=late_methods,
        late_edges=late_edges,
    )

    if loaded_final:
        # 保留此前 late 记录（若有），再追加本次新增；本次被重分配的学生旧记录
        # 必须被替换（同一学生的新分配覆盖旧分配），其余按 (batch, reviewer, task) 去重
        reader = _load_jsonl(layout / "assignment" / "assignment_records.jsonl")
        prior = [
            r for r in reader
            if r.get("assignment_batch") == "late" and r.get("reviewer_id") not in set(late_students)
        ]
        records = [r for r in records if r.get("assignment_batch") == "main"] + prior + \
            [r for r in records if r.get("assignment_batch") == "late"]
        records = _dedupe_records(records)

    from codesetarena.phase1 import write_assignment_outputs

    # 保留 main 快照的 provenance 扩展字段（sampling_mode / task_exposure_cap 等），
    # late 重写 main_assignment.json 时不得丢失（§24 / §32）
    base_keys = {"seed", "assignment_batch", "assignment_method", "reviews_per_student",
                 "student_count", "task_count", "anonymized", "frozen_snapshot",
                 "main_reviewer_to_tasks"}
    extra_meta = {k: v for k, v in loaded_main.items() if k not in base_keys}

    paths = write_assignment_outputs(
        layout / "assignment",
        main_result=main_proxy,
        reviewer_to_tasks=reviewer_to_tasks,
        task_to_reviewers=task_to_reviewers,
        records=records,
        snapshot=snapshot,
        main_mapping=main_mapping,
        extra_meta=extra_meta,
    )

    # final diversity audit：覆盖 main + late 全部 active reviewers（batch 区分）
    from codesetarena.phase1.optimize import write_diversity_audit

    audit_path = write_diversity_audit(
        layout / "assignment" / "assignment_diversity.csv",
        current_rows, reviewer_to_tasks, frozen_students,
        records=records, expected_review_count=reviews,
    )

    errors = verify_reverse_consistency(reviewer_to_tasks, task_to_reviewers)
    if errors:
        print("[ERROR] 最终 mapping 反向一致性失败:", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 1

    print("[PASS] main assignment unchanged")
    print(f"[PASS] final reviewer_to_tasks -> {paths['reviewer_to_tasks']}")
    print(f"[PASS] final task_to_reviewers -> {paths['task_to_reviewers']}")
    print(f"[PASS] assignment records     -> {paths['records']}")
    print(f"[PASS] diversity audit        -> {audit_path}")
    print()
    print("=== Final ===")
    print(f"active students = {len(reviewer_to_tasks)}")
    print(f"active tasks    = {len(task_to_reviewers)}")
    zero = [t for t, rs in task_to_reviewers.items() if not rs]
    print(f"tasks with 0 reviewers = {len(zero)} (late tasks 允许): {zero}")
    return 0


def _load_json(path: Path):
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _t2r_from_mapping(main_mapping: dict[str, list[str]]) -> dict[str, list[str]]:
    t2r: dict[str, list[str]] = {}
    for reviewer, tasks in main_mapping.items():
        for task in tasks:
            t2r.setdefault(task, []).append(reviewer)
    return t2r


def _load_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def _dedupe_records(records: list[dict]) -> list[dict]:
    """按 (assignment_batch, reviewer_id, task_id) 去重，保留首次出现顺序。"""

    seen: set[tuple[str, str, str]] = set()
    out: list[dict] = []
    for record in records:
        key = (record.get("assignment_batch", ""), record.get("reviewer_id", ""),
               record.get("task_id", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(record)
    return out


if __name__ == "__main__":
    raise SystemExit(main())