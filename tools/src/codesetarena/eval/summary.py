"""评测汇总 —— 从磁盘真实产物生成 summary.csv + summary.json。

强制约束：

- 一条 Task × Agent × Model 组合对应表中一行，**包括失败与未执行的组合**；
- 数据只从实际生成的 ``result.json``（runtime 真相）与 ``eval_record.json``
  （评测层元数据）读取，绝不根据 CLI 返回码猜测、绝不使用内存残留值；
- 结果文件缺失 / 不可读时，对应行标记 ``result_read_error``，绝不静默忽略；
- ``result_path`` / ``log_path`` 只填写真实存在的文件路径（未执行为空串）；
- 计划组合数必须等于汇总行数（调用方 ``batch.run_batch`` 保证）。
"""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .outcome import (
    FINAL_OUTCOME_API_ERROR,
    FINAL_OUTCOME_CORRECT,
    FINAL_OUTCOME_RUNTIME_ERROR,
    FINAL_OUTCOME_TIMEOUT,
    FINAL_OUTCOME_TOKEN_BUDGET,
    FINAL_OUTCOME_UNEVALUATED,
    FINAL_OUTCOME_WRONG,
    summarize_outcome,
)

SUMMARY_SCHEMA = "datac-eval-summary-v1"
SUMMARY_CSV_FILENAME = "summary.csv"
SUMMARY_JSON_FILENAME = "summary.json"
SUMMARY_XLSX_FILENAME = "summary.xlsx"

SUMMARY_CSV_FIELDS = (
    "batch_id",
    "run_id",
    "task_id",
    "agent_id",
    "model",
    "execution_status",
    "final_outcome",
    "correct",
    "elapsed_seconds",
    "tokens_used",
    "token_budget",
    "timeout_seconds",
    "error_category",
    "error_message",
    "result_path",
    "log_path",
)

FINAL_OUTCOME_COUNT_KEYS = (
    FINAL_OUTCOME_CORRECT,
    FINAL_OUTCOME_WRONG,
    FINAL_OUTCOME_TIMEOUT,
    FINAL_OUTCOME_TOKEN_BUDGET,
    FINAL_OUTCOME_API_ERROR,
    FINAL_OUTCOME_RUNTIME_ERROR,
    FINAL_OUTCOME_UNEVALUATED,
)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _new_counts() -> dict[str, int]:
    return {key: 0 for key in FINAL_OUTCOME_COUNT_KEYS}


def combo_row(batch_id: str | None, combo: dict[str, Any]) -> dict[str, Any]:
    """一条计划组合 → 一行汇总记录（只读磁盘，不猜状态）。"""

    combo_dir = Path(combo.get("combo_dir") or "")
    record = _read_json(combo_dir / "eval_record.json")
    row = {
        "batch_id": batch_id or "",
        "run_id": "",
        "task_id": str(combo.get("task_id") or ""),
        "agent_id": str(combo.get("agent_id") or ""),
        "model": "",
        "execution_status": str(combo.get("status") or "not_executed"),
        "final_outcome": FINAL_OUTCOME_UNEVALUATED,
        "correct": "",
        "elapsed_seconds": "",
        "tokens_used": "",
        "token_budget": "",
        "timeout_seconds": "",
        "error_category": "",
        "error_message": "",
        "result_path": "",
        "log_path": "",
    }
    if record is None:
        row["error_category"] = "not_executed"
        row["error_message"] = "该组合未执行（批量中断或尚未轮到）；无 result.json"
        return row

    limits = record.get("limits") or {}
    row["model"] = str(record.get("model") or "")
    row["token_budget"] = limits.get("max_tokens") if limits.get("max_tokens") is not None else ""
    row["timeout_seconds"] = (
        limits.get("supervisor_timeout_seconds")
        if limits.get("supervisor_timeout_seconds") is not None
        else ""
    )
    row["log_path"] = str(record.get("log_path") or "")

    if record.get("status") != "executed":
        row["error_category"] = str(record.get("error_category") or "invalid_input")
        row["error_message"] = str(record.get("error_message") or "")
        return row

    result_path_text = str(record.get("result_path") or "")
    result = _read_json(Path(result_path_text)) if result_path_text else None
    if result is None:
        row["execution_status"] = "result_read_error"
        row["error_category"] = "result_read_error"
        row["error_message"] = f"result.json 缺失或不可读: {result_path_text}"
        return row

    final_outcome, error_category = summarize_outcome(result)
    row["execution_status"] = str(result.get("status") or "unknown")
    row["run_id"] = str(result.get("run_id") or record.get("run_id") or "")
    row["final_outcome"] = final_outcome
    row["correct"] = "" if result.get("correct") is None else str(bool(result.get("correct")))
    row["elapsed_seconds"] = (
        result.get("elapsed_seconds") if result.get("elapsed_seconds") is not None else ""
    )
    row["tokens_used"] = (
        result.get("total_tokens") if result.get("total_tokens") is not None else ""
    )
    row["error_category"] = error_category
    row["error_message"] = str(record.get("error_message") or "")
    if result_path_text and Path(result_path_text).is_file():
        row["result_path"] = result_path_text
    return row


