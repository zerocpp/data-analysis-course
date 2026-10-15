"""Gate A.5 —— 已冻结 Assignment Run 的完整性 + 独立重验证（§19–§23）。

不重跑 solver：只加载 Phase E.1 冻结的 ``assignment.npy`` / ``input_snapshot.json``
并重新执行独立 Verifier + 输入快照一致性（生产 ``build_assignment_input``
重放对比）。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from codesetarena.assignment import input_builder as ib
from codesetarena.assignment.verifier import verify_assignment
from codesetarena.submission.registry import SubmissionRegistry

from .contract import ASSIGNMENT_ARTIFACT_DRIFT, ASSIGNMENT_INPUT_DRIFT, PhaseFError


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_run(repo: str | Path, run_id: str) -> tuple[Path, dict]:
    run_dir = Path(repo) / "assignments" / run_id
    run_json = run_dir / "run.json"
    if not run_json.is_file():
        raise PhaseFError("ASSIGN_RUN_NOT_FOUND", f"{run_id}: run.json 不存在", gate="G05")
    return run_dir, json.loads(run_json.read_text(encoding="utf-8"))


def reverify_assignment_run(
    repo: str | Path,
    run_id: str,
    *,
    expected_matrix_sha256: str | None = None,
) -> dict:
    """§19/§22/§23：重放独立 Verifier；矩阵哈希与冻结值比对。"""
    run_dir, run = load_run(repo, run_id)
    matrix_path = run_dir / "assignment.npy"
    snapshot = json.loads((run_dir / "input_snapshot.json").read_text(encoding="utf-8"))

    matrix_sha = sha256_file(matrix_path)
    if expected_matrix_sha256 and matrix_sha != expected_matrix_sha256:
        raise PhaseFError(
            ASSIGNMENT_ARTIFACT_DRIFT,
            f"assignment.npy sha256={matrix_sha} != 冻结值 {expected_matrix_sha256}",
            gate="G05",
        )

    # 快照文件的 int-key dict 经 JSON 往返后无法按原序重哈希（sort_keys 语义）；
    # 完整性由 check_input_snapshot_consistency（canonical 重建 in-memory hash）
    # 保证，这里只做 run 内部 artifact 一致性。
    if run.get("category_vector") != snapshot.get("category_vector"):
        raise PhaseFError(
            ASSIGNMENT_ARTIFACT_DRIFT,
            "run.json.category_vector 与 input_snapshot.json.category_vector 不一致",
            gate="G05",
        )

    r = int(run["r"])
    matrix = np.load(matrix_path)
    case_input = {
        "case_id": run_id,
        "num_students": run["num_reviewers"],
        "num_tasks": run["num_tasks"],
        "num_categories": 14,
        "category_vector": snapshot["category_vector"],
    }
    verifier = verify_assignment(case_input, matrix.tolist(), r)

    n = run["num_reviewers"]
    expected = {
        "matrix_shape": [n, n],
        "total_assignments": n * r,
        "min_reviewer_load": r,
        "max_reviewer_load": r,
        "min_task_coverage": r,
        "max_task_coverage": r,
        "self_review_count": 0,
        "duplicate_pair_count": 0,
    }
    mismatches = {
        key: {"expected": value, "actual": verifier.get(key)}
        for key, value in expected.items()
        if verifier.get(key) != value
    }
    result = {
        "assignment_run_id": run_id,
        "recorded_input_snapshot_sha256": run.get("input_snapshot_hash"),
        "input_snapshot_sha256": run.get("input_snapshot_hash"),
        "matrix_sha256": matrix_sha,
        "verifier": verifier,
        "expected_stats": expected,
        "mismatches": mismatches,
        "status": "PASS" if verifier["valid"] and not mismatches else "FAIL",
    }
    if result["status"] != "PASS":
        raise PhaseFError(
            ASSIGNMENT_ARTIFACT_DRIFT,
            f"独立 Verifier 重放未达 Hard Gate: {mismatches or verifier['errors'][:5]}",
            gate="G06",
        )
    return result


def check_input_snapshot_consistency(repo: str | Path, run_id: str) -> dict:
    """§21：Input Snapshot 与当前 Canonical Repository 仍一致。

    用生产 ``build_assignment_input`` 从 canonical repo 重建输入，与冻结快照
    逐项对比（reviewer / task / owner / category）；任何漂移 -> 
    ``ASSIGNMENT_INPUT_DRIFT``，禁止构建 RC。
    """
    run_dir, run = load_run(repo, run_id)
    snapshot = json.loads((run_dir / "input_snapshot.json").read_text(encoding="utf-8"))
    rebuilt = ib.build_assignment_input(repo, run["phase"], registry=SubmissionRegistry(repo))
    rebuilt_snapshot = ib.freeze_input_snapshot(rebuilt)
    rebuilt_hash = ib.input_snapshot_hash(rebuilt_snapshot)

    drifted: list[str] = []
    if rebuilt_hash != run.get("input_snapshot_hash"):
        drifted.append("input_snapshot_hash")
    for key in ("reviewer_order", "task_order", "tasks", "category_vector",
                "counts_by_category"):
        actual = rebuilt.get(key)
        frozen = snapshot.get(key)
        if key == "counts_by_category":
            # JSON 往返会把 int key 变成 str key：按字符串键归一后比较
            actual = {str(k): v for k, v in (actual or {}).items()}
            frozen = {str(k): v for k, v in (frozen or {}).items()}
        if actual != frozen:
            drifted.append(key)
    result = {
        "assignment_run_id": run_id,
        "recorded_input_snapshot_sha256": run.get("input_snapshot_hash"),
        "rebuilt_snapshot_sha256": rebuilt_hash,
        "drifted_fields": drifted,
        "status": "PASS" if not drifted else "FAIL",
    }
    if drifted:
        raise PhaseFError(
            ASSIGNMENT_INPUT_DRIFT,
            f"快照字段漂移: {drifted}",
            gate="G05",
        )
    return result
