"""FM Distribution Builder（Phase 1 Coverage）—— 只读 registry CSV/validated rows。

输出每个快照三个文件（main 冻结时存 *_main.*，以后每次 late 更新 *_current.*，
绝不覆盖 main 快照）::

    phase1_fm_distribution_{main,current}.csv
    phase1_fm_distribution_{main,current}.json
    phase1_fm_distribution_{main,current}.png

统计语义：

- 14 个合法 FM 固定顺序（HYPOTHESIZED_FM_ORDER = FM01-FM14 全集，含 FM10/FM13），
  count=0 也保留行；
- proportion = count / total（6 位小数）；
- 这是 **hypothesized（自声明 / 未验证）** 分布，不是 observed/validated 分布，
  禁止对 FM 重排、打分、推荐成绩。

PNG：300 dpi，标题 "Phase 1 Hypothesized Failure Mode Distribution"，副标题
"Self-declared / Unvalidated"，柱顶标注 count 与百分比。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ..constants import HYPOTHESIZED_FM_ORDER

COVERAGE_FILENAME_PREFIX = "phase1_fm_distribution"
MAIN_SNAPSHOT_TAG = "main"
CURRENT_SNAPSHOT_TAG = "current"


def _aggregate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按固定顺序统计 12 类 FM；count 可为 0。"""

    counts = {fm: 0 for fm in HYPOTHESIZED_FM_ORDER}
    for row in rows:
        fm = row.get("hypothesized_failure_mode") or ""
        if fm in counts:
            counts[fm] += 1
    total = sum(counts.values())
    distribution = []
    for fm in HYPOTHESIZED_FM_ORDER:
        count = counts[fm]
        distribution.append(
            {
                "hypothesized_failure_mode": fm,
                "count": count,
                "proportion": round(count / total, 6) if total else 0.0,
            }
        )
    return distribution


def _write_png(distribution: list[dict[str, Any]], path: Path) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:  # PNG 是教师 Dashboard 产物，缺失时给出可执行提示
        raise RuntimeError(
            "生成 FM PNG 需要 matplotlib：.venv/bin/python -m pip install matplotlib"
        ) from exc

    labels = [d["hypothesized_failure_mode"] for d in distribution]
    counts = [d["count"] for d in distribution]

    fig, ax = plt.subplots(figsize=(11, 6.5), dpi=300)
    colors = ["#4C72B0" if c > 0 else "#D3D3D3" for c in counts]
    bars = ax.bar(labels, counts, color=colors, edgecolor="#33415C", linewidth=0.6)

    ax.set_title(
        "Phase 1 Hypothesized Failure Mode Distribution\nSelf-declared / Unvalidated",
        fontsize=13,
        fontweight="bold",
        loc="center",
    )
    ax.set_xlabel("Hypothesized Failure Mode")
    ax.set_ylabel("Number of Tasks")
    ax.set_ylim(0, (max(counts) * 1.25) if max(counts) else 1.0)

    for bar, d in zip(bars, distribution):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 0.2,
            f"{d['count']}\n({d['proportion'] * 100:.1f}%)",
            ha="center",
            va="bottom",
            fontsize=8,
        )

    ax.tick_params(axis="x", rotation=45)
    fig.tight_layout()
    fig.savefig(path, dpi=300)
    plt.close(fig)


def build_fm_distribution(
    rows: list[dict[str, Any]],
    coverage_dir: str | Path,
    *,
    snapshot_tag: str = MAIN_SNAPSHOT_TAG,
) -> dict[str, Path]:
    """由 registry rows 生成 CSV + JSON + PNG；返回 {ext: path}。"""

    out = Path(coverage_dir)
    out.mkdir(parents=True, exist_ok=True)
    distribution = _aggregate(rows)
    base = out / f"{COVERAGE_FILENAME_PREFIX}_{snapshot_tag}"

    data = {
        "snapshot_tag": snapshot_tag,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "total_tasks": sum(d["count"] for d in distribution),
        "fm_count": len(HYPOTHESIZED_FM_ORDER),
        "distribution": distribution,
    }

    csv_path = base.with_suffix(".csv")
    with open(csv_path, "w", encoding="utf-8", newline="") as fh:
        fh.write("hypothesized_failure_mode,count,proportion\n")
        for d in distribution:
            fh.write(f"{d['hypothesized_failure_mode']},{d['count']},{d['proportion']}\n")

    json_path = base.with_suffix(".json")
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    png_path = base.with_suffix(".png")
    _write_png(distribution, png_path)

    return {"csv": csv_path, "json": json_path, "png": png_path}


def load_fm_distribution(json_path: str | Path) -> dict[str, Any]:
    """读取已生成的 distribution JSON。"""

    return json.loads(Path(json_path).read_text(encoding="utf-8"))