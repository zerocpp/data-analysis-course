"""Gate A —— Phase1 Final Acceptance（§5–§17）。

每个 Archive 重新执行完整校验链（不复用历史 PASS）::

    archive -> validate_archive_envelope -> safe_extract -> validate_single_package
           -> ValidationResult                       （validate_submission_archive）

之后对全部 Single-PASS Candidate 重新执行 ``validate_cross_package``（§10），
并把 Final Accepted 与 Canonical Repository 逐行对账（§15/§16）。

只读：不修复、不重新 ingest、不改 canonical。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from codesetarena.submission.archive import validate_submission_archive
from codesetarena.submission.batch.validator import validate_cross_package
from codesetarena.submission.registry import SubmissionRegistry

from .contract import CANONICAL_DRIFT, PhaseFError

VALIDATION_RULE_VERSION = "submission-contract.v2"
_VALIDATOR_VERSION = "codesetarena.submission.archive.validate_submission_archive"


def validate_archives_fresh(archive_paths: list[Path], *, log=None) -> list[dict[str, Any]]:
    """§5：逐包重新调用唯一官方 Single-Package Validator（含 envelope + safe extract）。"""
    results: list[dict[str, Any]] = []
    for index, path in enumerate(sorted(archive_paths), start=1):
        result = validate_submission_archive(path)
        result["archive_sha256"] = None  # 由调用方用冻结值填充
        results.append(result)
        if log:
            log(f"[single {index}/{len(archive_paths)}] {path.name} -> {result['status']}")
    return results


def cross_package_fresh(single_results: list[dict[str, Any]]) -> dict[str, Any]:
    """§10：只对 Single-PASS Candidate 重新执行正式跨包校验。"""
    candidates = [r for r in single_results if r.get("status") == "PASS"]
    return validate_cross_package(candidates)


def build_acceptance_rows(
    inventory: list[dict],
    single_results: list[dict[str, Any]],
    cross: dict[str, Any],
    registry: SubmissionRegistry,
) -> list[dict[str, Any]]:
    """§12：Final Acceptance Manifest 行（冻结记录，后续 RC 只引用它）。"""
    frozen = {row["archive_filename"]: row for row in inventory}
    conflict_by_archive: dict[str, str] = {}
    for conflict in cross.get("errors", []):
        for archive in conflict.get("packages", []):
            conflict_by_archive.setdefault(archive, conflict["code"])

    rows: list[dict[str, Any]] = []
    for result in sorted(single_results, key=lambda r: r["archive"]):
        inv = frozen.get(result["archive"], {})
        registry_row = registry.lookup_archive_sha256(inv.get("sha256") or "") or {}
        mapping = registry.find_anonymous_task(registry_row.get("task_id") or "") or {}
        single_status = result.get("status", "FAIL")
        batch_status = "NOT_RUN" if single_status != "PASS" else (
            "FAIL" if result["archive"] in conflict_by_archive else "PASS"
        )
        final = "ACCEPTED" if single_status == "PASS" and batch_status == "PASS" else "REJECTED"
        rows.append({
            "archive_filename": result["archive"],
            "archive_sha256": inv.get("sha256"),
            "student_id": result.get("student_id") or inv.get("student_id", ""),
            "submission_id": registry_row.get("submission_id", ""),
            "task_id": result.get("task_id") or registry_row.get("source_task_id", ""),
            "agent_id": registry_row.get("agent_id", ""),
            "phase": result.get("submission_phase") or inv.get("phase", ""),
            "single_status": single_status,
            "batch_status": batch_status,
            "final_status": final,
            "error_code": result.get("error_code"),
            "validation_rule_version": VALIDATION_RULE_VERSION,
            "validator_version": _VALIDATOR_VERSION,
            "canonical_task_id": registry_row.get("task_id", ""),
            "canonical_agent_id": registry_row.get("agent_id", ""),
            "anonymous_task_id": mapping.get("anonymous_task_id", ""),
            "registry_submission_id": registry_row.get("submission_id", ""),
        })
    return rows


def accepted_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if row["final_status"] == "ACCEPTED"]


# ---------------------------------------------------------------------------
# §15/§16：Canonical Provenance（registry ↔ archive ↔ canonical 对象）
# ---------------------------------------------------------------------------


def canonical_provenance(repo: Path, accepted: list[dict[str, Any]]) -> dict[str, Any]:
    """Final Accepted Submissions 与 Canonical Repository 的一致性对账。

    - registry 行数 == accepted 数（无 orphan submission）；
    - 每 accepted 的 archive_sha256 在 registry 唯一命中（owner / ID 不漂移）；
    - canonical tasks / agents 目录与 registry 一一对应（无 orphan / missing）；
    - anonymous mapping 双向唯一（canonical ↔ AT，无 duplicate mapping）。
    任一不一致 -> ``PHASE1_CANONICAL_DRIFT``（绝不自动 re-ingest）。
    """
    repo = Path(repo)
    registry = SubmissionRegistry(repo)
    problems: list[str] = []

    rows_by_sha: dict[str, list[dict]] = {}
    for row in registry.rows:
        rows_by_sha.setdefault(row.get("archive_sha256") or "", []).append(row)

    accepted_tasks: set[str] = set()
    accepted_agents: set[str] = set()
    for entry in accepted:
        hits = rows_by_sha.get(entry["archive_sha256"] or "", [])
        if len(hits) != 1:
            problems.append(
                f"{entry['archive_filename']}: registry sha256 命中 {len(hits)} 行"
            )
            continue
        row = hits[0]
        accepted_tasks.add(row.get("task_id") or "")
        accepted_agents.add(row.get("agent_id") or "")
        if row.get("student_id") != entry["student_id"]:
            problems.append(f"{entry['archive_filename']}: student_id 与 registry 不一致")

    canonical_tasks = sorted(p.name for p in (repo / "tasks").iterdir() if p.is_dir())
    canonical_agents = sorted(p.name for p in (repo / "agents").iterdir() if p.is_dir())
    for task_id in sorted(accepted_tasks - set(canonical_tasks)):
        problems.append(f"orphan submission task: {task_id} 无 canonical 目录")
    for agent_id in sorted(accepted_agents - set(canonical_agents)):
        problems.append(f"orphan submission agent: {agent_id} 无 canonical 目录")
    for task_id in sorted(set(canonical_tasks) - accepted_tasks):
        problems.append(f"orphan canonical task: {task_id} 无 accepted submission")
    for agent_id in sorted(set(canonical_agents) - accepted_agents):
        problems.append(f"orphan canonical agent: {agent_id} 无 accepted submission")
    for task_id in sorted(accepted_tasks):
        if not (repo / "tasks" / task_id / "metadata.json").is_file():
            problems.append(f"canonical task 缺 metadata.json: {task_id}")

    anon_task_ids = [m for m in registry.mappings if m.get("anonymous_task_id")]
    by_task: dict[str, int] = {}
    by_at: dict[str, int] = {}
    for mapping in anon_task_ids:
        by_task[mapping["task_id"]] = by_task.get(mapping["task_id"], 0) + 1
        by_at[mapping["anonymous_task_id"]] = by_at.get(mapping["anonymous_task_id"], 0) + 1
    problems += [
        f"duplicate anonymous mapping for task {key}"
        for key, count in sorted(by_task.items()) if count != 1
    ]
    problems += [
        f"duplicate anonymous_task_id {key}"
        for key, count in sorted(by_at.items()) if count != 1
    ]
    missing_at = sorted(accepted_tasks - set(by_task))
    problems += [f"canonical task 无 anonymous_task_id: {t}" for t in missing_at]

    result = {
        "registry_rows": len(registry.rows),
        "accepted": len(accepted),
        "canonical_tasks": len(canonical_tasks),
        "canonical_agents": len(canonical_agents),
        "anonymous_task_mappings": len(anon_task_ids),
        "problems": problems,
        "status": "PASS" if not problems else "FAIL",
    }
    if problems:
        raise PhaseFError(
            CANONICAL_DRIFT,
            f"{len(problems)} 项不一致（示例: {problems[:5]}）",
            gate="G04",
        )
    return result


def write_manifest(rows: list[dict[str, Any]], path: str | Path) -> Path:
    """§13：Final Acceptance Manifest 是冻结记录（JSONL）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
    )
    path.write_text(payload, encoding="utf-8")
    return path
