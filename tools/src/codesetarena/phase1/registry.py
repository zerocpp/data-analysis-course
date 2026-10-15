"""Task Registry Builder（Validation PASS 后自动执行）。

唯一输入：validated canonical package 元数据（绝不解析 raw ZIP）。
输出：

- phase1_task_registry.csv   任务注册表（唯一事实来源，Assignment / Coverage 都读它）
- phase1_task_registry.json  同上（机器可读副本，供 JSON 工具链）
- task_to_owner.json         task_id -> student_id
- student_to_tasks.json      student_id -> [task_id, ...]（支持一对多，历史/多阶段）
"""
from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

REGISTRY_CSV = "phase1_task_registry.csv"
REGISTRY_JSON = "phase1_task_registry.json"
TASK_TO_OWNER = "task_to_owner.json"
STUDENT_TO_TASKS = "student_to_tasks.json"

REGISTRY_CSV_COLUMNS = (
    "task_id", "student_id", "hypothesized_failure_mode", "task_path", "submission_batch",
)


def build_registry(packages: list[dict[str, Any]], *, submission_batch: str = "main") -> list[dict[str, Any]]:
    """由 validated packages 构造注册表行（保持输入顺序，不排序、不修改内容）。"""

    rows: list[dict[str, Any]] = []
    for pkg in packages:
        rows.append(
            {
                "task_id": pkg["task_id"],
                "student_id": pkg["student_id"],
                "hypothesized_failure_mode": pkg["hypothesized_failure_mode"],
                "task_path": pkg["dir"],
                "submission_batch": submission_batch,
                "package_sha256": pkg.get("sha256", ""),
            }
        )
    return rows


def write_registry(rows: list[dict[str, Any]], registry_dir: str | Path) -> dict[str, Path]:
    """写 registry CSV + JSON + 两个 owner mapping；返回 {name: path}。"""

    out = Path(registry_dir)
    out.mkdir(parents=True, exist_ok=True)

    csv_path = out / REGISTRY_CSV
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=REGISTRY_CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in REGISTRY_CSV_COLUMNS})

    json_path = out / REGISTRY_JSON
    json_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "rows": rows,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    task_to_owner_path = out / TASK_TO_OWNER
    task_to_owner_path.write_text(
        json.dumps({r["task_id"]: r["student_id"] for r in rows}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )

    student_to_tasks: dict[str, list[str]] = {}
    for r in rows:
        student_to_tasks.setdefault(r["student_id"], []).append(r["task_id"])
    student_to_tasks_path = out / STUDENT_TO_TASKS
    student_to_tasks_path.write_text(
        json.dumps(student_to_tasks, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    return {
        "csv": csv_path,
        "json": json_path,
        "task_to_owner": task_to_owner_path,
        "student_to_tasks": student_to_tasks_path,
    }


def load_registry(registry_dir: str | Path) -> list[dict[str, Any]]:
    """从 registry JSON 读取全部行（JSON 为准；CSV 是给人看的同一数据）。"""

    path = Path(registry_dir) / REGISTRY_JSON
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("rows") or [])


def registry_rows_sha256(rows: list[dict[str, Any]]) -> str:
    """对注册表行做确定性哈希（用于 frozen pool snapshot 一致性审计）。"""

    digest = hashlib.sha256()
    for row in sorted(rows, key=lambda r: (r.get("task_id") or "", r.get("student_id") or "")):
        canonical = json.dumps(row, ensure_ascii=False, sort_keys=True)
        digest.update(canonical.encode("utf-8"))
    return digest.hexdigest()


def append_registry_rows(
    registry_dir: str | Path,
    new_rows: list[dict[str, Any]],
    *,
    submission_batch: str = "late",
) -> list[dict[str, Any]]:
    """把 late 新行追加进 current registry 并重写全部 registry 文件。

    只追加 task_id 尚未出现的行（幂等）；已有行原样保留（main 冻结行永不修改）。
    返回合并后的全部行。
    """

    existing = load_registry(registry_dir)
    known_task_ids = {r["task_id"] for r in existing}
    merged = list(existing)
    for row in new_rows:
        if row["task_id"] in known_task_ids:
            continue
        row = dict(row)
        row["submission_batch"] = submission_batch
        merged.append(row)
        known_task_ids.add(row["task_id"])
    write_registry(merged, registry_dir)
    return merged