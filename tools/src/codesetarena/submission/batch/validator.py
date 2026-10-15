"""Server Batch Validator —— 第一遍单包 + 第二遍跨包（v2 §24–§31）。

第一遍（``validate_submission_archive``，batch-validate 的 N × validate，§13）
对每个 archive 执行：

    Archive Intake（文档 §8/§18：可打开 + 解压炸弹护栏 + safe_extract）
        -> 共享 Single-Package Validator（``validate_single_package``）

第二遍（``validate_cross_package``）只对 Single-Package PASS Candidate 执行，
真正属于包间的规则（§14/§28–§30）：

- ``(phase, student_id)`` 唯一性  -> DUPLICATE_ACTIVE_STUDENT_ID
- ``(phase, task_id)`` 唯一性     -> DUPLICATE_TASK_ID
- archive basename 唯一性         -> DUPLICATE_ARCHIVE

所有可解析包中观察到的重复（含 FAIL 包）记录进 ``diagnostics`` 供助教人工
排查，与正式 reject 条件（只作用于 PASS Candidate）分离（v2 §27）。

Batch Validator 只读：不修复、不重写、不信任学生本地 validation 状态。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import contract as C
from ..archive import validate_submission_archive
from .collector import aggregate, find_archives

BATCH_REPORT = "batch_report.json"


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _package_row(result: dict[str, Any], archive_name: str) -> dict[str, Any]:
    """从 Single-Package 结果提取 Batch 主表行（§32）。"""

    error_code = result.get("error_code")
    first_error = next(iter(result.get("errors") or []), {})
    return {
        "archive": archive_name,
        "student_id": str((result.get("checks") or {}).get("student_id") or ""),
        "task_id": str((result.get("checks") or {}).get("task_id") or ""),
        "submission_phase": str((result.get("checks") or {}).get("submission_phase") or ""),
        "single_package_status": result.get("status", "FAIL"),
        "error_code": error_code,
        "error_detail": str(first_error.get("detail") or ""),
        "validation_layer": result.get("validation_layer", ""),
    }


def validate_archive(archive: str | Path) -> dict[str, Any]:
    """Batch 第一遍：Server Archive Intake + 共享 Single-Package Validation。

    与 ``validate_submission_archive`` 是同一份实现（功能别名，§13：batch 必须
    复用 validate 的 Python API，绝不自己实现第二套解包/校验）；额外补充维度
    来自返回结果的 checks / errors。只读。
    """

    body = validate_submission_archive(archive)
    result = _package_row(body, Path(archive).name)
    result["errors"] = body.get("errors", [])
    return result


# ---------------------------------------------------------------------------
# 第二遍：跨包规则（只作用于 PASS Candidate）
# ---------------------------------------------------------------------------


def _duplicates(groups: dict[Any, list[dict[str, Any]]], code: str, field: str) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    for key, members in sorted(groups.items(), key=lambda kv: str(kv[0])):
        if len(members) > 1:
            conflicts.append(
                {
                    "code": code,
                    "field": field,
                    "value": str(key),
                    "packages": [m["archive"] for m in members],
                    "detail": f"多个 active submission 共享 {field}={key!r}，需人工裁决",
                }
            )
    return conflicts


def validate_cross_package(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """跨包唯一性校验（只作用于 Single-Package PASS Candidate，§28–§30）。"""

    errors: list[dict[str, Any]] = []

    # （phase, student_id）唯一性：课程允许每学生每阶段一个 active package
    student_groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for package in candidates:
        key = (package.get("submission_phase") or C.PHASE1, str(package.get("student_id") or ""))
        if key[1]:
            student_groups.setdefault(key, []).append(package)
    errors += _duplicates(student_groups, C.BATCH_STUDENT_ID_DUPLICATE,
                          "student_id")

    # （phase, task_id）唯一性
    task_groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for package in candidates:
        key = (package.get("submission_phase") or C.PHASE1, str(package.get("task_id") or ""))
        if key[1]:
            task_groups.setdefault(key, []).append(package)
    errors += _duplicates(task_groups, C.BATCH_TASK_ID_DUPLICATE, "task_id")

    # archive basename 唯一性（全局）
    name_groups: dict[str, list[dict[str, Any]]] = {}
    for package in candidates:
        name_groups.setdefault(package["archive"], []).append(package)
    errors += _duplicates(name_groups, C.BATCH_ARCHIVE_NAME_DUPLICATE, "archive")

    by_phase_counts: dict[str, int] = {}
    for package in candidates:
        phase = package.get("submission_phase") or C.PHASE1
        by_phase_counts[phase] = by_phase_counts.get(phase, 0) + 1

    return {
        "status": "PASS" if not errors else "FAIL",
        "validation_layer": C.LAYER_BATCH,
        "error_code": errors[0]["code"] if errors else None,
        "error_count": len(errors),
        "errors": errors,
        "by_phase": sorted(by_phase_counts.items()),
    }


# ---------------------------------------------------------------------------
# Batch 顶层
# ---------------------------------------------------------------------------


def validate_batch(input_dir: str | Path) -> dict[str, Any]:
    """Batch Validator 顶层（§24–§33）。

    第一遍：每个 *.zip 独立执行完整的 Server Intake + Single-Package
    Validation（不信任学生本地 PASS）；第二遍：只对 PASS Candidate 执行
    跨包唯一性检查。返回结构化 report（JSON 可直接落盘）。
    """

    archives = find_archives(input_dir)
    package_results = [validate_archive(a) for a in archives]
    summary = aggregate(package_results)
    candidates = [r for r in package_results if r["single_package_status"] == "PASS"]
    cross = validate_cross_package(candidates)

    candidates_ok = [p["archive"] for p in candidates]
    conflicts_by_archive: dict[str, list[dict[str, Any]]] = {}
    for conflict in cross["errors"]:
        for archive in conflict.get("packages", []):
            conflicts_by_archive.setdefault(archive, []).append(conflict)

    final_rows: list[dict[str, Any]] = []
    for package in package_results:
        if package["single_package_status"] != "PASS":
            package["batch_status"] = "NOT_RUN"
            package["final_status"] = "REJECT"
        elif package["archive"] in conflicts_by_archive:
            package["batch_status"] = "FAIL"
            package["final_status"] = "REJECT"
            package["error_code"] = conflicts_by_archive[package["archive"]][0]["code"]
            package["error_detail"] = conflicts_by_archive[package["archive"]][0]["detail"]
        else:
            package["batch_status"] = "PASS"
            package["final_status"] = "ACCEPTABLE"
        final_rows.append(package)

    # diagnostics：所有可解析包（含 FAIL 包）中观察到的重复，供助教人工排查
    # （§27：诊断信息与正式 reject 条件分离；reject 只看 PASS Candidate）
    diagnostics = _observed_duplicates(package_results)

    status = "PASS" if all(r["final_status"] == "ACCEPTABLE" for r in final_rows) else "FAIL"
    return {
        "schema_version": C.VALIDATION_REPORT_SCHEMA_VERSION,
        "validation_layer": C.LAYER_BATCH,
        "status": status,
        "validated_at": _now_iso(),
        "summary": summary,
        "candidate_count": len(candidates_ok),
        "error_count": cross["error_count"],
        "error_code": cross["error_code"],
        "errors": cross["errors"],
        "diagnostics": diagnostics,
        "packages": final_rows,
    }


def _observed_duplicates(package_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """对所有可解析包按身份字段观察重复（§27 诊断，不构成 reject）。"""

    groups: list[tuple[str, str, list[str]]] = []
    for label, key_of in (
        ("student_id",
         lambda p: ((p.get("submission_phase") or ""), str(p.get("student_id") or ""))),
        ("task_id",
         lambda p: ((p.get("submission_phase") or ""), str(p.get("task_id") or ""))),
        ("archive", lambda p: p["archive"]),
    ):
        seen: dict[Any, list[str]] = {}
        for package in package_results:
            key = key_of(package)
            value = key[1] if isinstance(key, tuple) else key
            if value:
                seen.setdefault(key, []).append(package["archive"])
        for key, archives in sorted(seen.items(), key=lambda kv: str(kv[0])):
            if len(archives) > 1:
                groups.append((label, str(key), archives))
    return [
        {
            "label": label,
            "value": value,
            "packages": archives,
            "detail": f"观察到的 {label} 重复（诊断信息，不作为 reject 条件）",
        }
        for label, value, archives in groups
    ]


def write_batch_json(report: dict[str, Any], output_dir: str | Path) -> Path:
    """把 Batch report 落盘为 batch_report.json。"""

    path = Path(output_dir) / BATCH_REPORT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path