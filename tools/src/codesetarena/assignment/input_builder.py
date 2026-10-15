"""Production Assignment Input Builder。

Assignment 输入只能来自 Ingest 完成的 Canonical Task Repository + Private
Registry（§5/§6）：Single/batch FAIL 的 submission 不会在 Registry 中，因此
不可能进入 assignment。绝不从 students/*.tar.gz / received/ 直接读取。

- reviewer（学生）集合：来自 Registry 中该 phase 的 accepted submission owners
- task 集合：来自 canonical ``tasks/<T######>/``
- category：读取 canonical ``metadata.json.failure_mode``（ingest 时固化；
  绝不重新用 LLM 推断 / 随机补 / 默认填）
- canonical ordering：``reviewer_order`` 按 student_id 排序；
  ``task_order[i]`` = reviewer_order[i] 的 own task（配对序，使 solver
  的 no-self 语义 X[i,i]=0 恰好排除 own task）
"""

from __future__ import annotations

import json
from pathlib import Path

from codesetarena.assignment import category as catmod
from codesetarena.assignment.contract import (
    ASSIGN_INPUT_CATEGORY_DRIFT,
    ASSIGN_INPUT_COUNT_MISMATCH,
    ASSIGN_INPUT_DUPLICATE_REVIEWER,
    ASSIGN_INPUT_DUPLICATE_TASK,
    ASSIGN_INPUT_INVALID_CATEGORY,
    ASSIGN_INPUT_MISSING_CATEGORY,
    ASSIGN_INPUT_MISSING_OWN_TASK,
    ASSIGN_INPUT_NOT_ACTIVE,
    ASSIGN_INPUT_OWNER_MISSING,
    ASSIGN_INPUT_PHASE_MISMATCH,
    ASSIGN_INPUT_TASK_NOT_EXISTS,
    AssignmentError,
)
from codesetarena.submission.registry import SubmissionRegistry

NUM_CATEGORIES = 14


def _canonical_failure_mode(task_dir: Path, task_id: str) -> tuple[str | None, str | None]:
    """返回 (category_slug, error_code)。metadata.failure_mode 为权威来源。"""
    metadata_path = task_dir / "metadata.json"
    if not metadata_path.is_file():
        return None, ASSIGN_INPUT_TASK_NOT_EXISTS
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    value = metadata.get("failure_mode")
    if value is None or str(value).strip() == "":
        return None, ASSIGN_INPUT_MISSING_CATEGORY
    slug = catmod.normalize_category_code(value)
    if slug is None:
        return None, ASSIGN_INPUT_INVALID_CATEGORY
    task_json = task_dir / "task/task.json"
    if task_json.is_file():
        declared = json.loads(task_json.read_text(encoding="utf-8")).get("hypothesized_failure_mode")
        if declared:
            declared_slug = catmod.normalize_category_code(declared)
            if declared_slug is not None and declared_slug != slug:
                return None, ASSIGN_INPUT_CATEGORY_DRIFT
    return slug, None


def build_assignment_input(repo_root, phase: str, *, registry=None) -> dict:
    """从 Canonical Repository 构建 AssignmentInput；失败抛 AssignmentError。

    返回结构（§62）：
        {phase, reviewer_order, task_order, tasks:[{task_id, owner_student_id,
          category_index, category_code}], category_vector, counts_by_category}
    """
    repo = Path(repo_root)
    reg = registry or SubmissionRegistry(repo)
    errors: list = []

    rows = [row for row in reg.rows if row.get("phase") == phase]
    active = []
    for row in rows:
        if row.get("validation_status") != "PASS" or row.get("ingest_status") != "INGESTED":
            errors.append(f"{ASSIGN_INPUT_NOT_ACTIVE}: {row.get('student_id')}")
            continue
        task_id = str(row.get("task_id") or "")
        student_id = str(row.get("student_id") or "")
        if not task_id or not student_id:
            errors.append(f"{ASSIGN_INPUT_OWNER_MISSING}: row {row.get('submission_id')}")
            continue
        task_dir = repo / "tasks" / task_id
        if not (task_dir / "metadata.json").is_file():
            errors.append(f"{ASSIGN_INPUT_TASK_NOT_EXISTS}: {task_id}")
            continue
        category_code, category_error = _canonical_failure_mode(task_dir, task_id)
        if category_error:
            errors.append(f"{category_error}: task {task_id}")
            continue
        active.append(
            {
                "student_id": student_id,
                "task_id": task_id,
                "category_code": str(category_code),
                "category_slug": str(category_code),
            }
        )

    reviewers = [row["student_id"] for row in active]
    if len(set(reviewers)) != len(reviewers):
        errors.append(ASSIGN_INPUT_DUPLICATE_REVIEWER)
    task_ids = [row["task_id"] for row in active]
    if len(set(task_ids)) != len(task_ids):
        errors.append(ASSIGN_INPUT_DUPLICATE_TASK)

    if errors:
        raise AssignmentError(
            "ASSIGN_INPUT_INVALID", f"phase={phase}: " + " | ".join(errors)
        )

    reviewer_order = sorted(reviewers)
    by_student = {row["student_id"]: row for row in active}
    if len(by_student) != len(reviewer_order):
        raise AssignmentError(ASSIGN_INPUT_MISSING_OWN_TASK,
                              f"{len(reviewer_order)} reviewers vs {len(by_student)} tasks")

    task_order = [by_student[sid]["task_id"] for sid in reviewer_order]
    category_vector = [
        catmod.category_index_of(by_student[sid]["category_code"]) for sid in reviewer_order
    ]
    counts_by_category = {
        i: sum(1 for c in category_vector if c == i) for i in range(1, NUM_CATEGORIES + 1)
    }

    tasks = [
        {
            "task_id": task_id,
            "owner_student_id": by_student[sid]["student_id"],
            "category_index": catmod.category_index_of(by_student[sid]["category_code"]),
            "category_code": by_student[sid]["category_code"],
        }
        for sid, task_id in zip(reviewer_order, task_order)
    ]

    if len(reviewer_order) != len(task_order) or len(reviewer_order) < 2:
        raise AssignmentError(
            ASSIGN_INPUT_COUNT_MISMATCH,
            f"reviewers={len(reviewer_order)} tasks={len(task_order)}",
        )
    if any(r.get("phase") != phase for r in rows):
        raise AssignmentError(ASSIGN_INPUT_PHASE_MISMATCH, f"phase={phase}")

    return {
        "phase": phase,
        "reviewer_order": reviewer_order,
        "task_order": task_order,
        "tasks": tasks,
        "category_vector": category_vector,
        "counts_by_category": counts_by_category,
    }


def freeze_input_snapshot(assignment_input: dict) -> dict:
    """输入快照（§22/§62）：保存完整 index mapping，后续可复现。"""
    return {
        "phase": assignment_input["phase"],
        "reviewer_order": assignment_input["reviewer_order"],
        "task_order": assignment_input["task_order"],
        "tasks": assignment_input["tasks"],
        "category_vector": assignment_input["category_vector"],
        "counts_by_category": assignment_input["counts_by_category"],
        "category_code_by_index": {
            str(idx): catmod.category_code_of(idx) for idx in range(1, NUM_CATEGORIES + 1)
        },
    }


def input_snapshot_hash(snapshot: dict) -> str:
    import hashlib

    payload = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()