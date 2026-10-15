"""Server Batch Global Validation —— cohort 级（Local 无法验证）规则。

只读取服务器自己的产物（``submissions/<student>/<phase>/<task_id>/
server_metadata.json`` + ``task/task.json``），**绝不重新实现 Base 规则**：

- DUPLICATE_TASK_ID：同一阶段多个 active submission 使用相同 task_id；
- DUPLICATE_ACTIVE_STUDENT_ID：同一学生同一阶段存在多个 active submission；
- UNKNOWN_STUDENT_ID：student_id 不在官方 roster（身份已在 receive 时校验，
  这里做 cohort 汇总复审）。

报告写入 ``validation/batch_report.json``（status / error_codes / errors /
packages 列表）。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from . import contract as C
from .receiver import BATCH_REPORT, SERVER_METADATA


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def scan_submissions(pool_root: str | Path, phase: str) -> list[dict[str, Any]]:
    """扫描 pool 内某阶段的所有 canonical submission（读取 server_metadata）。"""

    root = Path(pool_root)
    submissions: list[dict[str, Any]] = []
    submissions_dir = root / C.SUBMISSIONS
    if not submissions_dir.is_dir():
        return submissions
    phase_name = C.phase_dir(phase)
    for student_dir in sorted(submissions_dir.iterdir()):
        if not student_dir.is_dir():
            continue
        phase_dir = student_dir / phase_name
        if not phase_dir.is_dir():
            continue
        for task_dir in sorted(phase_dir.iterdir()):
            metadata_path = task_dir / SERVER_METADATA
            task_path = task_dir / C.TASK_JSON
            if not metadata_path.is_file():
                continue
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                metadata = {}
            task: dict[str, Any] = {}
            if task_path.is_file():
                try:
                    task = json.loads(task_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    task = {}
            submissions.append(
                {
                    "student_id": str(metadata.get("student_id") or student_dir.name),
                    "task_id": str(metadata.get("task_id") or task.get("task_id") or ""),
                    "task_dir": str(task_dir),
                    "submission_phase": str(metadata.get("submission_phase") or phase),
                    "submission_batch": str(metadata.get("submission_batch") or "main"),
                }
            )
    return submissions


def validate_batch_submissions(
    pool_root: str | Path,
    roster: str | Path,
    *,
    phases: tuple[str, ...] = (C.PHASE1, C.PHASE2),
) -> dict[str, Any]:
    """cohort 全局校验（真实 server artifact 驱动，无业务规则重实现）。"""

    from .identity import load_roster

    errors: list[dict[str, Any]] = []
    packages: list[dict[str, Any]] = []
    roster_students = load_roster(roster)

    for phase in phases:
        for pkg in scan_submissions(pool_root, phase):
            packages.append(pkg)
            if pkg["student_id"] and pkg["student_id"] not in roster_students:
                errors.append(
                    {
                        "code": C.UNKNOWN_STUDENT_ID,
                        "package": pkg["task_dir"],
                        "student_id": pkg["student_id"],
                        "detail": "cohort 复审：student_id 不在官方 roster",
                    }
                )

    for phase in phases:
        by_task: dict[str, list[dict[str, Any]]] = {}
        for pkg in packages:
            if pkg["submission_phase"] != phase:
                continue
            if pkg["task_id"]:
                by_task.setdefault(pkg["task_id"], []).append(pkg)
        for task_id, members in sorted(by_task.items()):
            if len(members) > 1:
                errors.append(
                    {
                        "code": C.DUPLICATE_TASK_ID,
                        "task_id": task_id,
                        "phase": phase,
                        "detail": f"{phase} 多个 active submission 使用相同 task_id",
                        "packages": [p["task_dir"] for p in members],
                    }
                )
        by_student: dict[str, list[dict[str, Any]]] = {}
        for pkg in packages:
            if pkg["submission_phase"] != phase:
                continue
            if pkg["student_id"]:
                by_student.setdefault(pkg["student_id"], []).append(pkg)
        for student_id, members in sorted(by_student.items()):
            if len(members) > 1:
                errors.append(
                    {
                        "code": C.DUPLICATE_ACTIVE_STUDENT_ID,
                        "student_id": student_id,
                        "phase": phase,
                        "detail": f"{phase} 同一 student_id 存在多个 active submission，需人工裁决",
                        "packages": [p["task_dir"] for p in members],
                    }
                )

    status = "PASS" if not errors else "FAIL"
    report = {
        "schema_version": C.VALIDATION_REPORT_SCHEMA_VERSION,
        "status": status,
        "validation_layer": C.LAYER_BATCH,
        "validated_at": _now_iso(),
        "package_count": len(packages),
        "error_count": len(errors),
        "error_code": errors[0]["code"] if errors else None,
        "error_codes": sorted({e["code"] for e in errors}),
        "errors": errors,
        "packages": packages,
    }
    return report


def write_batch_report(report: dict[str, Any], validation_dir: str | Path) -> Path:
    path = Path(validation_dir) / BATCH_REPORT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path