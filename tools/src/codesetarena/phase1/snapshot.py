"""Main Assignment Pool 冻结 / 加载。

195 份 main 提交 Validation+Registry+Coverage 通过后，把「当时」的注册表冻结为
不可变快照（Assignment 只允许基于该快照：新来的 late submission 绝不重排
main 分配）。快照写入 assignment/ 目录：main_pool_registry.csv + main_pool_snapshot.json
（记录 frozen timestamp / task 数 / student 数 / registry hash / assignment seed /
FM distribution 快照路径）。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .registry import (
    REGISTRY_CSV_COLUMNS,
    registry_rows_sha256,
)

MAIN_POOL_REGISTRY_CSV = "main_pool_registry.csv"
MAIN_POOL_SNAPSHOT_JSON = "main_pool_snapshot.json"


def freeze_main_pool(
    rows: list[dict[str, Any]],
    assignment_dir: str | Path,
    *,
    seed: int,
    reviewers_per_student: int,
    fm_distribution_paths: dict[str, Path] | None = None,
    main_pool_size: int | None = None,
) -> dict[str, Any]:
    """把当前 registry 冻结为 main pool 快照；main_pool_size 用于记录预期人数。

    只接受 submission_batch == 'main' 的行（冻结时刻 active main 提交全集）。
    """

    out = Path(assignment_dir)
    out.mkdir(parents=True, exist_ok=True)

    main_rows = [r for r in rows if r.get("submission_batch", "main") == "main"]
    csv_path = out / MAIN_POOL_REGISTRY_CSV
    _write_rows_csv(main_rows, csv_path)

    snapshot: dict[str, Any] = {
        "frozen_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "task_count": len(main_rows),
        "student_count": len({r["student_id"] for r in main_rows}),
        "main_pool_size": main_pool_size,
        "registry_sha256": registry_rows_sha256(main_rows),
        "assignment_seed": seed,
        "reviewers_per_student": reviewers_per_student,
        "fm_distribution_main": {k: str(v) for k, v in (fm_distribution_paths or {}).items()},
        "registry_csv": str(csv_path),
    }
    snapshot_path = out / MAIN_POOL_SNAPSHOT_JSON
    snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return snapshot


def _write_rows_csv(rows: list[dict[str, Any]], path: Path) -> None:
    import csv

    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=REGISTRY_CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in REGISTRY_CSV_COLUMNS})


def load_main_pool(assignment_dir: str | Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """读取冻结快照；返回 (rows, snapshot)。文件缺失时抛出 FileNotFoundError。"""

    out = Path(assignment_dir)
    snapshot_path = out / MAIN_POOL_SNAPSHOT_JSON
    if not snapshot_path.is_file():
        raise FileNotFoundError(f"缺少 main pool 冻结快照: {snapshot_path}")
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))

    rows: list[dict[str, Any]] = []
    csv_path = out / MAIN_POOL_REGISTRY_CSV
    if csv_path.is_file():
        import csv as _csv

        with open(csv_path, encoding="utf-8", newline="") as fh:
            for row in _csv.DictReader(fh):
                rows.append(dict(row))
    return rows, snapshot