"""Release Lineage / Set Hash（§80–§84）。

链路::

    phase1_submission_set_sha256
      -> canonical_repository_snapshot_sha256
      -> assignment_input_snapshot_sha256
      -> assignment_matrix_sha256
      -> anonymous_mapping_sha256
      -> phase2_release_candidate_set_sha256

未来正式发放的约束（§84）：不重新生成 Assignment / Anonymous Mapping / Package，
只允许读取 ``RELEASE_CANDIDATE_VERIFIED`` 且 hash 未变化的同一批 RC bytes。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .contract import (
    RELEASE_AWAITING_EXPLICIT,
    RELEASE_NOT_RELEASED,
    RC_SET_HASH_KEY,
    RC_STATUS_VERIFIED,
    SCHEMA_VERSION,
)


def component_set_sha256(pairs: list[tuple[str, str]]) -> str:
    """整批对象的 Set Hash：按 id 排序拼接 ``id\\0sha256\\0``。"""
    digest = hashlib.sha256()
    for identifier, sha in sorted(pairs):
        digest.update(identifier.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(sha.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


def rc_set_sha256(records: list[dict]) -> str:
    """§80：Release Candidate 整批 Set Hash（package id + sha256）。"""
    return component_set_sha256(
        [(r["release_candidate_id"], r["sha256"]) for r in records]
    )


def canonical_repository_snapshot_sha256(repo: str | Path) -> str:
    """Canonical Repository 快照哈希（tasks/ + agents/ + registry/ 内容）。"""
    repo = Path(repo)
    digest = hashlib.sha256()
    for sub in ("tasks", "agents", "registry"):
        base = repo / sub
        for path in sorted(p for p in base.rglob("*") if p.is_file()):
            digest.update(str(Path(sub) / path.relative_to(base)).encode("utf-8"))
            digest.update(b"\x00")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
            digest.update(b"\x00")
    return digest.hexdigest()


def file_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def anonymous_mapping_sha256(repo: str | Path) -> str:
    return file_sha256(Path(repo) / "registry" / "anonymous_mapping.jsonl")


def build_release_lineage(
    *,
    phase1_set_hash: str,
    canonical_snapshot_hash: str,
    assignment_run_id: str,
    input_snapshot_hash: str,
    matrix_sha256: str,
    anon_mapping_hash: str,
    rc_set_hash: str,
) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "phase1_submission_set_sha256": phase1_set_hash,
        "canonical_repository_snapshot_sha256": canonical_snapshot_hash,
        "assignment_run_id": assignment_run_id,
        "assignment_input_snapshot_sha256": input_snapshot_hash,
        "assignment_matrix_sha256": matrix_sha256,
        "anonymous_mapping_sha256": anon_mapping_hash,
        RC_SET_HASH_KEY: rc_set_hash,
        "phase2_rc_status": RC_STATUS_VERIFIED,
        "release_status": RELEASE_NOT_RELEASED,
        "next_action": RELEASE_AWAITING_EXPLICIT,
        "release_rule": (
            "explicit release only: reuse these exact verified bytes; "
            "no re-solve, no re-mapping, no rebuild"
        ),
    }


def write_json(path: str | Path, payload) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def write_jsonl(path: str | Path, rows: list[dict]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path
