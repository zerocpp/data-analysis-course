"""Phase F 报告：机器可读 JSON/JSONL（权威）+ 管理端 Excel（视图）。

后续系统只解析 JSON/JSONL；Excel 缺失 openpyxl 时降级 CSV。
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Sequence


def _excel_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def _write_sheet(wb, title: str, headers: Sequence[str], rows: list[list[Any]]) -> None:
    ws = wb.create_sheet(title)
    ws.append(list(headers))
    for row in rows:
        ws.append([_excel_value(cell) for cell in row])


def write_workbook(
    path: str | Path,
    sheets: Sequence[tuple[str, Sequence[str], list[list[Any]]]],
) -> Path:
    """sheets: [(title, headers, rows)]；openpyxl 缺失时降级为同名 CSV。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from openpyxl import Workbook
    except ImportError:  # pragma: no cover - 环境无 openpyxl
        for title, headers, rows in sheets:
            csv_path = path.with_name(f"{path.stem}_{title.lower()}.csv")
            with csv_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(list(headers))
                writer.writerows(rows)
        return path

    wb = Workbook()
    wb.remove(wb.active)
    for title, headers, rows in sheets:
        _write_sheet(wb, title, headers, rows)
    wb.save(path)
    return path


# ---------------------------------------------------------------------------
# Gate A：phase1_final_validation_report.xlsx（§14）
# ---------------------------------------------------------------------------


def write_phase1_report(
    path: str | Path,
    *,
    summary: dict[str, Any],
    acceptance_rows: list[dict[str, Any]],
    cross: dict[str, Any],
    provenance: dict[str, Any],
    identity: list[dict[str, Any]],
    secret_hits: list[dict[str, Any]],
    inventory: list[dict[str, Any]],
) -> Path:
    cross_rows = [
        [c.get("code", ""), c.get("field", ""), c.get("value", ""),
         json.dumps(c.get("packages", []), ensure_ascii=False), c.get("detail", "")]
        for c in cross.get("errors", [])
    ] or [["-", "-", "-", "[]", "no cross-package conflict"]]

    identity_rows = [
        [h.get("path", ""), h.get("token", ""), h.get("surface", "")]
        for h in identity
    ] or [["-", "-", "0 identity leaks"]]

    secret_rows = [
        [h.get("path", ""), h.get("kind", ""), h.get("snippet", "")]
        for h in secret_hits
    ] or [["-", "-", "0 secret leaks"]]

    hash_rows = [
        [row["archive_filename"], row["sha256"], row["size_bytes"], row["mtime_epoch"]]
        for row in inventory
    ]

    provenance_rows = [
        [key, _excel_value(provenance.get(key))]
        for key in ("registry_rows", "accepted", "canonical_tasks", "canonical_agents",
                    "anonymous_task_mappings", "status")
    ]

    return write_workbook(path, [
        ("Summary", ["metric", "value"], [[k, _excel_value(v)] for k, v in summary.items()]),
        ("PackageResults", [
            "Archive", "Student", "Task", "Agent", "Phase", "Single", "Batch", "Final",
            "Error Code", "Submission ID", "Canonical Task", "Anonymous Task",
        ], [[
            r["archive_filename"], r["student_id"], r["task_id"], r["agent_id"], r["phase"],
            r["single_status"], r["batch_status"], r["final_status"],
            r.get("error_code") or "", r["submission_id"], r["canonical_task_id"],
            r["anonymous_task_id"],
        ] for r in acceptance_rows]),
        ("CrossPackageChecks", ["Code", "Field", "Value", "Packages", "Detail"], cross_rows),
        ("IdentityConsistency", ["Location", "Token", "Surface"], identity_rows),
        ("SecretScan", ["Path", "Kind", "Snippet"], secret_rows),
        ("ArchiveHashes", ["Archive", "SHA256", "SizeBytes", "Mtime"], hash_rows),
        ("CanonicalProvenance", ["Metric", "Value"], provenance_rows),
    ])


# ---------------------------------------------------------------------------
# Gate B：phase2_release_candidate_report.xlsx（§65–§70）
# ---------------------------------------------------------------------------


