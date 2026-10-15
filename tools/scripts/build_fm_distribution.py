#!/usr/bin/env python3
"""独立重绘 FM Distribution（教师查看用）——只读 registry，不重跑 pipeline。

数据源唯一：registry/phase1_task_registry.json（或自定义 CSV/目录）；绝不解析 raw/。
输出 trio（snapshot_tag 默认 current；main 快照冻结后不覆盖）::

    <coverage>/phase1_fm_distribution_<tag>.{csv,json,png}

PNG：14 类 FM 固定顺序（count=0 也保留），300 dpi，柱顶 count + 百分比，
标题 "Phase 1 Hypothesized Failure Mode Distribution"，副标题 "Self-declared / Unvalidated"。

用法::

    .venv/bin/python scripts/build_fm_distribution.py --pool-root pool_demo20
    .venv/bin/python scripts/build_fm_distribution.py --pool-root phase1 --snapshot-tag main
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SRC = ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from codesetarena.phase1 import build_fm_distribution, load_registry  # noqa: E402
from codesetarena.phase1.layout import ensure_pool_layout  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="build_fm_distribution",
        description="从 validated registry 重绘 Phase 1 FM Distribution（CSV/JSON/PNG）",
    )
    parser.add_argument("--pool-root", default=str(Path("phase1")), help="pool 根目录（默认 ./phase1）")
    parser.add_argument(
        "--registry-dir", default=None,
        help="registry 目录（默认 <pool-root>/registry）",
    )
    parser.add_argument(
        "--snapshot-tag", choices=("main", "current"), default="current",
        help="快照标签（默认 current；main 用于对照冻结时刻）",
    )
    args = parser.parse_args(argv)

    layout = ensure_pool_layout(args.pool_root)
    registry_dir = Path(args.registry_dir).resolve() if args.registry_dir else layout / "registry"
    rows = load_registry(registry_dir)
    if not rows:
        print(f"[fm] registry 为空：{registry_dir}", file=sys.stderr)
        return 1

    out_dir = layout / "coverage"
    paths = build_fm_distribution(rows, out_dir, snapshot_tag=args.snapshot_tag)
    print(f"[fm] rows = {len(rows)} (snapshot_tag={args.snapshot_tag})")
    print(f"[fm] csv -> {paths['csv']}")
    print(f"[fm] png -> {paths['png']}")
    print("[fm] 注：Hypothesized（自声明 / 未验证）分布，14 类固定顺序，count=0 保留。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())