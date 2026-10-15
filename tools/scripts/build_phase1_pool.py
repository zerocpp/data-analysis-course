#!/usr/bin/env python3
"""Phase 1 总入口（教师端）：Build/locate canonical -> Validate -> Registry -> FM Distribution -> Freeze Main Pool -> (可选) Assignment。

数据流严格单向（Assignment 唯一合法输入是 validated registry / frozen main pool，
本脚本绝不读取 raw/）::

    canonical/ -> Batch Validator（只读） -> phase1_task_registry -> FM Coverage
      -> frozen main pool（不可变） -> main assignment（FM-Stratified Min-Count Sampling）

任何 blocking validation error（duplicate task_id / duplicate active student_id /
invalid FM / missing field / malformed JSON / identity mismatch）都会使本脚本以
非零退出码停止，且 Coverage 与 Assignment 均不生成。

用法::

    .venv/bin/python scripts/build_phase1_pool.py --canonical-root phase1/canonical
    .venv/bin/python scripts/build_phase1_pool.py --canonical-root phase1/canonical --assign --seed 42
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
_SRC = ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from codesetarena.constants import HYPOTHESIZED_FM_ORDER  # noqa: E402
from codesetarena.phase1 import (  # noqa: E402
    DEFAULT_REVIEWS_PER_STUDENT,
    build_fm_distribution,
    build_registry,
    freeze_main_pool,
    prepare_canonical,
    run_main_assignment_pipeline,
    scan_canonical,
    validate_batch,
    write_registry,
)
from codesetarena.phase1.layout import ensure_pool_layout  # noqa: E402
from codesetarena.phase1.validation import validated_packages, write_validation_report  # noqa: E402


def _fmt_step(index: int, total: int, name: str, status: str) -> str:
    dots = " " * max(1, 18 - len(name))
    return f"[{index}/{total}] {name}{dots} {status}"


def _validate_or_stop(package_dirs: list[Path], validation_dir: Path) -> dict[str, Any] | None:
    """执行批量校验；PASS 写报告返回 dict，ERROR 打印明细并返回 None（调用方退出）。"""

    report = validate_batch(package_dirs)
    path = write_validation_report(report, validation_dir)
    if report["status"] != "PASS":
        print("[1/6] Phase 1 validation ........ ERROR", file=sys.stderr)
        for error in report["errors"]:
            print(f"[ERROR] {error['code']}", file=sys.stderr)
            if error.get("detail"):
                print(f"  {error['detail']}", file=sys.stderr)
            if error["code"] == "DUPLICATE_TASK_ID":
                for member in error.get("students", []):
                    print(
                        f"  student {member['student_id']}  package {member['package']}",
                        file=sys.stderr,
                    )
            elif error["code"] == "DUPLICATE_ACTIVE_STUDENT_ID":
                for package in error.get("packages", []):
                    print(f"  package {package}", file=sys.stderr)
        print("STOP: Coverage 与 Assignment 未生成。请人工修正后重跑。", file=sys.stderr)
        return None
    print("[1/6] Phase 1 validation ........ PASS")
    print(f"  validation report -> {path}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="build_phase1_pool",
        description="Phase 1: Build -> Validate -> Registry -> FM Distribution -> Freeze Main Pool",
    )
    parser.add_argument("--pool-root", default=str(Path("phase1")), help="pool 根目录（默认 ./phase1）")
    parser.add_argument(
        "--canonical-root", default=None,
        help="canonical 提交目录（默认 <pool-root>/canonical）",
    )
    parser.add_argument(
        "--prepare-zips", default=None, nargs="*",
        help="Batch Build 前置：把这些学生 ZIP 整理为 canonical（--prepare-zips DIR 或多个 zip）",
    )
    parser.add_argument("--seed", type=int, default=42, help="assignment seed（默认 42）")
    parser.add_argument(
        "--reviews-per-student", type=int, default=DEFAULT_REVIEWS_PER_STUDENT,
        help=f"每位 reviewer 分到的 Task 数 m（默认 {DEFAULT_REVIEWS_PER_STUDENT}，"
             f"Production 默认；Demo 小池请显式传 2）",
    )
    parser.add_argument(
        "--main-pool-size", type=int, default=None, help="预期 main pool 人数（默认=注册表行数）"
    )
    parser.add_argument("--assign", action="store_true", help="冻结后立即执行 Main Assignment")
    args = parser.parse_args(argv)

    pool_root = Path(args.pool_root).resolve()
    canonical_root = (
        Path(args.canonical_root).resolve() if args.canonical_root else pool_root / "canonical"
    )
    layout = ensure_pool_layout(pool_root)

    # [0] Batch Build 前置（可选）：raw ZIP -> canonical（raw 永不修改；缺失 task_id
    #     生成 T_ 全局 ID 并记录 audit/task_id_corrections.jsonl）
    if args.prepare_zips:
        audit_log = layout / "audit" / "task_id_corrections.jsonl"
        sources: list[Path] = []
        for item in args.prepare_zips:
            p = Path(item)
            if p.is_dir():
                sources.extend(sorted(p.glob("*.zip")))
            elif p.is_file():
                sources.append(p)
        prepared = 0
        for source in sources:
            dest = prepare_canonical(source, canonical_root, audit_log=audit_log)
            if dest is not None:
                prepared += 1
        print(f"[0/6] Batch Build (zips -> canonical) .. PASS（{prepared}/{len(sources)} 个包）")

    # [1/6] Phase 1 validation（locate canonical submissions + batch checks）
    package_dirs = scan_canonical(canonical_root)
    report = _validate_or_stop(package_dirs, layout / "validation")
    if report is None:
        return 1

    # [2/6] Task registry
    rows = build_registry(validated_packages(report), submission_batch="main")
    registry_paths = write_registry(rows, layout / "registry")
    print("[2/6] Task registry ............ PASS")
    print(f"  registry csv -> {registry_paths['csv']}")

    # [3/6] Owner mappings（registry 同批产出）
    print("[3/6] Owner mappings ........... PASS")
    print(f"  task_to_owner     -> {registry_paths['task_to_owner']}")
    print(f"  student_to_tasks  -> {registry_paths['student_to_tasks']}")

    # [4/6] FM distribution（main 快照）
    fm_main = build_fm_distribution(rows, layout / "coverage", snapshot_tag="main")
    print("[4/6] FM distribution .......... PASS")
    print(f"  fm csv -> {fm_main['csv']}")
    print(f"  fm png -> {fm_main['png']}")

    # [5/6] Main pool snapshot（不可变冻结）
    snapshot = freeze_main_pool(
        rows,
        layout / "assignment",
        seed=args.seed,
        reviewers_per_student=args.reviews_per_student,
        fm_distribution_paths=fm_main,
        main_pool_size=args.main_pool_size or len(rows),
    )
    print("[5/6] Main pool snapshot ....... PASS")
    print(f"  snapshot -> {layout / 'assignment' / 'main_pool_snapshot.json'}")

    # [6/6] Assignment（可选；走与 build_assignment.py 完全相同的 runner）
    if args.assign:
        meta = run_main_assignment_pipeline(
            rows,
            snapshot,
            layout / "assignment",
            seed=args.seed,
            reviews=args.reviews_per_student,
        )
        print("[6/6] Assignment ............... PASS")
        print(f"  method={meta['method']} (m={meta['review_degree']}, "
              f"cap={meta['task_exposure_cap']})")
        print(f"  task exposure min/mean/max = "
              f"{meta['task_exposure']['min']}/"
              f"{round(meta['task_exposure']['mean'], 4)}/"
              f"{meta['task_exposure']['max']}")
        print(f"  reviewer_to_tasks -> {meta['paths']['reviewer_to_tasks']}")
        print(f"  task_to_reviewers -> {meta['paths']['task_to_reviewers']}")
        print(f"  diversity audit   -> {meta['audit_path']}")
        print(f"  task exposure     -> {meta['exposure_path']}")
        print(f"  assignment summary-> {meta['summary_path']}")
    else:
        print("[6/6] Assignment ............... SKIP")

    _print_summary(rows)
    return 0


def _print_summary(rows: list[dict[str, Any]]) -> None:
    print()
    print(f"Valid students: {len({r['student_id'] for r in rows})}")
    print(f"Valid tasks: {len(rows)}")
    print()
    print("FM distribution:")
    counts = {fm: 0 for fm in HYPOTHESIZED_FM_ORDER}
    for row in rows:
        if row["hypothesized_failure_mode"] in counts:
            counts[row["hypothesized_failure_mode"]] += 1
    for fm in HYPOTHESIZED_FM_ORDER:
        print(f"  {fm:<28} {counts[fm]}")
    print()
    print("Ready for Assignment.")


if __name__ == "__main__":
    raise SystemExit(main())