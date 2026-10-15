"""Gate B —— Phase2 Release Candidate 构建 / 校验 / 反向重建（§24–§51）。

RC 与未来正式分发**同构**：package 目录用正式
``assignment.distribution.build_reviewer_package``，archive 用正式
``build_reviewer_archive``（确定性字节）。RC 只落在隔离区
``release_candidates/``，绝不写正式 ``distributions/<AR>/reviewers/``。
"""

from __future__ import annotations

import json
import tarfile
import tempfile
from pathlib import Path
from typing import Any

import numpy as np

from codesetarena.assignment import distribution as dist_mod
from codesetarena.assignment.contract import DIST_TASK_COUNT, AssignmentError
from codesetarena.submission.archive import validate_archive_envelope
from codesetarena.submission.registry import SubmissionRegistry

from .contract import PhaseFError, RC_DIR_NAME

# 学生分发物禁止出现的文件（§8/§30/§38/§39）
RC_FORBIDDEN_FILES = (
    ".env",
    ".env.example",
    "runtime_policy.json",
    "assignment_policy.json",
    "agent.py",
    "agent_manifest.json",
    "reference.py",
    "gold.json",
    "reference_solution.py",
    "private_mapping.json",
    "submission_registry.jsonl",
    "anonymous_mapping.jsonl",
    "assignments.jsonl",
    "local_validation.json",
)

ASSIGNMENT_JSON = "assignment.json"


def reviewer_package_dirs(rc_dir: str | Path) -> Path:
    return Path(rc_dir) / "expanded"


def reviewer_archive_dir(rc_dir: str | Path) -> Path:
    return Path(rc_dir) / "packages"


# ---------------------------------------------------------------------------
# 构建（§24/§25/§41：正式 builder，隔离目录）
# ---------------------------------------------------------------------------


def build_release_candidates(
    repo: str | Path,
    run: dict,
    private_mapping: dict,
    rc_root: str | Path,
    *,
    log=None,
) -> list[dict[str, Any]]:
    """构建全部 Reviewer Package 目录 + 确定性 tar.gz（不触正式 distributions/）。"""
    repo = Path(repo)
    rc_dir = Path(rc_root) / run["assignment_run_id"]
    expanded_root = reviewer_package_dirs(rc_dir)
    archives_root = reviewer_archive_dir(rc_dir)
    archives_root.mkdir(parents=True, exist_ok=True)
    tasks_root = repo / "distributions" / "tasks"

    records: list[dict[str, Any]] = []
    total = len(private_mapping["reviewers"])
    for index, reviewer in enumerate(private_mapping["reviewers"], start=1):
        package_id = reviewer["reviewer_package_id"]
        package_dir = expanded_root / package_id
        dist_mod.build_reviewer_package(
            package_dir,
            run=run,
            reviewer_package_id=package_id,
            anonymous_task_ids=reviewer["anonymous_task_ids"],
            anonymous_tasks_root=tasks_root,
        )
        archive = archives_root / dist_mod.reviewer_archive_name(package_id)
        dist_mod.build_reviewer_archive(package_dir, archive)
        stat = archive.stat()
        records.append({
            "release_candidate_id": package_id,
            "reviewer_package_id": package_id,
            "reviewer_student_id": reviewer["reviewer_student_id"],
            "package_filename": archive.name,
            "package_path": str(archive),
            "sha256": _sha256(archive),
            "size_bytes": stat.st_size,
            "task_count": len(reviewer["anonymous_task_ids"]),
            "anonymous_task_ids": list(reviewer["anonymous_task_ids"]),
        })
        if log:
            log(f"[rc {index}/{total}] {archive.name} tasks={len(reviewer['anonymous_task_ids'])}")
    return records


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# 单包校验（§44：正式 validator + RC 契约附加检查，不复制解包/结构逻辑）
# ---------------------------------------------------------------------------


