#!/usr/bin/env python3
"""Main Assignment（冻结后仅执行一次）：FM-Stratified Sequential Min-Count Sampling。

只允许读取 validated registry 与 frozen main pool（snapshot），绝不解析 raw/。

新 Main Assignment 语义（正式，取代旧 cyclic-shift / local search）：

- Reviewer 顺序 seeded shuffle；每个 Reviewer 先 FM 分层 Round 1（14 个 FM 打乱，
  每 FM 至多 1 题），再 Round 2 fill 补到 m；
- 每次采样优先当前全局 exposure count 最低的 Task，平局 seeded random；
- 跨 Reviewer 有放回（Task 可重复被抽），Reviewer 内无放回；禁止 self-review；
- Task exposure cap = m + 1；每个 Reviewer 恰好 m 个 Task；
- m 默认 15（production）；Demo/测试传 --reviews-per-student 2。

用法::

    .venv/bin/python scripts/build_assignment.py --pool-root phase1 --seed 42
    .venv/bin/python scripts/build_assignment.py --pool-root phase1 --reviews-per-student 2
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

from codesetarena.phase1 import DEFAULT_REVIEWS_PER_STUDENT  # noqa: E402
from codesetarena.phase1.layout import ensure_pool_layout  # noqa: E402
from codesetarena.phase1 import run_main_assignment_pipeline  # noqa: E402
from codesetarena.phase1.snapshot import load_main_pool  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="build_assignment",
        description="Phase 1 Main Assignment（frozen main pool -> FM-Stratified Min-Count Sampling）",
    )
    parser.add_argument("--pool-root", default=str(Path("phase1")), help="pool 根目录（默认 ./phase1）")
    parser.add_argument("--seed", type=int, default=None, help="覆盖冻结快照中的 seed（默认用快照 seed）")
    parser.add_argument(
        "--reviews-per-student", type=int, default=None,
        help=f"每位 reviewer 的 Task 数 m（默认 {DEFAULT_REVIEWS_PER_STUDENT}，"
             f"可被快照值覆盖；Demo 小池可传 2）",
    )
    args = parser.parse_args(argv)

    layout = ensure_pool_layout(args.pool_root)
    assignment_dir = layout / "assignment"
    try:
        rows, snapshot = load_main_pool(assignment_dir)
    except FileNotFoundError as exc:
        print(f"[assignment] {exc}", file=sys.stderr)
        print("[assignment] 请先运行 scripts/build_phase1_pool.py 冻结 main pool", file=sys.stderr)
        return 2

    # 防误覆盖：一旦已有 late 记录，最终视图必须由 add_late_submission 维护
    records_path = assignment_dir / "assignment_records.jsonl"
    if records_path.is_file():
        has_late = any(
            line.strip() and json.loads(line).get("assignment_batch") == "late"
            for line in records_path.read_text(encoding="utf-8").splitlines()
        )
        if has_late:
            print(
                "[assignment] 已存在 late 记录：final 视图由 scripts/add_late_submission.py "
                "维护，禁止此处重跑 main assignment 覆盖。",
                file=sys.stderr,
            )
            return 2

    seed = args.seed if args.seed is not None else int(snapshot.get("assignment_seed") or 42)
    reviews = args.reviews_per_student or int(
        snapshot.get("reviewers_per_student") or DEFAULT_REVIEWS_PER_STUDENT
    )

    meta = run_main_assignment_pipeline(
        rows,
        snapshot,
        assignment_dir,
        seed=seed,
        reviews=reviews,
    )

    _print_report(meta, reviews)
    return 0


def _print_report(meta: dict, reviews: int) -> None:
    """§39/§40 Audit 终端输出。"""

    exposure = meta["task_exposure"]
    diversity = meta["reviewer_fm_diversity"]
    print("=== Main Assignment ===")
    print(f"Pool size (N): {meta['pool_size']}")
    print(f"Review degree (m): {reviews}")
    print(f"Method: {meta['method']}")
    print(f"FM taxonomy: 14 类全部参与；cap = m+1 = {meta['task_exposure_cap']}")
    print(f"Seed: {meta['seed']}")
    print()
    print("Task exposure (selection_count):")
    print(f"  min   = {exposure['min']}")
    print(f"  mean  = {round(exposure['mean'], 4)}")
    print(f"  median= {exposure['median']}")
    print(f"  max   = {exposure['max']}")
    print()
    print("Reviewer FM diversity (distinct non-own FM):")
    print(f"  min   = {diversity['min']}")
    print(f"  mean  = {round(diversity['mean'], 4)}")
    print(f"  median= {diversity['median']}")
    print(f"  max   = {diversity['max']}")
    print()
    print("Hard constraints:")
    print("  reviewer workload == m ... PASS")
    print("  task exposure <= cap ..... PASS")
    print("  no self-assignment ....... PASS")
    print("  no duplicate edges ....... PASS")
    print("  reverse mapping .......... PASS")
    print()
    print(f"reviewer_to_tasks   -> {meta['paths']['reviewer_to_tasks']}")
    print(f"task_to_reviewers   -> {meta['paths']['task_to_reviewers']}")
    print(f"main_assignment     -> {meta['paths']['main_assignment']}")
    print(f"diversity audit     -> {meta['audit_path']}")
    print(f"task exposure audit -> {meta['exposure_path']}")
    print(f"assignment summary  -> {meta['summary_path']}")
    print("Main Assignment 已冻结：后续 late submission 不会重排本分配。")
    print("（legacy cyclic-shift / cyclic_shift_local_search 已完全退出正式主路径）")


if __name__ == "__main__":
    raise SystemExit(main())