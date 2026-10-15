"""Validation Result 数据模型（v2 §9/§10）—— build / validate / batch-validate 共用。

结果永远是结构化 dict（当前工程风格），但字段名与结构是**稳定接口**：
机器消费（batch 汇总 / Excel / CLI ``--json-output``）只依赖字段名，
绝不解析终端文本。错误码（rule_id）也是稳定接口，message 可以以后优化。

``machine_result`` 把内部校验结果（``{code, location, detail}``）规范化为
v2 的 ValidationResult / Issue 形态：

    {
        "scope": "single_package",
        "status": "PASS" | "FAIL",
        "package_name": "...",
        "student_id": "...",
        "task_id": "...",
        "agent_id": "...",
        "phase": "...",
        "validation_layer": "...",
        "error_code": "...",
        "errors": [{"rule_id", "path", "field", "message"}, ...],
        "warnings": [...],
    }
"""
from __future__ import annotations

from typing import Any, Mapping


def normalize_issue(error: Mapping[str, Any]) -> dict[str, Any]:
    """把内部错误条目（``{code, location, detail}``）规范化为统一 Issue。"""

    return {
        "rule_id": str(error.get("code") or ""),
        "path": str(error.get("location") or ""),
        "field": str(error.get("field") or ""),
        "message": str(error.get("message") or error.get("detail") or ""),
    }


def machine_result(
    result: Mapping[str, Any],
    package_name: str | None = None,
) -> dict[str, Any]:
    """统一 Single-Package ValidationResult（v2 §9）。"""

    checks = result.get("checks") or {}
    return {
        "scope": "single_package",
        "status": result.get("status", "FAIL"),
        "package_name": (
            package_name if package_name is not None
            else result.get("archive", result.get("package_name", ""))
        ),
        "student_id": checks.get("student_id") or result.get("student_id") or "",
        "task_id": checks.get("task_id") or result.get("task_id") or "",
        "agent_id": checks.get("agent_id") or result.get("agent_id") or "",
        "phase": checks.get("submission_phase") or result.get("submission_phase") or "",
        "validation_layer": result.get("validation_layer"),
        "error_code": result.get("error_code"),
        "errors": [normalize_issue(error) for error in result.get("errors", [])],
        "warnings": [normalize_issue(warning) for warning in result.get("warnings", [])],
    }


__all__ = ["machine_result", "normalize_issue"]