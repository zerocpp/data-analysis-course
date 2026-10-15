"""Private Assignment Mapping（§24/§25/§35）。

- ``registry/assignments.jsonl`` = Assignment Registry Source of Truth
  （reviewer_student_id ↔ canonical task_id ↔ task_owner_student_id ↔
  anonymous_task_id ↔ reviewer_package_id）。Private，禁止进入学生分发。
- run 目录 ``private_mapping.json`` = 每 run 的管理端私有索引
  （reviewer_package_id ↔ reviewer_student_id ↔ anonymous_task_ids）。
- 匿名映射（canonical task_id ↔ anonymous_task_id）复用 ingest 已建立的
  ``registry/anonymous_mapping.jsonl``（同一 canonical Task 恒为同一 AT，
  course-wide stable，不可被分配阶段改写）。

评审者 Package ID（R######）只是私有索引键：公开 distribution 树不出现
student_id，只有 R 编号；「R 编号 ↔ 谁」由管理端私有映射决定。
"""

from __future__ import annotations

import json
from pathlib import Path

from codesetarena.assignment.contract import (
    ASSIGNMENT_RECORD_SCHEMA_VERSION,
    AssignmentError,
)
from codesetarena.submission.registry import now_iso


def derive_private_assignment_mapping(
    snapshot: dict,
    matrix,
    *,
    r: int,
    run_id: str,
    phase: str,
    registry,
) -> tuple:
    """纯函数：快照 + 矩阵 + Registry -> (assignments rows, private mapping)。

    不写盘（正式 distribute 与 Release Candidate 构建共用；写盘由调用方决定）。
    """
    reviewer_order = snapshot["reviewer_order"]
    task_order = snapshot["task_order"]
    tasks = {t["task_id"]: t for t in snapshot["tasks"]}

    rows = []
    private_mapping = {
        "schema_version": ASSIGNMENT_RECORD_SCHEMA_VERSION,
        "assignment_run_id": run_id,
        "phase": phase,
        "reviewers": [],
    }
    for i, reviewer in enumerate(reviewer_order):
        package_id = f"R{i + 1:06d}"
        anonymous_tasks = []
        for j, task_id in enumerate(task_order):
            if matrix[i][j]:
                task = tasks[task_id]
                mapping = registry.find_anonymous_task(task_id)
                if mapping is None:
                    raise AssignmentError(
                        "ASSIGN_MAPPING_FAILED",
                        f"{run_id}: canonical task {task_id} 无匿名映射",
                    )
                anonymous_task_id = mapping["anonymous_task_id"]
                anonymous_tasks.append(anonymous_task_id)
                rows.append({
                    "schema_version": ASSIGNMENT_RECORD_SCHEMA_VERSION,
                    "assignment_run_id": run_id,
                    "phase": phase,
                    "reviewer_student_id": reviewer,
                    "reviewer_package_id": package_id,
                    "task_id": task_id,
                    "task_owner_student_id": task["owner_student_id"],
                    "anonymous_task_id": anonymous_task_id,
                    "review_index": len(anonymous_tasks),
                    "created_at": now_iso(),
                })
        if len(anonymous_tasks) != r:
            raise AssignmentError(
                "ASSIGN_MAPPING_FAILED",
                f"{run_id}: reviewer {reviewer} 映射到 {len(anonymous_tasks)} 个任务 (r={r})",
            )
        private_mapping["reviewers"].append({
            "reviewer_student_id": reviewer,
            "reviewer_package_id": package_id,
            "anonymous_task_ids": anonymous_tasks,
        })
    return rows, private_mapping


def build_private_assignment_mapping(run_dir: Path, repo: Path, run: dict, registry) -> tuple:
    """生成 assignments.jsonl 行 + 每 run 的私有 reviewer 映射。

    返回 (rows, private_mapping)；调用方负责把 rows 追加写回
    registry/assignments.jsonl（原子 commit）。
    """
    import numpy as np

    matrix_path = run_dir / "assignment.npy"
    snapshot = json.loads((run_dir / "input_snapshot.json").read_text(encoding="utf-8"))
    rows, private_mapping = derive_private_assignment_mapping(
        snapshot,
        np.load(matrix_path).tolist(),
        r=int(run["r"]),
        run_id=run["assignment_run_id"],
        phase=run["phase"],
        registry=registry,
    )

    (run_dir / "private_mapping.json").write_text(
        json.dumps(private_mapping, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return rows, private_mapping