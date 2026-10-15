"""Cross-Phase Workflow Check —— 同一个学生跨 Phase 的纵向一致性。

只读取服务器自己的产物（submissions/*/server_metadata.json），对比：

- student_id 跨阶段一致（否则 STUDENT_ID_MISMATCH）；
- task_id 跨阶段一致（正式协议要求 Task Identity 跨阶段保持，否则
  TASK_ID_MISMATCH）；
- 任一个 Phase 缺少提交（MISSING_SUBMISSION）。

读取的是真实 server artifact，不是重新校验 Base 规则。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from . import contract as C
from .receiver import SERVER_METADATA


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _submission_identity(pool_root: str | Path, student_id: str, phase: str) -> dict[str, Any] | None:
    """读取某学生某阶段最近一份 server_metadata（无提交返回 None）。"""

    phase_dir = Path(pool_root) / C.SUBMISSIONS / student_id / C.phase_dir(phase)
    if not phase_dir.is_dir():
        return None
    task_dirs = sorted(p for p in phase_dir.iterdir() if (p / SERVER_METADATA).is_file())
    if not task_dirs:
        return None
    metadata_path = task_dirs[-1] / SERVER_METADATA
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return {
        "student_id": str(metadata.get("student_id") or ""),
        "task_id": str(metadata.get("task_id") or ""),
        "submission_phase": str(metadata.get("submission_phase") or phase),
    }


def _other_student_by_task(pool_root: str | Path, task_id: str, phase: str,
                           exclude_student: str) -> dict[str, str] | None:
    """在 pool 中找其他学生同一 task_id 在指定阶段的提交（身份漂移检测）。"""

    phase_dir = Path(pool_root) / C.SUBMISSIONS
    if not phase_dir.is_dir():
        return None
    for student_dir in sorted(phase_dir.iterdir()):
        if not student_dir.is_dir() or student_dir.name == exclude_student:
            continue
        task_dir = student_dir / C.phase_dir(phase)
        if not task_dir.is_dir():
            continue
        for entry in task_dir.iterdir():
            metadata_path = entry / SERVER_METADATA
            if not metadata_path.is_file():
                continue
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if str(metadata.get("task_id") or "") == task_id:
                return {"student_id": student_dir.name,
                        "task_id": str(metadata.get("task_id") or "")}
    return None


def check_cross_phase(pool_root: str | Path, student_id: str) -> dict[str, Any]:
    """同一学生 P1 / P2 纵向一致性；返回 Validation Result（layer=workflow）。

    若某个阶段缺失，但同一 task_id 在另一阶段出现在其他学生名下，判定为
    STUDENT_ID_MISMATCH（学生跨阶段换了学号）；否则 MISSING_SUBMISSION。
    """

    errors: list[dict[str, Any]] = []
    identities: dict[str, dict[str, Any] | None] = {}
    for phase in (C.PHASE1, C.PHASE2):
        identities[phase] = _submission_identity(pool_root, student_id, phase)

    p1, p2 = identities.get(C.PHASE1), identities.get(C.PHASE2)
    if p1 is not None and p2 is None:
        other = _other_student_by_task(pool_root, p1["task_id"], C.PHASE2, student_id)
        if other is not None:
            errors.append(
                {
                    "code": C.STUDENT_ID_MISMATCH,
                    "location": "server_metadata",
                    "detail": (
                        f"task_id={p1['task_id']!r} 的 phase2 提交归属 {other['student_id']}，"
                        f"与 {student_id} 的 phase1 身份不一致（疑似跨阶段更换学号）"
                    ),
                }
            )
        else:
            errors.append(
                {
                    "code": C.MISSING_SUBMISSION,
                    "location": f"submissions/{student_id}/phase2",
                    "detail": f"学生 {student_id} 缺少 phase2 提交",
                }
            )
    elif p1 is None and p2 is not None:
        other = _other_student_by_task(pool_root, p2["task_id"], C.PHASE1, student_id)
        if other is not None:
            errors.append(
                {
                    "code": C.STUDENT_ID_MISMATCH,
                    "location": "server_metadata",
                    "detail": (
                        f"task_id={p2['task_id']!r} 的 phase1 提交归属 {other['student_id']}，"
                        f"与 {student_id} 的 phase2 身份不一致（疑似跨阶段更换学号）"
                    ),
                }
            )
        else:
            errors.append(
                {
                    "code": C.MISSING_SUBMISSION,
                    "location": f"submissions/{student_id}/phase1",
                    "detail": f"学生 {student_id} 缺少 phase1 提交",
                }
            )
    if p1 is not None and p2 is not None:
        for field, code in (("student_id", C.STUDENT_ID_MISMATCH), ("task_id", C.TASK_ID_MISMATCH)):
            if (p1[field] or "") != (p2[field] or ""):
                errors.append(
                    {
                        "code": code,
                        "location": "server_metadata",
                        "detail": (
                            f"跨阶段 {field} 不一致：phase1={p1[field]!r} vs "
                            f"phase2={p2[field]!r}"
                        ),
                    }
                )

    status = "FAIL" if errors else "PASS"
    return {
        "schema_version": C.VALIDATION_REPORT_SCHEMA_VERSION,
        "status": status,
        "validation_layer": C.LAYER_WORKFLOW,
        "validated_at": _now_iso(),
        "student_id": student_id,
        "error_code": errors[0]["code"] if errors else None,
        "error_count": len(errors),
        "errors": errors,
        "identities": {phase: (identities[phase] or {}) for phase in (C.PHASE1, C.PHASE2)},
    }