def build_summary(
    *,
    batch_id: str | None,
    output_dir: Path,
    combos: list[dict[str, Any]],
    models: list[dict[str, Any]] | None = None,
    interrupted: bool = False,
    batch_config: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """全部计划组合 → (rows, summary dict)；行数恒等于计划组合数。"""

    rows = [combo_row(batch_id, combo) for combo in combos]
    counts = _new_counts()
    status_counts: dict[str, int] = {}
    tokens_total = 0
    wall_total = 0.0
    missing: list[str] = []
    for row, combo in zip(rows, combos):
        counts[row["final_outcome"]] = counts.get(row["final_outcome"], 0) + 1
        status_counts[row["execution_status"]] = status_counts.get(row["execution_status"], 0) + 1
        if isinstance(row["tokens_used"], (int, float)):
            tokens_total += int(row["tokens_used"])
        record = _read_json(Path(combo.get("combo_dir") or "") / "eval_record.json")
        if isinstance(record, dict) and isinstance(record.get("wall_seconds"), (int, float)):
            wall_total += float(record["wall_seconds"])
        if row["final_outcome"] in (FINAL_OUTCOME_UNEVALUATED,):
            missing.append(str(combo.get("combo_key") or ""))

    executed_total = sum(
        1
        for row in rows
        if row["execution_status"] not in ("not_executed", "input_error", "result_read_error")
    )
    input_errors = sum(1 for row in rows if row["execution_status"] == "input_error")
    result_read_errors = sum(1 for row in rows if row["execution_status"] == "result_read_error")
    not_executed = sum(1 for row in rows if row["execution_status"] == "not_executed")

    per_model: list[dict[str, Any]] = []
    for model_entry in models or []:
        label = str(model_entry.get("label") or "")
        model_rows = [
            row for row, combo in zip(rows, combos)
            if str(combo.get("model_label") or "") == label
        ]
        model_counts = _new_counts()
        for row in model_rows:
            model_counts[row["final_outcome"]] = model_counts.get(row["final_outcome"], 0) + 1
        per_model.append(
            {
                "model_label": label,
                "model": model_entry.get("model") or "",
                "config": model_entry.get("config") or "",
                "planned": len(model_rows),
                "executed": sum(
                    1
                    for row in model_rows
                    if row["execution_status"]
                    not in ("not_executed", "input_error", "result_read_error")
                ),
                "counts": model_counts,
            }
        )

    summary = {
        "schema": SUMMARY_SCHEMA,
        "batch_id": batch_id or "",
        "output_dir": str(output_dir),
        "batch_config": batch_config or "",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "interrupted": interrupted,
        "planned_total": len(rows),
        "executed_total": executed_total,
        "input_errors": input_errors,
        "result_read_errors": result_read_errors,
        "not_executed": not_executed,
        "complete": (
            executed_total == len(rows)
            and not_executed == 0
            and input_errors == 0
            and result_read_errors == 0
            and counts[FINAL_OUTCOME_UNEVALUATED] == 0
        ),
        "counts": counts,
        "counts_by_status": dict(sorted(status_counts.items())),
        "tokens_total": tokens_total,
        "wall_seconds_total": round(wall_total, 3),
        "models": sorted({row["model"] for row in rows if row["model"]}),
        "per_model": per_model,
        "missing": missing,
        "summary_csv": str(output_dir / SUMMARY_CSV_FILENAME),
        "summary_json": str(output_dir / SUMMARY_JSON_FILENAME),
        "combos": [
            {
                "combo_key": str(combo.get("combo_key") or ""),
                "model_label": str(combo.get("model_label") or ""),
                "task_id": row["task_id"],
                "agent_id": row["agent_id"],
                "status": row["execution_status"],
                "final_outcome": row["final_outcome"],
                "result_path": row["result_path"],
            }
            for row, combo in zip(rows, combos)
        ],
    }
    return rows, summary


def _write_summary_xlsx(output_dir: Path, rows: list[dict[str, Any]]) -> str:
    """可选导出 summary.xlsx（openpyxl 已是仓库依赖；缺失时降级跳过）。"""

    try:
        from openpyxl import Workbook
    except ImportError:
        return ""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Summary"
    sheet.append(list(SUMMARY_CSV_FIELDS))
    for row in rows:
        sheet.append([row.get(field, "") for field in SUMMARY_CSV_FIELDS])
    xlsx_path = output_dir / SUMMARY_XLSX_FILENAME
    workbook.save(xlsx_path)
    return str(xlsx_path)


def write_summary(
    output_dir: Path, rows: list[dict[str, Any]], summary: dict[str, Any]
) -> tuple[Path, Path]:
    """写出 summary.csv（UTF-8 with BOM，Excel 可直接打开）+ summary.json。

    附加产物：summary.xlsx（openpyxl 可导入时；不是验收必需项）。
    """

    csv_path = output_dir / SUMMARY_CSV_FILENAME
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=SUMMARY_CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    summary["summary_xlsx"] = _write_summary_xlsx(output_dir, rows)
    json_path = output_dir / SUMMARY_JSON_FILENAME
    json_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return csv_path, json_path
