"""Batch Excel Report —— 助教最终报告（v2 §32）。

生成 ``validation_report.xlsx``，三个 Sheet：

1. Summary       —— 整批汇总（总数 / 单包通过 / Candidate / 最终状态 / 错误码分布）；
2. PackageResults—— 主表：Package | Student ID | Task ID | Phase | Single Package |
                     Batch | Final | Error Code | Error Detail（v2 §32 表）；
3. BatchConflicts — 跨包冲突明细：Rule ID | Field | Value | Packages。

openpyxl 缺失时降级写 CSV（summary.csv / package_results.csv / conflicts.csv），
报告路径仍返回（机器可解析）。JSON 报告（batch_report.json）永远是权威产物，
Excel 只是给人看的视图。
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

BATCH_XLSX = "validation_report.xlsx"

SUMMARY_HEADERS = ("metric", "value")
PACKAGE_HEADERS = (
    "Package", "Student ID", "Task ID", "Phase", "Single Package",
    "Batch", "Final", "Error Code", "Error Detail",
)
CONFLICT_HEADERS = ("Rule ID", "Field", "Value", "Packages")


def _excel_value(value: Any) -> str:
    """openpyxl 不能直接写 dict/list，先序列化为人可读字符串。"""

    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def _sheet_rows(report: dict[str, Any]) -> tuple[list[tuple], list[list[Any]], list[list[Any]]]:
    """把 report 展开为三个 Sheet 的原始行。"""

    summary = report.get("summary") or {}
    status_counts: dict[str, int] = {}
    for package in report.get("packages") or []:
        final = package.get("final_status", "REJECT")
        status_counts[final] = status_counts.get(final, 0) + 1
    error_codes = sorted({p.get("error_code") or "" for p in report.get("packages") or []
                          if p.get("error_code")})
    summary_rows = [
        ("status", report.get("status", "FAIL")),
        ("validated_at", report.get("validated_at", "")),
        ("package_count", summary.get("package_count", 0)),
        ("single_pass_count", summary.get("single_pass_count", 0)),
        ("single_fail_count", summary.get("single_fail_count", 0)),
        ("candidate_count", summary.get("candidate_count", 0)),
        ("by_phase", _excel_value(summary.get("by_phase", {}))),
        ("final_status_counts", _excel_value(status_counts)),
        ("error_codes", _excel_value(error_codes)),
    ]

    package_rows: list[list[Any]] = []
    for package in report.get("packages") or []:
        package_rows.append(
            [
                package.get("archive", ""),
                package.get("student_id", ""),
                package.get("task_id", ""),
                package.get("submission_phase", ""),
                package.get("single_package_status", "FAIL"),
                package.get("batch_status", "NOT_RUN"),
                "PASS" if package.get("final_status") == "ACCEPTABLE" else "FAIL",
                package.get("error_code") or "",
                package.get("error_detail", ""),
            ]
        )

    conflict_rows: list[list[Any]] = []
    for conflict in report.get("errors") or []:
        conflict_rows.append(
            [
                conflict.get("code", ""),
                conflict.get("field", ""),
                conflict.get("value", ""),
                ", ".join(conflict.get("packages") or []),
            ]
        )
    if not conflict_rows:
        for diag in report.get("diagnostics") or []:
            conflict_rows.append(
                ["(diagnostic)", diag.get("label", ""), diag.get("value", ""),
                 ", ".join(diag.get("packages") or [])]
            )
    return summary_rows, package_rows, conflict_rows


def _write_csv_fallback(
    output_dir: Path,
    summary_rows: list[tuple],
    package_rows: list[list[Any]],
    conflict_rows: list[list[Any]],
) -> None:
    """openpyxl 缺失时的 CSV 降级（文件名带后缀，便于与 xlsx 区分）。"""

    def dump(name: str, header: tuple, rows: list) -> None:
        path = output_dir / name
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            writer.writerows(rows)

    dump("summary.csv", SUMMARY_HEADERS, summary_rows)
    dump("package_results.csv", PACKAGE_HEADERS, package_rows)
    dump("batch_conflicts.csv", CONFLICT_HEADERS, conflict_rows)


def write_batch_xlsx(report: dict[str, Any], output_dir: str | Path) -> Path:
    """把 Batch report 写为 validation_report.xlsx（openpyxl 缺失时降级 CSV）。"""

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_rows, package_rows, conflict_rows = _sheet_rows(report)

    try:
        from openpyxl import Workbook
    except ImportError:
        _write_csv_fallback(output_dir, summary_rows, package_rows, conflict_rows)
        return output_dir / BATCH_XLSX  # 返回名义路径；实际产物为 CSV

    workbook = Workbook()
    summary_sheet = workbook.active
    summary_sheet.title = "Summary"
    summary_sheet.append(SUMMARY_HEADERS)
    for row in summary_rows:
        summary_sheet.append(row)

    package_sheet = workbook.create_sheet("PackageResults")
    package_sheet.append(PACKAGE_HEADERS)
    for row in package_rows:
        package_sheet.append(row)

    conflict_sheet = workbook.create_sheet("BatchConflicts")
    conflict_sheet.append(CONFLICT_HEADERS)
    for row in conflict_rows:
        conflict_sheet.append(row)

    for sheet in (summary_sheet, package_sheet, conflict_sheet):
        for column in sheet.columns:
            width = max(len(str(cell.value)) if cell.value is not None else 0 for cell in column)
            sheet.column_dimensions[column[0].column_letter].width = min(max(width + 2, 10), 60)

    path = output_dir / BATCH_XLSX
    workbook.save(path)
    return path