def write_phase2_report(
    path: str | Path,
    *,
    summary: dict[str, Any],
    packages: list[dict[str, Any]],
    exposure_rows: list[dict[str, Any]],
    reconstruction: dict[str, Any],
    leakage: dict[str, list[dict[str, Any]]],
    archives: list[dict[str, Any]],
    gates: list[dict[str, Any]],
) -> Path:
    package_rows = [[
        p.get("release_candidate_id", ""),
        p.get("reviewer_student_id", ""),
        p.get("package_filename", ""),
        p.get("sha256", ""),
        p.get("task_count", 0),
        json.dumps(p.get("anonymous_task_ids", []), ensure_ascii=False),
        p.get("validation_status", ""),
        p.get("leak_status", ""),
    ] for p in packages]

    exposure_sheet = [[
        row.get("canonical_task_id", ""),
        row.get("anonymous_task_id", ""),
        row.get("owner_student_id", ""),
        row.get("reviewer_count", 0),
    ] for row in exposure_rows]

    reconstruction_rows = [
        ["expected_matrix_sha256", reconstruction.get("expected_matrix_sha256", "")],
        ["reconstructed_matrix_sha256", reconstruction.get("reconstructed_matrix_sha256", "")],
        ["matrix_shape_expected", reconstruction.get("matrix_shape_expected", "")],
        ["matrix_shape_reconstructed", reconstruction.get("matrix_shape_reconstructed", "")],
        ["expected_total", reconstruction.get("expected_total", "")],
        ["reconstructed_total", reconstruction.get("reconstructed_total", "")],
        ["difference_count", reconstruction.get("difference_count", "")],
        ["bit_exact", reconstruction.get("bit_exact", "")],
        ["status", reconstruction.get("status", "")],
    ]

    identity_rows = [[
        h.get("archive", h.get("path", "")), h.get("member", ""), h.get("token", ""),
        h.get("surface", ""),
    ] for h in leakage.get("identity", [])] or [["-", "-", "-", "0 identity leaks"]]
    private_rows = [[
        h.get("archive", h.get("path", "")), h.get("member", ""), h.get("token", ""),
    ] for h in leakage.get("private_content", [])] or [["-", "-", "0 private content leaks"]]
    secret_sheet = [[
        h.get("archive", h.get("path", "")), h.get("member", ""), h.get("kind", ""),
        h.get("snippet", ""),
    ] for h in leakage.get("secret", [])] + [[
        h.get("archive", h.get("path", "")), h.get("member", ""), h.get("kind", ""),
        h.get("snippet", ""),
    ] for h in leakage.get("local_path", [])] or [["-", "-", "-", "0 secret / local path leaks"]]

    archive_rows = [[
        a.get("package_filename", ""), a.get("sha256", ""), a.get("size_bytes", ""),
        a.get("task_count", ""),
    ] for a in archives]

    gate_rows = [[g.get("gate", ""), g.get("name", ""), g.get("status", ""),
                  g.get("detail", "")] for g in gates]

    return write_workbook(path, [
        ("Summary", ["metric", "value"], [[k, _excel_value(v)] for k, v in summary.items()]),
        ("ReviewerPackages", [
            "RC ID", "Reviewer Student", "Package", "SHA256", "Task Count",
            "Anonymous Tasks", "Validation", "Leak",
        ], package_rows),
        ("TaskExposure", [
            "Canonical Task", "Anonymous Task", "Owner Student", "Reviewer Count",
        ], exposure_sheet),
        ("AssignmentReconstruction", ["Metric", "Value"], reconstruction_rows),
        ("IdentityLeakage", ["Archive", "Member", "Token", "Surface"], identity_rows),
        ("PrivateContentLeakage", ["Archive", "Member", "Token"], private_rows),
        ("SecretLeakage", ["Archive", "Member", "Kind", "Snippet"], secret_sheet),
        ("ArchiveIntegrity", ["Package", "SHA256", "Size", "Tasks"], archive_rows),
        ("HashManifest", ["Package", "SHA256", "Size", "Tasks"], archive_rows),
        ("ReleaseGate", ["Gate", "Name", "Status", "Detail"], gate_rows),
    ])