def validate_rc_package(
    archive_path: str | Path,
    r: int,
    *,
    secret_tokens: tuple | list = (),
) -> dict[str, Any]:
    """最终 RC Archive 校验：envelope + 正式 ``validate_reviewer_archive`` +
    assignment.json schema + 禁止文件 + 匿名任务目录一致性。"""
    from codesetarena.assignment.contract import DIST_MISSING_TASK, DIST_UNKNOWN_TASK

    archive_path = Path(archive_path)
    errors: list[str] = []
    manifest: dict[str, Any] = {}

    result = dist_mod.validate_reviewer_archive(
        archive_path, r, secret_tokens=tuple(secret_tokens)
    )
    errors.extend(result.get("errors") or [])

    try:
        validate_archive_envelope(archive_path)
    except Exception as exc:  # noqa: BLE001 - envelope 护栏失败即整包失败
        errors.append(f"{DIST_MISSING_TASK}: envelope: {exc}")

    with tarfile.open(archive_path, "r:gz") as tar:
        members = tar.getmembers()
        names = [m.name for m in members]
        headers = _header_violations(members)
        errors.extend(headers)
        for name in names:
            if Path(name).name in RC_FORBIDDEN_FILES:
                errors.append(f"DIST_FORBIDDEN_FILE: {name}")
        member = tar.extractfile(ASSIGNMENT_JSON) if ASSIGNMENT_JSON in names else None
        if member is None:
            errors.append(f"{DIST_MISSING_TASK}: 根目录缺 {ASSIGNMENT_JSON}")
        else:
            try:
                manifest = json.loads(member.read().decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                errors.append(f"DIST_ARCHIVE_INVALID: {ASSIGNMENT_JSON} 解析失败: {exc}")
        task_dirs = sorted({
            d for d in (dist_mod.member_task_dir(name) for name in names) if d
        })

    anonymous_tasks = manifest.get("anonymous_tasks") or [] if manifest else []
    if manifest:
        if sorted(anonymous_tasks) != task_dirs:
            errors.append(
                f"{DIST_UNKNOWN_TASK}: assignment.json 与 tasks/ 目录不一致"
            )
        if len(set(anonymous_tasks)) != len(anonymous_tasks):
            errors.append(f"{DIST_TASK_COUNT}: anonymous_tasks 含重复")
        if manifest.get("distribution_phase") != dist_mod.DISTRIBUTION_PHASE:
            errors.append("DIST_PHASE: distribution_phase 必须为 phase2")
        if not manifest.get("source_phase"):
            errors.append("DIST_PHASE: 缺 source_phase")
        for field in ("student_id", "reviewer_student_id", "task_id", "owner",
                      "submission_id", "source_task_id", "source_agent_id",
                      "original_archive"):
            if field in manifest:
                errors.append(f"DIST_IDENTITY_LEAK: manifest 含禁止字段 {field}")

    return {
        "archive": archive_path.name,
        "valid": not errors,
        "errors": errors,
        "task_count": result.get("task_count", -1),
        "anonymous_tasks": anonymous_tasks,
        "manifest": manifest,
    }


def _header_violations(members: list) -> list[str]:
    """§43：tar header（member 名 / uname / gname / linkname / pax）也是泄露面。"""
    errors: list[str] = []
    for member in members:
        for attribute in ("uname", "gname", "linkname"):
            value = getattr(member, attribute, "") or ""
            if value.strip():
                errors.append(
                    f"DIST_ARCHIVE_HEADER: {member.name} {attribute}={value!r}"
                )
        for key in (member.pax_headers or {}):
            errors.append(f"DIST_ARCHIVE_HEADER: {member.name} pax:{key}")
    return errors


# ---------------------------------------------------------------------------
# 反向重建 / No-Self / Exposure（§45–§50）
# ---------------------------------------------------------------------------


def reconstruct_matrix_from_archives(
    archives: list[Path],
    *,
    private_mapping: dict,
    registry: SubmissionRegistry,
    snapshot: dict,
    r: int,
) -> dict[str, Any]:
    """§48：从最终 RC Archive 反向重建完整矩阵（reader-only，不信任目录）。"""
    reviewer_index = {
        row["reviewer_package_id"]: row["reviewer_student_id"]
        for row in private_mapping["reviewers"]
    }
    reviewer_order = snapshot["reviewer_order"]
    task_order = snapshot["task_order"]
    position = {sid: i for i, sid in enumerate(reviewer_order)}
    task_index = {tid: j for j, tid in enumerate(task_order)}
    n = len(reviewer_order)
    matrix = [[0] * n for _ in range(n)]
    errors: list[str] = []
    per_package: list[dict[str, Any]] = []

    for archive in sorted(archives, key=lambda p: p.name):
        package_id = _package_id_of(archive.name)
        student = reviewer_index.get(package_id)
        if student is None or student not in position:
            errors.append(f"{archive.name}: reviewer_package_id {package_id} 无私有映射")
            continue
        i = position[student]
        manifest = _read_assignment_json(archive)
        if manifest is None:
            errors.append(f"{archive.name}: 缺 assignment.json")
            continue
        ats = manifest.get("anonymous_tasks") or []
        canonical_tasks = []
        for at in ats:
            mapping = registry.find_canonical_task(at)
            if mapping is None:
                errors.append(f"{archive.name}: {at} 无 canonical 回溯")
                continue
            task_id = mapping["task_id"]
            if task_id not in task_index:
                errors.append(f"{archive.name}: {task_id} 不在本次 assignment")
                continue
            matrix[i][task_index[task_id]] += 1
            canonical_tasks.append(task_id)
        per_package.append({
            "reviewer_package_id": package_id,
            "reviewer_student_id": student,
            "task_count": len(ats),
            "unique_anonymous_tasks": len(set(ats)),
            "unique_canonical_tasks": len(set(canonical_tasks)),
            "canonical_task_ids": canonical_tasks,
            "archive": archive.name,
        })
    return {
        "matrix": matrix,
        "errors": errors,
        "packages": per_package,
        "package_count": len(per_package),
        "r": r,
    }


def _package_id_of(archive_name: str) -> str:
    name = Path(archive_name).name
    if name.startswith("review_package_") and name.endswith(".tar.gz"):
        return name[len("review_package_"):-len(".tar.gz")]
    return name


def _read_assignment_json(archive: Path) -> dict | None:
    try:
        with tarfile.open(archive, "r:gz") as tar:
            member = tar.extractfile(ASSIGNMENT_JSON)
            if member is None:
                return None
            return json.loads(member.read().decode("utf-8"))
    except (tarfile.TarError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def compare_matrix(reconstructed: list[list[int]], expected_path: str | Path) -> dict:
    """§49：bit-exact 比较（``numpy.array_equal``）。"""
    expected = np.load(expected_path)
    actual = np.asarray(reconstructed, dtype=np.uint8)
    difference = int(np.count_nonzero(expected != actual))
    return {
        "matrix_shape_expected": list(expected.shape),
        "matrix_shape_reconstructed": list(actual.shape),
        "expected_total": int(expected.sum()),
        "reconstructed_total": int(actual.sum()),
        "difference_count": difference,
        "bit_exact": bool(np.array_equal(expected, actual)),
    }


def task_exposure(matrix: list[list[int]], task_order: list[str]) -> dict[str, int]:
    exposure: dict[str, int] = {task_id: 0 for task_id in task_order}
    for row in matrix:
        for j, value in enumerate(row):
            if value:
                exposure[task_order[j]] += 1
    return exposure


def package_no_self(
    packages: list[dict[str, Any]],
    registry: SubmissionRegistry,
    snapshot: dict,
) -> tuple[list[dict[str, Any]], list[str]]:
    """§45：私有映射 reviewer -> 自己的 canonical task（Registry 权威 owner），
    逐包验证 own task 不在其 5 个任务中（不信任矩阵对角线）。"""
    own_task_by_student = {
        row["student_id"]: row["task_id"] for row in registry.rows if row.get("task_id")
    }
    snapshot_owner = {t["task_id"]: t["owner_student_id"] for t in snapshot["tasks"]}
    rows: list[dict[str, Any]] = []
    problems: list[str] = []
    for package in packages:
        student = package["reviewer_student_id"]
        own_task = own_task_by_student.get(student)
        snapshot_task = next(
            (tid for tid, owner in snapshot_owner.items() if owner == student), None
        )
        assigned = list(package.get("canonical_task_ids") or [])
        self_hits = [tid for tid in assigned if tid in (own_task, snapshot_task)]
        rows.append({
            "reviewer_package_id": package["reviewer_package_id"],
            "reviewer_student_id": student,
            "own_task_id": own_task or "",
            "self_review": bool(self_hits),
        })
        if self_hits:
            problems.append(
                f"{package['reviewer_package_id']} ({student}): own task {self_hits}"
            )
    return rows, problems


def deterministic_rebuild_check(
    records: list[dict[str, Any]],
    *,
    repo: str | Path,
    run: dict,
    rc_dir: str | Path,
    sample: int = 10,
) -> dict[str, Any]:
    """§59：抽样重建第二次，比较 SHA256（同一输入必须同字节）。"""
    repo = Path(repo)
    tasks_root = repo / "distributions" / "tasks"
    by_id = {r["reviewer_package_id"]: r for r in records}
    checked: list[dict[str, str]] = []
    mismatches: list[str] = []
    for package_id in sorted(by_id)[: max(1, sample)]:
        record = by_id[package_id]
        with tempfile.TemporaryDirectory(prefix="csa_rc_rebuild_") as tmp:
            package_dir = Path(tmp) / RC_DIR_NAME / package_id
            try:
                dist_mod.build_reviewer_package(
                    package_dir,
                    run=run,
                    reviewer_package_id=package_id,
                    anonymous_task_ids=record["anonymous_task_ids"],
                    anonymous_tasks_root=tasks_root,
                )
            except AssignmentError as exc:
                mismatches.append(f"{package_id}: rebuild failed: {exc}")
                continue
            rebuilt = dist_mod.build_reviewer_archive(package_dir, Path(tmp) / "reb.tar.gz")
            if _sha256(rebuilt) != record["sha256"]:
                mismatches.append(package_id)
            checked.append({
                "reviewer_package_id": package_id,
                "sha256": record["sha256"],
                "rebuilt_sha256": _sha256(rebuilt),
            })
    return {"checked": checked, "mismatches": mismatches,
            "status": "PASS" if not mismatches else "FAIL"}


def require_valid_packages(validations: list[dict[str, Any]], expected: int) -> None:
    """§44：RC_PACKAGE_PASS 必须 200/200。"""
    passed = sum(1 for v in validations if v["valid"])
    if passed != expected or len(validations) != expected:
        failed = [v["archive"] for v in validations if not v["valid"]][:5]
        raise PhaseFError(
            "RC_PACKAGE_VALIDATION_FAILED",
            f"RC package PASS {passed}/{expected}（示例失败: {failed}）",
            gate="G08",
        )
