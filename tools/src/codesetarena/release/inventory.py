"""Phase1 Archive Inventory（§3/§4/§81）：扫描 / SHA 冻结 / Set Hash。

只读：绝不修改、重写、重压缩、覆盖任何原始 *.tar.gz。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from codesetarena.submission.identity import IdentityError, parse_archive_name
from codesetarena.submission.safe_extract import sha256_file

from .contract import ARCHIVE_COUNT_MISMATCH, PhaseFError


def scan_archive_inventory(submissions_dir: str | Path) -> list[dict]:
    """扫描 submissions 目录（*.tar.gz，按文件名排序），生成只读清单行。"""
    root = Path(submissions_dir)
    rows: list[dict] = []
    for path in sorted(root.glob("*.tar.gz")):
        stat = path.stat()
        identity = {"course": "", "phase": "", "student_id": "", "identity_parse": "OK"}
        try:
            parsed = parse_archive_name(path.name)
            identity.update(parsed)
        except IdentityError as exc:
            identity["identity_parse"] = exc.code
        rows.append({
            "archive_filename": path.name,
            "path": str(path),
            "size_bytes": stat.st_size,
            "sha256": sha256_file(path),
            "mtime_epoch": int(stat.st_mtime),
            **identity,
        })
    return rows


def require_archive_count(rows: list[dict], expected: int) -> None:
    """§3：数量不是期望值立即失败（默认 200）。"""
    if len(rows) != expected:
        raise PhaseFError(
            ARCHIVE_COUNT_MISMATCH,
            f"submissions 目录发现 {len(rows)} 个 *.tar.gz，期望 {expected}",
            gate="G01",
        )


def write_inventory(rows: list[dict], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path


def freeze_hashes(rows: list[dict]) -> dict[str, str]:
    """冻结 {archive_filename: sha256}（§4/§7 的 before 快照）。"""
    return {row["archive_filename"]: row["sha256"] for row in rows}


def detect_mutations(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """§4：before == after 才算零修改；返回变更文件名列表。"""
    mutations = []
    for name in sorted(set(before) | set(after)):
        if before.get(name) != after.get(name):
            mutations.append(name)
    return mutations


def submission_set_sha256(rows: list[dict]) -> str:
    """§81：整批 Phase1 输入的 Set Hash（按文件名排序拼接 filename + sha256）。"""
    digest = hashlib.sha256()
    for row in sorted(rows, key=lambda r: r["archive_filename"]):
        digest.update(row["archive_filename"].encode("utf-8"))
        digest.update(b"\x00")
        digest.update(row["sha256"].encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()
