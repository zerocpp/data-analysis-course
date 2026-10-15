"""Phase 1 Batch Validator —— 严格只读。

Validator 是唯一裁决：PASS / WARNING / ERROR。绝对不修改任何文件：
不自动改 task_id / student_id / hypothesized_failure_mode，不重写 task.json，
不自动修复两个学生之间的冲突，不自动选择重复 active submission。

检查分两层：

1. 单包检查（MALFORMED_JSON / MISSING_FIELD / INVALID_FM / STUDENT_ID_MISMATCH）
2. cohort 全局检查（DUPLICATE_TASK_ID / DUPLICATE_ACTIVE_STUDENT_ID /
   STUDENT_ID_MISMATCH 跨包身份一致性）

任一 ERROR 都必须使 pipeline 立即停止（Coverage / Assignment 不生成），
由调用方依据 report["status"] 决定退出码。
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from ..constants import HYPOTHESIZED_FM_CODES, HYPOTHESIZED_FM_ORDER

MALFORMED_JSON = "MALFORMED_JSON"
MISSING_FIELD = "MISSING_FIELD"
INVALID_FM = "INVALID_FM"
DUPLICATE_TASK_ID = "DUPLICATE_TASK_ID"
DUPLICATE_ACTIVE_STUDENT_ID = "DUPLICATE_ACTIVE_STUDENT_ID"
STUDENT_ID_MISMATCH = "STUDENT_ID_MISMATCH"

REQUIRED_TASK_FIELDS = ("task_id", "student_id", "hypothesized_failure_mode", "task_statement")

# 目录名携带学号的形态：`272401`、`student_272401`、`system_272401`、`272401_something`
_DIR_ID_PATTERNS = (
    re.compile(r"^(?:student_|system_)?(\d{4,})(?:_|$)"),
    re.compile(r"^(\d{4,})$"),
)


def scan_canonical(canonical_root: str | Path) -> list[Path]:
    """扫描 canonical 根下含 task.json 的子目录（仅一层）。"""

    root = Path(canonical_root)
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir() and (p / "task.json").is_file())


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _dir_claimed_student(dirname: str) -> str | None:
    for pattern in _DIR_ID_PATTERNS:
        match = pattern.match(dirname)
        if match:
            return match.group(1)
    return None


def _check_identity(pkg: dict[str, Any], task: dict[str, Any], errors: list[dict[str, Any]]) -> None:
    """包内身份一致性：task.json.student_id 与目录名 / metadata.json 相互印证。"""

    student_id = str(task.get("student_id") or "").strip()
    dirname = Path(pkg["dir"]).name
    claimed = _dir_claimed_student(dirname)
    if claimed and claimed != student_id:
        errors.append(
            {
                "code": STUDENT_ID_MISMATCH,
                "package": str(pkg["dir"]),
                "detail": (
                    f"task.json student_id={student_id!r} 与目录名 {dirname!r} 不一致"
                    f"（目录声明 {claimed!r}）"
                ),
            }
        )
    metadata_path = Path(pkg["dir"]) / "metadata.json"
    if metadata_path.is_file():
        try:
            meta = json.loads(metadata_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            meta = None
        if isinstance(meta, dict):
            meta_task_id = str(meta.get("task_id") or "").strip()
            task_id = str(task.get("task_id") or "").strip()
            if meta_task_id and meta_task_id != task_id:
                errors.append(
                    {
                        "code": STUDENT_ID_MISMATCH,
                        "package": str(pkg["dir"]),
                        "detail": (
                            f"metadata.json task_id={meta_task_id!r} 与 task.json "
                            f"task_id={task_id!r} 不一致"
                        ),
                    }
                )
            meta_workspace = str(meta.get("workspace") or "").strip()
            if meta_workspace and student_id:
                claimed_ws = _dir_claimed_student(meta_workspace)
                if claimed_ws and claimed_ws != student_id:
                    errors.append(
                        {
                            "code": STUDENT_ID_MISMATCH,
                            "package": str(pkg["dir"]),
                            "detail": (
                                f"metadata.json workspace={meta_workspace!r} 声明的学号"
                                f" {claimed_ws!r} 与 task.json student_id={student_id!r} 不一致"
                            ),
                        }
                    )


def validate_batch(package_dirs: list[Path]) -> dict[str, Any]:
    """对当前 active 提交集合执行「单包 + cohort 全局」校验。返回只读报告。"""

    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    packages: list[dict[str, Any]] = []

    for pkg_dir in package_dirs:
        pkg: dict[str, Any] = {"dir": str(pkg_dir), "task_id": "", "student_id": "",
                               "hypothesized_failure_mode": "", "sha256": "",
                               "errors": [], "warnings": []}
        task_path = pkg_dir / "task.json"
        if not task_path.is_file():
            pkg["errors"].append(MALFORMED_JSON)
            errors.append(
                {"code": MALFORMED_JSON, "package": str(pkg_dir),
                 "detail": "缺少 task.json"}
            )
            packages.append(pkg)
            continue

        try:
            task = json.loads(task_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            pkg["errors"].append(MALFORMED_JSON)
            errors.append(
                {"code": MALFORMED_JSON, "package": str(pkg_dir),
                 "detail": f"task.json 不是合法 JSON: {exc}"}
            )
            pkg["sha256"] = _sha256(task_path)
            packages.append(pkg)
            continue
        if not isinstance(task, dict):
            pkg["errors"].append(MALFORMED_JSON)
            errors.append(
                {"code": MALFORMED_JSON, "package": str(pkg_dir),
                 "detail": "task.json 必须是 JSON 对象"}
            )
            pkg["sha256"] = _sha256(task_path)
            packages.append(pkg)
            continue

        pkg["sha256"] = _sha256(task_path)
        pkg["task_id"] = str(task.get("task_id") or "").strip()
        pkg["student_id"] = str(task.get("student_id") or "").strip()
        fm = str(task.get("hypothesized_failure_mode") or "").strip()
        pkg["hypothesized_failure_mode"] = fm

        for field in REQUIRED_TASK_FIELDS:
            value = str(task.get(field) or "").strip()
            if not value:
                pkg["errors"].append(MISSING_FIELD)
                errors.append(
                    {"code": MISSING_FIELD, "package": str(pkg_dir),
                     "detail": f"task.json 缺少必填字段 {field}"}
                )

        if fm and fm not in HYPOTHESIZED_FM_CODES:
            pkg["errors"].append(INVALID_FM)
            errors.append(
                {"code": INVALID_FM, "package": str(pkg_dir),
                 "detail": (
                     f"hypothesized_failure_mode={fm!r} 不是 Phase 1 合法枚举"
                     f"（只能取 " + "/".join(HYPOTHESIZED_FM_ORDER) + "）"
                 )}
            )

        _check_identity(pkg, task, errors)

        packages.append(pkg)

    # ---- cohort 全局检查：task_id 全局唯一 ----
    by_task_id: dict[str, list[dict[str, Any]]] = {}
    for pkg in packages:
        if pkg["task_id"]:
            by_task_id.setdefault(pkg["task_id"], []).append(pkg)
    for task_id, members in sorted(by_task_id.items()):
        if len(members) > 1:
            errors.append(
                {
                    "code": DUPLICATE_TASK_ID,
                    "task_id": task_id,
                    "detail": "多个 active submission 使用相同 task_id",
                    "students": [
                        {"student_id": p["student_id"], "package": p["dir"]} for p in members
                    ],
                }
            )
            for pkg in members:
                pkg["errors"].append(DUPLICATE_TASK_ID)

    # ---- cohort 全局检查：active student_id 唯一（不自动选「最新一个」）----
    by_student: dict[str, list[dict[str, Any]]] = {}
    for pkg in packages:
        if pkg["student_id"]:
            by_student.setdefault(pkg["student_id"], []).append(pkg)
    for student_id, members in sorted(by_student.items()):
        if len(members) > 1:
            errors.append(
                {
                    "code": DUPLICATE_ACTIVE_STUDENT_ID,
                    "student_id": student_id,
                    "detail": "active pool 中同一 student_id 存在多个提交，需人工裁决",
                    "packages": [p["dir"] for p in members],
                }
            )
            for pkg in members:
                pkg["errors"].append(DUPLICATE_ACTIVE_STUDENT_ID)

    status = "ERROR" if errors else "PASS"
    return {
        "status": status,
        "validated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "package_count": len(packages),
        "error_count": len(errors),
        "warning_count": len(warnings),
        "error_codes": sorted({e["code"] for e in errors}),
        "errors": errors,
        "warnings": warnings,
        "packages": packages,
    }


def write_validation_report(report: dict[str, Any], validation_dir: str | Path) -> Path:
    """把报告写入 validation/validation_report.json。"""

    path = Path(validation_dir) / "validation_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def validated_packages(report: dict[str, Any]) -> list[dict[str, Any]]:
    """报告 PASS 时返回全部包元数据（调用方必须先确认 status == PASS）。"""

    return [dict(p) for p in report["packages"]]