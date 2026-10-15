"""Server Identity Validation —— 外-内-Roster 三方一致性校验。

原则（§10-§12）：

- ``outer_student_id == manifest_student_id == task_student_id ==
  agent_student_id == roster student_id``，任何不一致 => STUDENT_ID_MISMATCH；
- 外部 Phase 与内部 manifest.submission_phase 不一致 => SUBMISSION_PHASE_MISMATCH；
- 内外一致但 roster 不存在 => UNKNOWN_STUDENT_ID；
- archive filename 不符合协议 => INVALID_ARCHIVE_NAME。

**绝不自动修复身份问题**：任何身份错误都进入人工裁决（receive 停止，
不写 canonical）。
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from . import contract as C
from .base_validation import _json_object


class IdentityError(RuntimeError):
    """身份校验失败（含错误码）。"""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def parse_archive_name(filename: str) -> dict[str, str]:
    """解析 <course>_P<phase>_<student_id>.<zip|tar.gz>；不符合协议抛 IdentityError。"""

    match = C.ARCHIVE_NAME_PATTERN.match(filename)
    if not match:
        raise IdentityError(
            C.INVALID_ARCHIVE_NAME,
            f"archive 文件名 {filename!r} 不符合协议 "
            f"<course>_P<phase>_<student_id>.zip / .tar.gz",
        )
    return {
        "course": match.group("course"),
        "phase": "phase1" if match.group("phase") == "1" else "phase2",
        "student_id": match.group("student_id"),
    }


def _read_location_values(root: Path) -> list[dict[str, str]]:
    """读取包内全部官方身份位置（manifest / task.json / agent_manifest）。"""

    locations: list[dict[str, str]] = []
    for relative, keys in (
        (C.MANIFEST, ("student_id", "submission_phase", "task_id")),
        (C.TASK_JSON, ("student_id", "task_id")),
        (C.AGENT_MANIFEST, ("student_id", "task_id")),
    ):
        path = root / relative
        if not path.is_file():
            continue
        data = _json_object(path)
        if data is None:
            continue
        locations.append({"location": relative, **{k: str(data.get(k) or "").strip() for k in keys}})
    return locations


def collect_inner_identity(root: str | Path) -> dict[str, Any]:
    """收集包内身份声明（缺失位置跳过，交给 Base Validation 报错）。"""

    root = Path(root)
    locations = _read_location_values(root)
    manifest = next((loc for loc in locations if loc["location"] == C.MANIFEST), None)
    return {
        "locations": locations,
        "submission_phase": (manifest or {}).get("submission_phase") or "",
        "student_ids": sorted({loc["student_id"] for loc in locations if loc["student_id"]}),
        "task_ids": sorted({loc["task_id"] for loc in locations if loc["task_id"]}),
    }


def load_roster(path: str | Path) -> list[str]:
    """读取官方 Roster（CSV，列名 student_id；或纯文本每行一个学号）。"""

    path = Path(path)
    students: list[str] = []
    if not path.is_file():
        return students
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if lines and "," in lines[0]:
        rows = list(csv.reader(lines))
        if not rows:
            return students
        header = [h.strip().lower() for h in rows[0]]
        if "student_id" in header:
            column = header.index("student_id")
            body = rows[1:]
        else:
            column = 0
            body = rows
        for row in body:
            if row and row[column].strip():
                students.append(row[column].strip())
    else:
        students = [line.strip() for line in text.splitlines() if line.strip()]
    # 去重并保持顺序
    seen: set[str] = set()
    unique: list[str] = []
    for student in students:
        if student not in seen:
            seen.add(student)
            unique.append(student)
    return unique


def cross_check_identity(
    outer: dict[str, str],
    inner: dict[str, Any],
    roster_students: list[str],
) -> dict[str, Any]:
    """外-内-Roster 一致性校验。返回 Validation Result（layer=identity）。"""

    errors: list[dict[str, Any]] = []

    if "phase" in outer and inner["submission_phase"]:
        if inner["submission_phase"] != outer["phase"]:
            errors.append(
                {
                    "code": C.SUBMISSION_PHASE_MISMATCH,
                    "location": C.MANIFEST,
                    "detail": (
                        f"outer phase={outer['phase']} 与 manifest.submission_phase="
                        f"{inner['submission_phase']!r} 不一致，不做猜测"
                    ),
                }
            )

    outer_student = outer.get("student_id", "")
    if outer_student:
        if inner["student_ids"] and any(s != outer_student for s in inner["student_ids"]):
            errors.append(
                {
                    "code": C.STUDENT_ID_MISMATCH,
                    "location": "archive-filename",
                    "detail": (
                        f"outer student_id={outer_student} 与包内身份 "
                        f"{inner['student_ids']} 不一致"
                    ),
                }
            )
        if outer_student not in roster_students:
            errors.append(
                {
                    "code": C.UNKNOWN_STUDENT_ID,
                    "location": "roster",
                    "detail": f"student_id={outer_student} 不在官方 roster 中",
                }
            )

    return {
        "schema_version": C.SCHEMA_VERSION,
        "status": "FAIL" if errors else "PASS",
        "validation_layer": C.LAYER_IDENTITY,
        "error_code": errors[0]["code"] if errors else None,
        "error_count": len(errors),
        "errors": errors,
        "identity": {
            "outer_student_id": outer.get("student_id", ""),
            "inner_student_ids": inner["student_ids"],
            "outer_phase": outer.get("phase", ""),
            "inner_phase": inner["submission_phase"],
        },
    }