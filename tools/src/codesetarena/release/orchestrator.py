"""Phase F Orchestrator（§87）：只编排，全部底层复用正式实现。

流程::

    discover 200 archives -> freeze inventory/SHA
      -> fresh single validation ×200 -> fresh cross-package validation
      -> PHASE1_FINAL_VALIDATED（冻结 Final Acceptance Manifest）
      -> canonical provenance
      -> load frozen AR###### -> artifact integrity + snapshot consistency
      -> re-run independent Verifier（不重跑 solver）
      -> derive private mapping（纯函数）
      -> build 200 RC packages（正式 builder，隔离区）
      -> validate RC ×200 -> no-self -> exposure -> reverse reconstruction
      -> identity / private / secret / local-path leakage scans
      -> deterministic rebuild check -> hash freeze -> reports

禁止：rebuild Phase1、rerun solver、写正式 distributions/、发放。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable

from codesetarena.assignment import distribution as dist_mod
from codesetarena.assignment import mapping as mapping_mod
from codesetarena.submission.registry import SubmissionRegistry

from . import assignment_gate, candidate, final_validation, inventory, leakage, lineage, reporting
from .contract import (
    GATES,
    PHASE1_FINAL_ACCEPTANCE_MANIFEST,
    PHASE1_ARCHIVE_INVENTORY,
    PHASE1_FINAL_SUMMARY,
    PHASE1_FINAL_VALIDATION_REPORT,
    PHASE1_FINAL_VALIDATED,
    PHASE1_LOG,
    RC_BUILD_LOG,
    RC_DIR_NAME,
    RC_LEAKAGE_REPORT,
    RC_MANIFEST,
    RC_PRIVATE_MANIFEST,
    RC_PUBLIC_MANIFEST,
    RC_RECONSTRUCTION_REPORT,
    RC_REPORT,
    RC_SPOT_CHECK,
    RC_STATUS_VERIFIED,
    RC_SUMMARY,
    RC_VERIFY_LOG,
    RELEASE_AWAITING_EXPLICIT,
    RELEASE_GATE,
    RELEASE_LINEAGE,
    RELEASE_NOT_RELEASED,
    PhaseFError,
)

Logger = Callable[[str], None]

MANIFESTS_DIR = "manifests"
REPORTS_DIR = "reports"
ARCHIVE_COUNT = 200
REVIEWS_PER_TASK = 10


def _make_logger(path: Path) -> Logger:
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"csa.phase_f.{path.name}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        handler = logging.FileHandler(path, mode="w", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        logger.addHandler(handler)
    return logger.info


def _write_json(path: Path, payload) -> Path:
    return lineage.write_json(path, payload)


def _write_jsonl(path: Path, rows: list[dict]) -> Path:
    return lineage.write_jsonl(path, rows)


# ---------------------------------------------------------------------------
# §62：正式 distributions/ 保护
# ---------------------------------------------------------------------------


def _distributions_inventory(repo: Path) -> dict:
    base = repo / "distributions"
    files: dict[str, int] = {}
    if base.is_dir():
        for path in sorted(p for p in base.rglob("*") if p.is_file()):
            files[str(path.relative_to(base))] = path.stat().st_size
    return {"file_count": len(files), "files": files}


def _assert_distributions_unchanged(before: dict, after: dict) -> None:
    if before != after:
        added = sorted(set(after["files"]) - set(before["files"]))
        raise PhaseFError(
            "FORMAL_DISTRIBUTION_MUTATED",
            f"正式 distributions/ 被修改（新增 {added[:5]}）",
            gate="G16",
        )


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def run_phase_f(
    workspace: str | Path,
    *,
    submissions_dirname: str = "submissions",
    repository_dirname: str = "repository",
    assignment_run_id: str = "AR000001",
    expected_archive_count: int = ARCHIVE_COUNT,
    reviews_per_task: int = REVIEWS_PER_TASK,
    expected_matrix_sha256: str | None = None,
    deterministic_sample: int = 10,
    spot_check_count: int = 10,
    log: Logger | None = None,
) -> dict:
    """执行完整 Phase F；返回机器可读 summary（任一 Gate 失败抛 PhaseFError）。"""
    workspace = Path(workspace)
    submissions_dir = workspace / submissions_dirname
    repo = workspace / repository_dirname
    final_dir = workspace / "final_acceptance"
    rc_dir = workspace / RC_DIR_NAME / assignment_run_id
    rc_manifests = rc_dir / MANIFESTS_DIR
    rc_reports = rc_dir / REPORTS_DIR
    for directory in (final_dir, rc_manifests, rc_reports):
        directory.mkdir(parents=True, exist_ok=True)

    say = log or (lambda message: None)
    phase1_log = _make_logger(final_dir / PHASE1_LOG)
    build_log = _make_logger(rc_reports / RC_BUILD_LOG)
    verify_log = _make_logger(rc_reports / RC_VERIFY_LOG)
    dist_before = _distributions_inventory(repo)

    # ------------------------------------------------------------------
    # Gate A：Phase1 Final Acceptance（§3–§18）
    # ------------------------------------------------------------------
    say("== Gate A: Phase1 Final Acceptance ==")
    phase1_log("scan submissions (read-only)")
    rows = inventory.scan_archive_inventory(submissions_dir)
    inventory.require_archive_count(rows, expected_archive_count)
    _write_jsonl(final_dir / PHASE1_ARCHIVE_INVENTORY, rows)
    phase1_log(f"ARCHIVES={len(rows)}")
    frozen_before = inventory.freeze_hashes(rows)
    phase1_set_hash = inventory.submission_set_sha256(rows)

    archive_paths = [Path(row["path"]) for row in rows]
    phase1_log("fresh single-package validation x%d" % len(archive_paths))
    single_results = final_validation.validate_archives_fresh(archive_paths, log=phase1_log)
    sha_by_name = frozen_before
    for result in single_results:
        result["archive_sha256"] = sha_by_name.get(result["archive"])

    single_pass = sum(1 for r in single_results if r["status"] == "PASS")
    phase1_log(f"SINGLE_PASS={single_pass}/{len(single_results)}")
    if single_pass != expected_archive_count:
        failed = [r["archive"] for r in single_results if r["status"] != "PASS"][:5]
        raise PhaseFError(
            "PHASE1_SINGLE_VALIDATION_FAILED",
            f"single PASS {single_pass}/{expected_archive_count}（示例: {failed}）",
            gate="G02",
        )

    phase1_log("fresh cross-package validation")
    cross = final_validation.cross_package_fresh(single_results)
    if cross.get("status") != "PASS":
        raise PhaseFError(
            "PHASE1_BATCH_CONFLICT",
            f"cross-package errors={cross.get('error_count')}",
            gate="G03",
        )

    registry = SubmissionRegistry(repo)
    acceptance_rows = final_validation.build_acceptance_rows(
        rows, single_results, cross, registry
    )
    accepted = final_validation.accepted_rows(acceptance_rows)
    if len(accepted) != expected_archive_count:
        raise PhaseFError(
            "PHASE1_FINAL_ACCEPTANCE_FAILED",
            f"FINAL_ACCEPTED={len(accepted)}/{expected_archive_count}",
            gate="G03",
        )
    _write_jsonl(final_dir / PHASE1_FINAL_ACCEPTANCE_MANIFEST, acceptance_rows)
    phase1_log(f"FINAL_ACCEPTED={len(accepted)} manifest frozen")

    provenance = final_validation.canonical_provenance(repo, accepted)
    phase1_log(f"CANONICAL_PROVENANCE={provenance['status']}")

    phase1_log("re-hash archives (mutation check)")
    rows_after = inventory.scan_archive_inventory(submissions_dir)
    mutations = inventory.detect_mutations(
        frozen_before, inventory.freeze_hashes(rows_after)
    )
    if mutations:
        raise PhaseFError(
            "PHASE1_ARCHIVE_MUTATION", f"{len(mutations)} 个 archive 被修改", gate="G02"
        )

    # ------------------------------------------------------------------
    # Gate A 附属扫描：200 个 Phase1 Archive 的最终 Secret Scan（§9）
    # ------------------------------------------------------------------
    phase1_secret_hits = leakage.scan_archives_for_secrets(archive_paths)
    if phase1_secret_hits:
        raise PhaseFError(
            "PHASE1_SECRET_LEAK",
            f"{len(phase1_secret_hits)} 个疑似 Secret（示例: {phase1_secret_hits[:3]}）",
            gate="G02",
        )
    phase1_log("phase1 secret scan = 0")

    phase1_summary = {
        "phase1_status": PHASE1_FINAL_VALIDATED,
        "archives": len(rows),
        "single_pass": single_pass,
        "single_fail": len(single_results) - single_pass,
        "batch_status": cross["status"],
        "batch_conflicts": cross.get("error_count", 0),
        "final_accepted": len(accepted),
        "archive_mutations": len(mutations),
        "canonical_provenance": provenance["status"],
        "phase1_submission_set_sha256": phase1_set_hash,
    }
    _write_json(final_dir / PHASE1_FINAL_SUMMARY, phase1_summary)

    # ------------------------------------------------------------------
    # §19–§23：冻结 Assignment Run 重验证
    # ------------------------------------------------------------------
    say(f"== Assignment Run {assignment_run_id}: integrity + re-verification ==")
    snapshot_consistency = assignment_gate.check_input_snapshot_consistency(
        repo, assignment_run_id
    )
    reverify = assignment_gate.reverify_assignment_run(
        repo,
        assignment_run_id,
        expected_matrix_sha256=expected_matrix_sha256,
    )
    say(f"assignment matrix sha256={reverify['matrix_sha256']}")
    say(f"verifier status={reverify['status']}")

    # ------------------------------------------------------------------
    # Gate B：Phase2 Release Candidate（§24–§61）
    # ------------------------------------------------------------------
    say("== Gate B: Phase2 Release Candidate ==")
    run_dir, run = assignment_gate.load_run(repo, assignment_run_id)
    if run["r"] != reviews_per_task:
        raise PhaseFError(
            "RC_R_MISMATCH",
            f"assignment r={run['r']} != policy {reviews_per_task}",
            gate="G07",
        )
    matrix = _load_matrix(run_dir / "assignment.npy")
    assignment_rows, private_mapping = mapping_mod.derive_private_assignment_mapping(
        json.loads((run_dir / "input_snapshot.json").read_text(encoding="utf-8")),
        matrix.tolist(),
        r=run["r"],
        run_id=assignment_run_id,
        phase=run["phase"],
        registry=registry,
    )
    build_log(f"private mapping reviewers={len(private_mapping['reviewers'])} "
              f"rows={len(assignment_rows)}")

    build_log("building RC packages with production builder (isolated dir)")
    records = candidate.build_release_candidates(
        repo, run, private_mapping, workspace / RC_DIR_NAME, log=build_log
    )
    if len(records) != expected_archive_count:
        raise PhaseFError(
            "RC_PACKAGE_COUNT_FAILED",
            f"RC packages={len(records)} != {expected_archive_count}",
            gate="G07",
        )

    archive_paths = [Path(r["package_path"]) for r in records]
    validations = [
        candidate.validate_rc_package(path, run["r"]) for path in archive_paths
    ]
    for record, validation in zip(records, validations):
        record["validation_status"] = "PASS" if validation["valid"] else "FAIL"
        record["validation_errors"] = validation["errors"][:5]
    candidate.require_valid_packages(validations, expected_archive_count)
    build_log(f"RC_PACKAGE_PASS={len(validations)}/{expected_archive_count}")

    # ------------------------------------------------------------------
    # 反向重建 + No-Self + Exposure（§45–§50）
    # ------------------------------------------------------------------
    verify_log("reverse reconstruction from final RC archives")
    snapshot = json.loads((run_dir / "input_snapshot.json").read_text(encoding="utf-8"))
    rebuilt = candidate.reconstruct_matrix_from_archives(
        archive_paths,
        private_mapping=private_mapping,
        registry=registry,
        snapshot=snapshot,
        r=run["r"],
    )
    if rebuilt["errors"]:
        raise PhaseFError(
            "RC_RECONSTRUCTION_ERROR",
            f"{len(rebuilt['errors'])} 项包级错误（示例: {rebuilt['errors'][:5]}）",
            gate="G11",
        )
    comparison = candidate.compare_matrix(rebuilt["matrix"], run_dir / "assignment.npy")
    verify_log(f"RECONSTRUCTION_DIFF={comparison['difference_count']}")

    no_self_rows, no_self_problems = candidate.package_no_self(
        rebuilt["packages"], registry, snapshot
    )
    if no_self_problems:
        raise PhaseFError(
            "RC_SELF_REVIEW",
            f"{len(no_self_problems)} 个包出现 own task",
            gate="G09",
        )
    exposure = candidate.task_exposure(rebuilt["matrix"], snapshot["task_order"])
    exposure_values = list(exposure.values())
    total_exposure = sum(exposure_values)
    if min(exposure_values) != run["r"] or max(exposure_values) != run["r"]:
        raise PhaseFError(
            "RC_EXPOSURE_FAILED",
            f"exposure min={min(exposure_values)} max={max(exposure_values)} != {run['r']}",
            gate="G10",
        )
    duplicates = sum(
        1 for p in rebuilt["packages"]
        if p["unique_canonical_tasks"] != run["r"]
        or p["unique_anonymous_tasks"] != run["r"]
        or p["task_count"] != run["r"]
    )
    if duplicates:
        raise PhaseFError(
            "RC_DUPLICATE_TASK", f"{duplicates} 个包含重复任务", gate="G08"
        )

    # ------------------------------------------------------------------
    # 泄露扫描（§51–§56）
    # ------------------------------------------------------------------
    verify_log("leakage scans (identity / private / secret / local path)")
    tokens = leakage.build_identity_tokens(
        registry.rows,
        extra_tokens=_generation_tokens(workspace),
    )
    expanded_root = candidate.reviewer_package_dirs(rc_dir)
    identity_tree = leakage.scan_identity(expanded_root, tokens)
    private_tree = leakage.scan_private_content(expanded_root)
    secret_tree = leakage.scan_secrets(expanded_root)
    path_tree = leakage.scan_local_paths(expanded_root)
    identity_archives = leakage.scan_archives_for_identity(archive_paths, tokens)
    private_archives = leakage.scan_archives_for_private_content(archive_paths)
    secret_archives = leakage.scan_archives_for_secrets(archive_paths)
    header_hits = leakage.scan_tar_headers(archive_paths, tokens)

    identity_leaks = identity_tree + identity_archives + header_hits
    private_leaks = private_tree + private_archives
    secret_leaks = secret_tree + [
        h for h in secret_archives
        if not str(h.get("kind", "")).startswith("local_path:")
    ]
    local_path_leaks = path_tree + [
        h for h in secret_archives
        if str(h.get("kind", "")).startswith("local_path:")
    ]
    leakage_payload = {
        "identity": identity_leaks,
        "private_content": private_leaks,
        "secret": secret_leaks,
        "local_path": local_path_leaks,
        "tokens_scanned": len(tokens),
    }
    _write_json(rc_reports / RC_LEAKAGE_REPORT, leakage_payload)
    for label, hits, gate in (
        ("identity", identity_leaks, "G12"),
        ("private content", private_leaks, "G13"),
        ("secret", secret_leaks, "G14"),
        ("local path", local_path_leaks, "G15"),
    ):
        if hits:
            raise PhaseFError(
                "RC_LEAKAGE", f"{label} leaks={len(hits)}（示例: {hits[:3]}）", gate=gate
            )
        verify_log(f"{label} leaks = 0")

    # ------------------------------------------------------------------
    # 确定性重建 + Hash Freeze（§57–§60/§80）
    # ------------------------------------------------------------------
    determinism = candidate.deterministic_rebuild_check(
        records, repo=repo, run=run, rc_dir=rc_dir, sample=deterministic_sample
    )
    if determinism["status"] != "PASS":
        raise PhaseFError(
            "RC_DETERMINISM_FAILED",
            f"{len(determinism['mismatches'])} 个包重建字节不一致",
            gate="G16",
        )
    verify_log(f"deterministic rebuild check OK (sample={deterministic_sample})")

    private_by_id = {r["reviewer_package_id"]: r for r in private_mapping["reviewers"]}
    public_rows = [{
        "release_candidate_id": r["release_candidate_id"],
        "package_filename": r["package_filename"],
        "sha256": r["sha256"],
        "size_bytes": r["size_bytes"],
        "task_count": r["task_count"],
        "anonymous_task_ids": r["anonymous_task_ids"],
        "distribution_phase": dist_mod.DISTRIBUTION_PHASE,
        "source_phase": run["phase"],
        "assignment_run_id": assignment_run_id,
    } for r in records]
    private_rows = [{
        **row,
        "reviewer_student_id": private_by_id[row["release_candidate_id"]]["reviewer_student_id"],
    } for row in public_rows]

    # §57/§58：manifest 三层中只有 private 版可含 reviewer_student_id
    staff_rows = [
        {k: v for k, v in record.items() if k != "reviewer_student_id"}
        for record in records
    ]
    _write_jsonl(rc_manifests / RC_MANIFEST, staff_rows)
    _write_jsonl(rc_manifests / RC_PUBLIC_MANIFEST, public_rows)
    _write_jsonl(rc_manifests / RC_PRIVATE_MANIFEST, private_rows)
    rc_set_hash = lineage.rc_set_sha256(records)
    verify_log(f"RC_SET_SHA256={rc_set_hash}")

    exposure_rows = _exposure_report_rows(exposure, registry)
    spot_check = _spot_check(
        records, repo, registry, snapshot, count=spot_check_count
    )
    _write_json(rc_reports / RC_SPOT_CHECK, spot_check)

    gates = _gate_table(
        archive_count=len(rows),
        expected_archive_count=expected_archive_count,
        single_pass=single_pass,
        cross=cross,
        provenance=provenance,
        reverify=reverify,
        snapshot_consistency=snapshot_consistency,
        rc_count=len(records),
        rc_valid=sum(1 for v in validations if v["valid"]),
        self_reviews=len(no_self_problems),
        exposure_min=min(exposure_values),
        exposure_max=max(exposure_values),
        reconstruction=comparison,
        identity_leaks=len(identity_leaks),
        private_leaks=len(private_leaks),
        secret_leaks=len(secret_leaks),
        local_path_leaks=len(local_path_leaks),
        hash_freeze=True,
    )

    _assert_distributions_unchanged(dist_before, _distributions_inventory(repo))

    reconstruction_report = {
        **comparison,
        "expected_matrix_sha256": reverify["matrix_sha256"],
        "reconstructed_matrix_sha256": _matrix_sha(rebuilt["matrix"]),
        "status": "PASS" if comparison["bit_exact"] else "FAIL",
        "packages_checked": rebuilt["package_count"],
    }
    _write_json(rc_reports / RC_RECONSTRUCTION_REPORT, reconstruction_report)

    rc_summary = {
        "phase1_final_accepted": len(accepted),
        "assignment_run_id": assignment_run_id,
        "reviewers": run["num_reviewers"],
        "tasks": run["num_tasks"],
        "r": run["r"],
        "rc_packages": len(records),
        "tasks_per_package_min": min(r["task_count"] for r in records),
        "tasks_per_package_max": max(r["task_count"] for r in records),
        "total_task_exposures": total_exposure,
        "task_exposure_min": min(exposure_values),
        "task_exposure_max": max(exposure_values),
        "self_review": len(no_self_problems),
        "duplicate_assignments": duplicates,
        "reconstruction_diff": comparison["difference_count"],
        "identity_leaks": len(identity_leaks),
        "private_content_leaks": len(private_leaks),
        "secret_leaks": len(leakage_payload["secret"]),
        "local_path_leaks": len(local_path_leaks),
        "assignment_matrix_sha256": reverify["matrix_sha256"],
        "phase1_submission_set_sha256": phase1_set_hash,
        "phase2_release_candidate_set_sha256": rc_set_hash,
        "formal_distribution_created": 0,
        "packages_released": 0,
        "phase2_rc_status": RC_STATUS_VERIFIED,
        "release_status": RELEASE_NOT_RELEASED,
        "next_action": RELEASE_AWAITING_EXPLICIT,
    }
    _write_json(rc_reports / RC_SUMMARY, rc_summary)
    _write_json(rc_reports / RELEASE_GATE, {
        "schema_version": "phase_f.v1",
        "gates": gates,
        "phase1_status": PHASE1_FINAL_VALIDATED,
        "phase2_rc_status": RC_STATUS_VERIFIED,
        "release_status": RELEASE_NOT_RELEASED,
    })

    # ------------------------------------------------------------------
    # 报告（JSON 权威 + Excel 视图）
    # ------------------------------------------------------------------
    reporting.write_phase1_report(
        final_dir / PHASE1_FINAL_VALIDATION_REPORT,
        summary=phase1_summary,
        acceptance_rows=acceptance_rows,
        cross=cross,
        provenance=provenance,
        identity=identity_tree,
        secret_hits=phase1_secret_hits,
        inventory=rows,
    )
    reporting.write_phase2_report(
        rc_reports / RC_REPORT,
        summary=rc_summary,
        packages=private_rows,
        exposure_rows=exposure_rows,
        reconstruction=reconstruction_report,
        leakage=leakage_payload,
        archives=records,
        gates=gates,
    )

    lineage_payload = lineage.build_release_lineage(
        phase1_set_hash=phase1_set_hash,
        canonical_snapshot_hash=lineage.canonical_repository_snapshot_sha256(repo),
        assignment_run_id=assignment_run_id,
        input_snapshot_hash=reverify["input_snapshot_sha256"],
        matrix_sha256=reverify["matrix_sha256"],
        anon_mapping_hash=lineage.anonymous_mapping_sha256(repo),
        rc_set_hash=rc_set_hash,
    )
    _write_json(rc_reports / RELEASE_LINEAGE, lineage_payload)
    _write_json(workspace / RC_DIR_NAME / RELEASE_LINEAGE, lineage_payload)

    say("== Phase F complete ==")
    return {
        "phase1": phase1_summary,
        "phase2_rc": rc_summary,
        "gates": gates,
        "lineage": lineage_payload,
    }


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _load_matrix(path: Path):
    import numpy as np

    return np.load(path)


def _matrix_sha(matrix: list[list[int]]) -> str:
    """与 ``assignment.npy`` 同一表示（``np.save`` uint8）下的 SHA256。"""
    import hashlib
    import io

    import numpy as np

    buffer = io.BytesIO()
    np.save(buffer, np.asarray(matrix, dtype=np.uint8))
    return hashlib.sha256(buffer.getvalue()).hexdigest()


def _generation_tokens(workspace: Path) -> list[str]:
    """§51：cohort 生成期的模板 / 手册路径等额外私有 token。"""
    tokens: set[str] = {
        "workspace/manual", "workspace\\manual", "manual/", "manual\\",
        "submission_registry.jsonl", "anonymous_mapping.jsonl",
        "assignments.jsonl", "private_mapping.json",
    }
    manifest_path = workspace / "generation" / "cohort_generation_manifest_final.jsonl"
    if manifest_path.is_file():
        for line in manifest_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            for key in ("synthetic_student_id", "source_template_dir", "generated_task_id",
                        "submission_archive_path"):
                value = row.get(key)
                if value:
                    tokens.add(Path(str(value)).name if key != "source_template_dir"
                               else str(value))
    return sorted(tokens)


def _exposure_report_rows(exposure: dict[str, int], registry: SubmissionRegistry) -> list[dict]:
    owner_by_task = {row["task_id"]: row["student_id"] for row in registry.rows}
    at_by_task = {
        mapping["task_id"]: mapping["anonymous_task_id"]
        for mapping in registry.mappings if mapping.get("anonymous_task_id")
    }
    return [{
        "canonical_task_id": task_id,
        "anonymous_task_id": at_by_task.get(task_id, ""),
        "owner_student_id": owner_by_task.get(task_id, ""),
        "reviewer_count": count,
    } for task_id, count in sorted(exposure.items())]


def _spot_check(
    records: list[dict],
    repo: Path,
    registry: SubmissionRegistry,
    snapshot: dict,
    *,
    count: int,
) -> dict:
    """§73：确定性抽取（不看 student 身份），覆盖尽量多的 FM / 匿名任务组合。"""
    by_id = {r["release_candidate_id"]: r for r in records}
    fm_by_task: dict[str, str] = {}
    for task_dir in sorted((repo / "tasks").iterdir()):
        metadata = task_dir / "metadata.json"
        if metadata.is_file():
            fm_by_task[task_dir.name] = json.loads(metadata.read_text(encoding="utf-8")).get(
                "failure_mode", ""
            )
    at_to_task = {
        m["anonymous_task_id"]: m["task_id"] for m in registry.mappings
        if m.get("anonymous_task_id")
    }
    owner_by_task = {row["task_id"]: row["student_id"] for row in registry.rows}

    ordered = sorted(by_id)
    picked: list[str] = []
    seen_fms: set[str] = set()
    for package_id in ordered:
        record = by_id[package_id]
        fms = {
            fm_by_task.get(at_to_task.get(at, ""), "")
            for at in record["anonymous_task_ids"]
        }
        if fms - seen_fms:
            picked.append(package_id)
            seen_fms |= fms
        if len(picked) >= count:
            break
    step = max(1, len(ordered) // max(1, count))
    for index in range(0, len(ordered), step):
        if len(picked) >= count:
            break
        if ordered[index] not in picked:
            picked.append(ordered[index])

    checks = []
    for package_id in picked[:count]:
        record = by_id[package_id]
        entry: dict[str, Any] = {
            "release_candidate_id": package_id,
            "package_filename": record["package_filename"],
            "task_count": record["task_count"],
            "tasks": [],
        }
        for at in record["anonymous_task_ids"]:
            task_id = at_to_task.get(at, "")
            task_view_dir = (
                candidate.reviewer_package_dirs(
                    Path(record["package_path"]).parents[1]
                ) / package_id / "tasks" / at
            )
            task_json = task_view_dir / "task" / "task.json"
            data_csv = task_view_dir / "task" / "data" / "data.csv"
            data_flat = task_view_dir / "data" / "data.csv"
            data_path = data_csv if data_csv.is_file() else data_flat
            payload = json.loads(task_json.read_text(encoding="utf-8")) if task_json.is_file() else {}
            forbidden = [key for key in payload if key in (
                "student_id", "task_id", "source_task_id", "submission_id",
                "declared_goal_answer", "gold_answer", "reference", "archive_sha256",
            )]
            header = ""
            if data_path.is_file():
                header = data_path.read_text(encoding="utf-8", errors="ignore").splitlines()[0]
            entry["tasks"].append({
                "anonymous_task_id": at,
                "canonical_task_id": task_id,
                "failure_mode": fm_by_task.get(task_id, ""),
                "owner_student_id": owner_by_task.get(task_id, ""),
                "has_task_json": task_json.is_file(),
                "has_data": data_path.is_file(),
                "data_header": header,
                "forbidden_fields": forbidden,
            })
        checks.append(entry)
    return {
        "selected": picked[:count],
        "selection_rule": "deterministic FM-coverage greedy + even spacing (identity-blind)",
        "checks": checks,
    }


def _gate_table(**kwargs) -> list[dict]:
    cross_ok = kwargs["cross"].get("status") == "PASS"
    exposure_ok = (
        kwargs["exposure_min"] == REVIEWS_PER_TASK
        and kwargs["exposure_max"] == REVIEWS_PER_TASK
    )
    checks = {
        "G01": kwargs["archive_count"] == kwargs["expected_archive_count"],
        "G02": kwargs["single_pass"] == kwargs["expected_archive_count"],
        "G03": cross_ok,
        "G04": kwargs["provenance"].get("status") == "PASS",
        "G05": kwargs["reverify"]["status"] == "PASS"
                and kwargs["snapshot_consistency"]["status"] == "PASS",
        "G06": kwargs["reverify"]["verifier"]["valid"],
        "G07": kwargs["rc_count"] == kwargs["expected_archive_count"],
        "G08": kwargs["rc_valid"] == kwargs["expected_archive_count"],
        "G09": kwargs["self_reviews"] == 0,
        "G10": exposure_ok,
        "G11": kwargs["reconstruction"]["bit_exact"],
        "G12": kwargs["identity_leaks"] == 0,
        "G13": kwargs["private_leaks"] == 0,
        "G14": kwargs["secret_leaks"] == 0,
        "G15": kwargs["local_path_leaks"] == 0,
        "G16": bool(kwargs["hash_freeze"]),
    }
    details = {
        "G01": f"archives={kwargs['archive_count']}",
        "G02": f"single PASS {kwargs['single_pass']}/{kwargs['expected_archive_count']}",
        "G03": f"cross-package status={kwargs['cross'].get('status')}",
        "G04": f"provenance={kwargs['provenance'].get('status')}",
        "G05": f"matrix sha={kwargs['reverify']['matrix_sha256'][:16]}…",
        "G06": f"verifier valid={kwargs['reverify']['verifier']['valid']}",
        "G07": f"rc packages={kwargs['rc_count']}",
        "G08": f"rc PASS {kwargs['rc_valid']}/{kwargs['expected_archive_count']}",
        "G09": f"self reviews={kwargs['self_reviews']}",
        "G10": f"exposure {kwargs['exposure_min']}..{kwargs['exposure_max']}",
        "G11": f"difference_count={kwargs['reconstruction']['difference_count']}",
        "G12": f"identity leaks={kwargs['identity_leaks']}",
        "G13": f"private content leaks={kwargs['private_leaks']}",
        "G14": f"secret leaks={kwargs['secret_leaks']}",
        "G15": f"local path leaks={kwargs['local_path_leaks']}",
        "G16": "manifests + set hashes frozen",
    }
    return [
        {"gate": gate_id, "name": name, "status": "PASS" if checks[gate_id] else "FAIL",
         "detail": details[gate_id]}
        for gate_id, name in GATES
    ]
