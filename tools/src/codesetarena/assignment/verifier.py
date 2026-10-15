"""独立 Assignment Verifier（生产强制要求；Benchmark 亦从其导入）。

``verify_assignment(case_input, assignment, r)`` 从零重算 Hard Gate，绝不信任
solver 内部校验。生产每次 Assignment 后必须执行，然后才允许 materialize
mapping / distribution。

Hard Gate（与 evaluation_spec.json 冻结定义一致）：
- 矩阵 shape 恰好 200x200 且 binary
- 每 reviewer load == r
- 每 task coverage == r
- 无 self-review（X[i,i] == 0）
- 无 duplicate reviewer-task pair
"""

from __future__ import annotations

CaseInput = dict


def _to_rows(assignment) -> list:
    rows = []
    for row in assignment:
        rows.append([int(v) for v in row])
    return rows


def verify_assignment(case_input: CaseInput, assignment, r: int) -> dict:
    """Case Input + Assignment -> {valid, ...结构统计, errors:[...]}。"""
    vector = case_input["category_vector"]
    n = len(vector)
    errors: list = []

    rows = _to_rows(assignment)

    if len(rows) != n:
        errors.append(f"INVALID_SHAPE: expected {n} rows, got {len(rows)}")
        return {
            "valid": False,
            "matrix_shape": [len(rows), len(rows[0]) if rows else 0],
            "total_assignments": 0,
            "min_reviewer_load": 0,
            "max_reviewer_load": 0,
            "min_task_coverage": 0,
            "max_task_coverage": 0,
            "self_review_count": 0,
            "duplicate_pair_count": 0,
            "errors": errors,
        }

    non_binary = 0
    duplicate_units = 0
    for i, row in enumerate(rows):
        if len(row) != n:
            errors.append(f"INVALID_SHAPE: row {i} has {len(row)} columns, expected {n}")
            continue
        for j, v in enumerate(row):
            if v not in (0, 1):
                non_binary += 1
                duplicate_units += max(0, v - 1)

    if non_binary:
        errors.append(f"NON_BINARY_MATRIX: {non_binary} entries outside {{0,1}}")

    if not rows or any(len(row) != n for row in rows):
        return {
            "valid": False,
            "matrix_shape": [n, n],
            "total_assignments": 0,
            "min_reviewer_load": 0,
            "max_reviewer_load": 0,
            "min_task_coverage": 0,
            "max_task_coverage": 0,
            "self_review_count": 0,
            "duplicate_pair_count": duplicate_units,
            "errors": errors,
        }

    reviewer_loads = [sum(row) for row in rows]
    task_coverage = [0] * n
    self_reviews = 0
    total = 0
    for i, row in enumerate(rows):
        for j, v in enumerate(row):
            if v:
                total += 1
                task_coverage[j] += 1
                if i == j:
                    self_reviews += 1

    for i, load in enumerate(reviewer_loads):
        if load != r:
            errors.append(f"REVIEWER_LOAD: reviewer {i} has load {load}, expected {r}")
    for j, cov in enumerate(task_coverage):
        if cov != r:
            errors.append(f"TASK_COVERAGE: task {j} has coverage {cov}, expected {r}")
    if self_reviews:
        errors.append(f"SELF_REVIEW: {self_reviews} diagonal entries set")

    return {
        "valid": not errors,
        "matrix_shape": [n, n],
        "total_assignments": total,
        "min_reviewer_load": min(reviewer_loads),
        "max_reviewer_load": max(reviewer_loads),
        "min_task_coverage": min(task_coverage),
        "max_task_coverage": max(task_coverage),
        "self_review_count": self_reviews,
        "duplicate_pair_count": duplicate_units,
        "errors": errors,
    }


def verifier_summary(verifier_result: dict) -> dict:
    """Production run.json 需要的 verifier 摘要（含状态字段）。"""
    return {
        "status": "PASS" if verifier_result["valid"] else "FAIL",
        **{k: v for k, v in verifier_result.items() if k != "errors"},
        "errors": verifier_result["errors"],
    }