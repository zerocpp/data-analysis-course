"""Assignment Run 编排（build / distribute / prepare / verify / report）。

状态机（contract.RunStatus）：

    INPUT_PREPARED -> SOLVED -> VERIFIED -> MAPPED -> DISTRIBUTION_BUILT
        -> DISTRIBUTION_VERIFIED -> READY

失败：INPUT_INVALID / SOLVER_FAILED / VERIFICATION_FAILED / MAPPING_FAILED /
      DISTRIBUTION_FAILED（阶段化，禁止只保留 PASS/FAIL）。

硬规则：
- Verifier PASS 之前禁止生成 mapping / distribution；
- Distribution / Leakage 未通过禁止 READY；
- 失败允许保留 run artifact（审计），但不产生分发；
- 幂等：同 (input_snapshot_hash, method, seed, policy_version) 重复执行返回已有 run；
- 禁止静默覆盖已有 AR######。
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from codesetarena.assignment.contract import (
    ASSIGN_RUN_ID_COLLISION,
    ASSIGNMENT_RECORD_SCHEMA_VERSION,
    CATEGORY_SCHEMA_VERSION,
    DISTRIBUTION_SCHEMA_VERSION,
    SOLVER_VERSION,
    RunStatus,
    AssignmentError,
)
from codesetarena.assignment import input_builder as ib
from codesetarena.assignment import mapping as mapping_mod
from codesetarena.assignment import distribution as dist_mod
from codesetarena.assignment import policy as policy_mod
from codesetarena.assignment import solver  # noqa: F401  (registry import)
from codesetarena.assignment.evaluator import evaluate_assignment
from codesetarena.assignment.policy import policy_solver_config, policy_version
from codesetarena.assignment.verifier import verify_assignment
from codesetarena.submission.registry import SubmissionRegistry, now_iso

ASSIGNMENTS_DIR = "assignments"


def _repo_dir(repo_root) -> Path:
    repo = Path(repo_root)
    (repo / ASSIGNMENTS_DIR).mkdir(parents=True, exist_ok=True)
    return repo


def _run_dir(repo: Path, run_id: str) -> Path:
    return repo / ASSIGNMENTS_DIR / run_id


def next_run_id(repo: Path) -> str:
    maximum = 0
    for path in (repo / ASSIGNMENTS_DIR).glob("AR*"):
        name = path.name
        if name.startswith("AR") and name[2:].isdigit():
            maximum = max(maximum, int(name[2:]))
    return f"AR{maximum + 1:06d}"


def list_runs(repo_root) -> list[dict]:
    repo = Path(repo_root)
    runs = []
    if (repo / ASSIGNMENTS_DIR).is_dir():
        for run_dir in sorted((repo / ASSIGNMENTS_DIR).glob("AR*")):
            run_json = run_dir / "run.json"
            if run_json.is_file():
                runs.append(json.loads(run_json.read_text(encoding="utf-8")))
    return runs


def load_run(repo_root, run_id: str) -> dict:
    path = _run_dir(_repo_dir(repo_root), run_id) / "run.json"
    if not path.is_file():
        raise AssignmentError("ASSIGN_RUN_NOT_FOUND", f"{run_id}: run.json 不存在")
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _update_status(repo: Path, run_dir: Path, run: dict, status: str, errors=None) -> dict:
    run["status"] = status
    if errors:
        run["errors"] = errors
    if status == RunStatus.READY:
        run["distribution_status"] = "READY"
    _write_json(run_dir / "run.json", run)
    return run


def _case_input(run: dict) -> dict:
    return {
        "case_id": run["assignment_run_id"],
        "num_students": run["num_reviewers"],
        "num_tasks": run["num_tasks"],
        "num_categories": 14,
        "category_vector": run["category_vector"],
    }


def build_assignment_run(
    repo_root,
    phase: str,
    *,
    policy=None,
    seed: int | None = None,
    run_id: str | None = None,
) -> dict:
    """Canonical Repository -> 验证过的 Assignment Run（build 阶段）。

    返回 run 记录；已有相同 (hash, method, seed, policy) 的 run 直接复用
    （status=RUN_EXISTS），不静默重建。
    """
    if policy is None or isinstance(policy, (str, Path)):
        policy = policy_mod.load_assignment_policy(policy)
    repo = _repo_dir(repo_root)

    assignment_input = ib.build_assignment_input(repo, phase)
    snapshot = ib.freeze_input_snapshot(assignment_input)
    snapshot_hash = ib.input_snapshot_hash(snapshot)
    if seed is None:
        # 缺省 seed 由输入快照派生：同一快照 + 同一 policy 必然得到同一 assignment。
        seed = int(snapshot_hash[:10], 16) % (10 ** 7)
    method = policy["method"]
    r = int(policy["reviews_per_task"])
    pv = policy_version(policy)

    # 幂等：同 (snapshot, method, seed, policy) 复用已有 run（§51）
    for existing in list_runs(repo):
        if (
            existing.get("input_snapshot_hash") == snapshot_hash
            and existing.get("method") == method
            and existing.get("seed") == seed
            and existing.get("policy_version") == pv
            and existing.get("status") not in RunStatus.FINAL_FAILURES
        ):
            existing["reused"] = True
            return existing

    run_dir = _run_dir(repo, run_id) if run_id else _run_dir(repo, next_run_id(repo))
    if run_dir.exists() and run_id:
        raise AssignmentError(ASSIGN_RUN_ID_COLLISION, f"{run_id} 已存在")
    run_dir.mkdir(parents=True, exist_ok=True)

    run = {
        "schema_version": ASSIGNMENT_RECORD_SCHEMA_VERSION,
        "assignment_run_id": run_dir.name,
        "course_id": "DataC1004",
        "phase": phase,
        "created_at": now_iso(),
        "method": method,
        "method_version": SOLVER_VERSION,
        "policy_version": pv,
        "category_schema_version": CATEGORY_SCHEMA_VERSION,
        "distribution_schema_version": DISTRIBUTION_SCHEMA_VERSION,
        "seed": seed,
        "r": r,
        "num_reviewers": len(assignment_input["reviewer_order"]),
        "num_tasks": len(assignment_input["task_order"]),
        "input_snapshot_hash": snapshot_hash,
        "status": RunStatus.INPUT_PREPARED,
        "errors": [],
    }
    run["category_vector"] = assignment_input["category_vector"]
    _write_json(run_dir / "input_snapshot.json", snapshot)
    category_vector_payload = {
        "assignment_run_id": run_dir.name,
        "phase": phase,
        "reviewer_order": assignment_input["reviewer_order"],
        "task_order": assignment_input["task_order"],
        "categories": assignment_input["category_vector"],
        "counts_by_category": assignment_input["counts_by_category"],
        "category_code_by_index": snapshot["category_code_by_index"],
    }
    _write_json(run_dir / "category_vector.json", category_vector_payload)
    _write_json(run_dir / "run.json", run)

    vector = assignment_input["category_vector"]
    try:
        result = solver.solve_assignment(
            vector, r, seed, policy_solver_config(policy), method=method
        )
    except Exception as exc:  # solver 异常 -> SOLVER_FAILED（记录，不静默 fallback）
        run = _update_status(
            repo, run_dir, run, RunStatus.SOLVER_FAILED,
            [f"ASSIGN_SOLVER_FAILED: {type(exc).__name__}: {exc}"],
        )
        return run
    run = _update_status(repo, run_dir, run, RunStatus.SOLVED)
    run["solver_metadata"] = {k: v for k, v in result.metadata.items()}

    verifier_result = verify_assignment(_case_input(run), result.matrix, r)
    _write_json(run_dir / "verifier.json", verifier_result)
    if not verifier_result["valid"]:
        run = _update_status(
            repo, run_dir, run, RunStatus.VERIFICATION_FAILED,
            [e for e in verifier_result["errors"]],
        )
        return run
    run = _update_status(repo, run_dir, run, RunStatus.VERIFIED)

    evaluation = evaluate_assignment(_case_input(run), result.matrix, r)
    run["verifier_status"] = "PASS"
    run["total_assignments"] = verifier_result["total_assignments"]
    run["min_reviewer_load"] = verifier_result["min_reviewer_load"]
    run["max_reviewer_load"] = verifier_result["max_reviewer_load"]
    run["min_task_coverage"] = verifier_result["min_task_coverage"]
    run["max_task_coverage"] = verifier_result["max_task_coverage"]
    run["self_review_count"] = verifier_result["self_review_count"]
    run["duplicate_pair_count"] = verifier_result["duplicate_pair_count"]
    run["diversity_score"] = evaluation["score"]
    run["exposure_mae"] = evaluation["exposure_mae"]
    run["reviewer_diversity"] = {
        k: v for k, v in evaluation["reviewer_diversity"].items()
    }
    run["artifacts"] = {
        "input_snapshot": f"assignments/{run_dir.name}/input_snapshot.json",
        "category_vector": f"assignments/{run_dir.name}/category_vector.json",
        "matrix": f"assignments/{run_dir.name}/assignment.npy",
        "assignment_csv": f"assignments/{run_dir.name}/assignment.csv",
        "verifier": f"assignments/{run_dir.name}/verifier.json",
        "audit": f"assignments/{run_dir.name}/audit.json",
        "private_mapping": f"assignments/{run_dir.name}/private_mapping.json",
    }
    _write_json(run_dir / "audit.json", {
        "assignment_run_id": run_dir.name,
        "diversity_score": evaluation["score"],
        "d_case": evaluation["d_case"],
        "collisions_total": evaluation["collisions_total"],
        "exposure_mae": evaluation["exposure_mae"],
        "reviewer_diversity": evaluation["reviewer_diversity"],
        "verifier": verifier_result,
    })
    np.save(run_dir / "assignment.npy", np.asarray(result.matrix, dtype=np.uint8))
    _write_private_assignment_csv(run_dir, run, assignment_input)
    _write_json(run_dir / "run.json", run)
    return run


def _write_private_assignment_csv(run_dir: Path, run: dict, assignment_input: dict) -> None:
    """私有 canonical artifact（§23）：含真实 reviewer / owner 身份，禁止分发。"""
    import csv

    matrix = np.load(run_dir / "assignment.npy").tolist()
    task_by_id = {t["task_id"]: t for t in assignment_input["tasks"]}
    reviewer_order = assignment_input["reviewer_order"]
    task_order = assignment_input["task_order"]
    with (run_dir / "assignment.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["reviewer_id", "task_id", "task_owner_id", "task_category", "review_index"])
        for i, reviewer in enumerate(reviewer_order):
            review_index = 0
            for j, task_id in enumerate(task_order):
                if matrix[i][j]:
                    review_index += 1
                    task = task_by_id[task_id]
                    writer.writerow(
                        [reviewer, task_id, task["owner_student_id"], task["category_code"],
                         review_index]
                    )


def distribute_assignment_run(repo_root, run_id: str, *, policy=None) -> dict:
    """VERIFIED run -> Private Mapping -> Distribution -> 校验 -> 泄露扫描 -> READY。"""
    if policy is None or isinstance(policy, (str, Path)):
        policy = policy_mod.load_assignment_policy(policy)
    repo = _repo_dir(repo_root)
    run_dir = _run_dir(repo, run_id)
    run = load_run(repo_root, run_id)
    if run["status"] != RunStatus.VERIFIED:
        raise AssignmentError(
            "ASSIGN_RUN_NOT_VERIFIED",
            f"{run_id}: status={run['status']}，必须 VERIFIED 后才能分发",
        )

    # 分发前重放独立 Verifier（防 artifact 被篡改 / 代码回归；§48/§17）
    matrix = np.load(run_dir / "assignment.npy").tolist()
    snapshot = json.loads((run_dir / "input_snapshot.json").read_text(encoding="utf-8"))
    recheck = verify_assignment(
        {
            "case_id": run_id,
            "num_students": run["num_reviewers"],
            "num_tasks": run["num_tasks"],
            "num_categories": 14,
            "category_vector": snapshot["category_vector"],
        },
        matrix,
        run["r"],
    )
    if not recheck["valid"]:
        run = _update_status(repo, run_dir, run, RunStatus.VERIFICATION_FAILED,
                             [e for e in recheck["errors"]])
        return run

    reg = SubmissionRegistry(repo)
    try:
        private_rows, private_mapping = mapping_mod.build_private_assignment_mapping(
            run_dir, repo, run, reg
        )
    except Exception as exc:
        run = _update_status(repo, run_dir, run, RunStatus.MAPPING_FAILED,
                             [f"ASSIGN_MAPPING_FAILED: {type(exc).__name__}: {exc}"])
        return run
    reg.assignments.extend(private_rows)
    reg.commit()
    run = _update_status(repo, run_dir, run, RunStatus.MAPPED)
    run["private_mapping_rows"] = len(private_rows)
    run["reviewer_packages"] = len(private_mapping["reviewers"])

    dist_root = dist_mod.build_reviewer_distributions(repo, run_dir, run, reg)
    run = _update_status(repo, run_dir, run, RunStatus.DISTRIBUTION_BUILT)
    run["distribution_root"] = str(dist_root.relative_to(repo))

    dist_check = dist_mod.verify_distribution(repo, run_dir, run, reg)
    if not dist_check["valid"]:
        run = _update_status(
            repo, run_dir, run, RunStatus.DISTRIBUTION_FAILED,
            dist_check["errors"][:20],
        )
        _write_json(run_dir / "distribution_verification.json", dist_check)
        return run
    run = _update_status(repo, run_dir, run, RunStatus.DISTRIBUTION_VERIFIED)
    _write_json(run_dir / "distribution_verification.json", dist_check)

    tokens = dist_mod.distribution_secret_tokens(repo, reg)
    leaks = dist_mod.scan_distribution_leaks(dist_root, tokens)
    content_leaks = dist_mod.scan_private_content_leaks(dist_root)
    # v10.1.11 Exposure Contract Fix #3：READY 前对最终分发执行 Exposure 语义扫描
    exposure_leaks = dist_mod.scan_exposure_contract_leaks(dist_root)
    _write_json(run_dir / "leakage_scan.json", {
        "identity_leaks": leaks,
        "private_content_leaks": content_leaks,
        "exposure_contract_leaks": exposure_leaks,
        "tokens_scanned": len(tokens),
    })
    if leaks or content_leaks or exposure_leaks:
        run = _update_status(
            repo, run_dir, run, RunStatus.DISTRIBUTION_FAILED,
            [f"DIST_IDENTITY_LEAK: {h['path']}: {h['token']}" for h in leaks][:20]
            + [f"DIST_PRIVATE_CONTENT_LEAK: {h['path']}" for h in content_leaks][:20]
            + [f"DIST_EXPOSURE_CONTRACT_LEAK: {h['path']}: {h['keys']}"
               for h in exposure_leaks][:20],
        )
        return run

    run = _update_status(repo, run_dir, run, RunStatus.READY)
    run["identity_leaks"] = 0
    run["exposure_contract_leaks"] = 0
    run["distribution_status"] = "READY"
    _write_json(run_dir / "run.json", run)
    return run


def prepare_assignment_run(repo_root, phase: str, *, policy=None, seed: int | None = None) -> dict:
    """build -> (verify) -> distribute —— 一键编排（底层功能独立可调，不耦合）。"""
    run = build_assignment_run(repo_root, phase, policy=policy, seed=seed)
    if run["status"] != RunStatus.VERIFIED:
        return run
    return distribute_assignment_run(repo_root, run["assignment_run_id"], policy=policy)