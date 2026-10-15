"""Reporting —— batch-validate / ingest 的机器可读落盘（v2 §16/§22/§27）。

机器结果（正式接口）：

- ``summary.json``         整批汇总（总数 / 单包通过 / candidate / 最终状态分布）
- ``package_results.jsonl``每包一行（PackageResults 主表，§16 字段）
- ``batch_conflicts.json`` 跨包冲突 + 诊断（§16 BatchConflicts）
- ``batch_report.json``    完整 report（back-compat，结构性超集）
- ``validation_report.xlsx``人读视图（Summary / PackageResults / BatchConflicts）

所有写盘原子化（temp + ``os.replace``），避免读一半写一半。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def write_json(path: str | Path, payload: Any) -> Path:
    """原子写 JSON（先写临时文件再 rename）。"""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def write_jsonl(path: str | Path, rows: list[Any]) -> Path:
    """原子写 JSONL（每行一个 JSON 对象）。"""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


PACKAGE_RESULT_FIELDS = (
    "package", "student_id", "source_task_id", "task_id", "source_agent_id", "agent_id",
    "phase", "single_package_status", "batch_status", "final_status",
    "error_count", "warning_count", "error_codes", "error_details",
)


def _package_result_row(package: dict[str, Any]) -> dict[str, Any]:
    """Batch 主表行（§16 PackageResults 至少字段）。"""

    errors = package.get("errors") or []
    warnings = package.get("warnings") or []
    return {
        "package": package.get("archive", ""),
        "student_id": package.get("student_id", ""),
        "source_task_id": package.get("task_id", ""),
        "task_id": package.get("canonical_task_id", ""),
        "source_agent_id": package.get("source_agent_id", ""),
        "agent_id": package.get("canonical_agent_id", ""),
        "phase": package.get("submission_phase", ""),
        "single_package_status": package.get("single_package_status", "FAIL"),
        "batch_status": package.get("batch_status", "NOT_RUN"),
        "final_status": package.get("final_status", "REJECT"),
        "error_count": len(errors),
        "warning_count": len(warnings),
        "error_codes": sorted({e.get("code") or e.get("rule_id") or "" for e in errors}),
        "error_details": [e.get("detail") or e.get("message") or "" for e in errors],
    }


def write_validation_report(report: dict[str, Any], output_dir: str | Path) -> dict[str, Path]:
    """batch-validate 的机器落地：summary.json / package_results.jsonl /
    batch_conflicts.json / batch_report.json / validation_report.xlsx。

    返回 {name: path}。Excel 通过 ``batch/excel_report.write_batch_xlsx`` 生成。
    """

    from .batch.excel_report import write_batch_xlsx

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    summary = dict(report.get("summary") or {})
    summary.update({
        "status": report.get("status"),
        "validated_at": report.get("validated_at"),
        "candidate_count": report.get("candidate_count"),
        "error_count": report.get("error_count"),
        "error_code": report.get("error_code"),
    })
    paths = {
        "summary": write_json(output_dir / "summary.json", summary),
        "package_results": write_jsonl(
            output_dir / "package_results.jsonl",
            [_package_result_row(p) for p in report.get("packages") or []],
        ),
        "batch_conflicts": write_json(
            output_dir / "batch_conflicts.json",
            {
                "errors": report.get("errors") or [],
                "diagnostics": report.get("diagnostics") or [],
            },
        ),
        "batch_report": write_json(output_dir / "batch_report.json", report),
        "xlsx": write_batch_xlsx(report, output_dir),
    }
    return paths


__all__ = ["write_json", "write_jsonl", "write_validation_report"]