"""Phase Extension Validation + Dispatch —— Phase 的「新增内容」唯一校验点。

硬规则：

1. 任何 Phase 都先跑 ``validate_base_submission``（公共规则只维护一份）；
2. Phase 专属校验只检查该 Phase 在 Base 之上「新增」的内容，绝不复制
   task / agent / identity / FM 等公共检查；
3. Dispatch 依据提交声明的 ``submission_phase`` 选择扩展校验器；
4. ``archive_name`` 提供时追加 Package Name ↔ manifest 一致性校验
   （v2 §8/§10：archive filename 也是 Single-Package Validation 的一部分，
   Student Build 与 Server 用完全相同的检查与错误码）。

Phase 1 = Base（作者任务契约：task_statement + hypothesized_failure_mode 必填）。
Phase 2 = Base + assignment.json + 实验报告.docx + review-comments.json
（v10.1.14：Final Report 与旧 reviews/*.json 多文件协议已删除，唯一审稿记录
协议见 submission/review_comments.py）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import contract as C
from .base_validation import validate_base_submission
from .identity import IdentityError, parse_archive_name
from .review_comments import validate_review_comments


def _fail(code: str, location: str, detail: str) -> dict[str, Any]:
    return {
        "code": code,
        "location": location,
        "detail": detail,
    }


def _extract_result(
    layer: str,
    errors: list[dict[str, Any]],
    checks: dict[str, Any] | None = None,
    warnings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": C.SCHEMA_VERSION,
        "status": "FAIL" if errors else "PASS",
        "validation_layer": layer,
        "error_code": errors[0]["code"] if errors else None,
        "error_count": len(errors),
        "errors": errors,
        "warnings": warnings or [],
        "checks": checks or {},
    }


# ---------------------------------------------------------------------------
# Phase 1 Extension
# ---------------------------------------------------------------------------


def validate_phase1_extension(root: Path) -> dict[str, Any]:
    """Phase 1 新增契约：作者任务必填 task_statement + hypothesized_failure_mode。

    这两个字段属于 Student-authored（服务器只校验存在性与 taxonomy 合法性，
    绝不替学生重新生成 / 重新分类）。
    """

    errors: list[dict[str, Any]] = []
    task_path = root / C.TASK_JSON
    if task_path.is_file():
        try:
            task = json.loads(task_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            task = None
        if isinstance(task, dict):
            if not str(task.get("task_statement") or "").strip():
                errors.append(_fail(C.MISSING_TASK_STATEMENT, C.TASK_JSON,
                                    "Phase 1 作者任务缺少 task_statement"))
            if not str(task.get("hypothesized_failure_mode") or "").strip():
                errors.append(_fail(C.MISSING_HYPOTHESIZED_FM, C.TASK_JSON,
                                    "Phase 1 作者任务缺少 hypothesized_failure_mode"))
    return _extract_result(C.LAYER_P1_EXTENSION, errors)


# ---------------------------------------------------------------------------
# Phase 2 Extension
# ---------------------------------------------------------------------------


def validate_phase2_extension(root: Path) -> dict[str, Any]:
    """Phase 2 新增契约（v10.1.14）：assignment.json + 实验报告.docx +
    review-comments.json（唯一审稿记录）。

    Final Report（report/final_report.md）与旧 reviews/*.json 多文件协议已删除，
    不再校验也不再接受。只针对新增内容做检查；Base 内容已由
    validate_base_submission 校验过，这里绝不重复。
    """

    import zipfile

    errors: list[dict[str, Any]] = []

    # ---- assignment.json：分配权威（审稿一致性的对照源；数量不写死）----
    assignment_path = root / C.PHASE2_ASSIGNMENT
    assigned: list[str] = []
    assignment_ok = False
    if not assignment_path.is_file():
        errors.append(_fail(C.MISSING_ASSIGNMENT, C.PHASE2_ASSIGNMENT,
                            "Phase 2 缺少 assignment.json（匿名 Task 分配记录）"))
    else:
        try:
            payload = json.loads(assignment_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            payload = None
            errors.append(_fail(C.INVALID_ASSIGNMENT, C.PHASE2_ASSIGNMENT,
                                f"assignment.json 不是合法 JSON: {exc}"))
        if isinstance(payload, dict):
            raw = payload.get("anonymous_tasks")
            if (
                isinstance(raw, list)
                and raw
                and all(isinstance(t, str) and t.strip() for t in raw)
                and len(set(raw)) == len(raw)
            ):
                assigned = [t.strip() for t in raw]
                assignment_ok = True
            else:
                errors.append(_fail(C.INVALID_ASSIGNMENT, C.PHASE2_ASSIGNMENT,
                                    "assignment.anonymous_tasks 必须是非空且不重复的"
                                    "字符串列表"))
        elif payload is not None:
            errors.append(_fail(C.INVALID_ASSIGNMENT, C.PHASE2_ASSIGNMENT,
                                "assignment.json 必须是 JSON 对象"))

    # ---- 实验报告.docx：存在 + 非 0 字节 + 能正常打开（合法 docx 容器）----
    report_path = root / C.PHASE2_EXPERIMENT_REPORT
    if not report_path.is_file():
        errors.append(_fail(C.MISSING_EXPERIMENT_REPORT, C.PHASE2_EXPERIMENT_REPORT,
                            "Phase 2 缺少实验报告.docx（基于课程 Word 模板填写）"))
    else:
        broken = False
        detail = ""
        if report_path.stat().st_size == 0:
            broken = True
            detail = "实验报告.docx 是 0 字节"
        else:
            try:
                with zipfile.ZipFile(report_path) as bundle:
                    if "word/document.xml" not in bundle.namelist():
                        broken = True
                        detail = "实验报告.docx 不是可打开的 docx（缺少 word/document.xml）"
            except (zipfile.BadZipFile, OSError):
                broken = True
                detail = "实验报告.docx 无法作为 docx 打开"
        if broken:
            errors.append(_fail(C.INVALID_EXPERIMENT_REPORT, C.PHASE2_EXPERIMENT_REPORT,
                                detail))

    # ---- review-comments.json：唯一审稿记录（与 Assignment 完全一致）----
    if assignment_ok:
        review_result = validate_review_comments(root, assigned)
        errors.extend(review_result["errors"])

    return _extract_result(C.LAYER_P2_EXTENSION, errors)


# ---------------------------------------------------------------------------
# Package Name ↔ manifest 一致性（Single-Package Validation 的一部分）
# ---------------------------------------------------------------------------


def _validate_package_name(root: Path, archive_name: str) -> list[dict[str, Any]]:
    """archive filename ↔ manifest 一致性检查（v2 §8 / §10）。

    archive filename 是 Submission Contract 的组成部分：Student Build 在正式
    打包前用最终目标文件名检查，Server 收到后用实际上传文件名重新检查，
    双方必须得到完全相同的错误码。复用 identity 层稳定词汇
    （INVALID_ARCHIVE_NAME / SUBMISSION_PHASE_MISMATCH / STUDENT_ID_MISMATCH），
    绝不另造一套错误码。manifest 缺失 / 不可解析时跳过（由 base 层负责报错）。
    """

    errors: list[dict[str, Any]] = []
    try:
        outer = parse_archive_name(archive_name)
    except IdentityError as exc:
        errors.append(_fail(exc.code, "archive-filename", exc.detail))
        return errors

    manifest_path = root / C.MANIFEST
    if not manifest_path.is_file():
        return errors
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return errors
    if not isinstance(manifest, dict):
        return errors

    inner_phase = str(manifest.get("submission_phase") or "").strip()
    if inner_phase and inner_phase != outer["phase"]:
        errors.append(
            _fail(
                C.SUBMISSION_PHASE_MISMATCH,
                C.MANIFEST,
                f"archive phase={outer['phase']} 与 manifest.submission_phase="
                f"{inner_phase!r} 不一致",
            )
        )
    inner_student = str(manifest.get("student_id") or "").strip()
    if inner_student and inner_student != outer["student_id"]:
        errors.append(
            _fail(
                C.STUDENT_ID_MISMATCH,
                "archive-filename",
                f"archive student_id={outer['student_id']} 与 manifest.student_id="
                f"{inner_student!r} 不一致",
            )
        )
    return errors


def _detect_phase(root: Path) -> str:
    """从 manifest.submission_phase 推断阶段；非法 / 缺失回退 phase1。"""

    manifest_path = root / C.MANIFEST
    if manifest_path.is_file():
        try:
            declared = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(declared, dict):
                value = str(declared.get("submission_phase") or "").strip()
                if value in C.VALID_PHASES:
                    return value
        except (json.JSONDecodeError, OSError):
            pass
    return C.PHASE1


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def validate_submission(
    root: str | Path,
    phase: str,
    archive_name: str | None = None,
) -> dict[str, Any]:
    """统一入口：Base（所有 Phase 共用）+ Package Name（可选）+ Phase Extension。

    ``archive_name`` 提供时追加 archive filename ↔ manifest 一致性校验
    （Student Build 以最终目标文件名调用，Server 以实际上传文件名调用，
    双方必须得到相同错误码）。Base FAIL 时立即返回 Base 结果（扩展校验没有
    意义，且避免用扩展层掩盖公共错误）；Base PASS 后才进入扩展校验。
    """

    root = Path(root)
    base = validate_base_submission(root)
    base_warnings = list(base.get("warnings") or [])
    if base["status"] != "PASS":
        return base
    if archive_name:
        name_errors = _validate_package_name(root, archive_name)
        if name_errors:
            return _extract_result(
                C.LAYER_PACKAGE, name_errors, checks=base["checks"],
                warnings=base_warnings,
            )
    if phase == C.PHASE1:
        extension = validate_phase1_extension(root)
    elif phase == C.PHASE2:
        extension = validate_phase2_extension(root)
    else:
        return _extract_result(
            C.LAYER_IDENTITY,
            [_fail(C.INVALID_SUBMISSION_PHASE, C.MANIFEST, f"未知阶段 {phase!r}")],
            checks=base["checks"],
            warnings=base_warnings,
        )
    if extension["status"] != "PASS":
        extension["checks"] = base["checks"]
        extension["warnings"] = base_warnings + list(extension.get("warnings") or [])
        return extension
    return {
        "schema_version": C.SCHEMA_VERSION,
        "status": "PASS",
        "validation_layer": C.LAYER_ALL,
        "error_code": None,
        "error_count": 0,
        "errors": [],
        "warnings": base_warnings,
        "checks": base["checks"],
        "layers": {"base": "PASS", extension["validation_layer"]: "PASS"},
    }


def validate_single_package(
    root: str | Path,
    archive_name: str | None = None,
    phase: str | None = None,
    contract: Any = None,  # noqa: ARG001 - 保留 doc 签名兼容；现阶段固定使用本包 contract
) -> dict[str, Any]:
    """唯一 Single-Package Validator（v2 §7 / §35）。

    Student Build / Student Valid / Server Receive 全部复用这一份实现：

    - ``phase`` 缺省时从 manifest.submission_phase 推断（非法 / 缺失回退 phase1）；
    - ``archive_name`` 缺省时跳过包名校验（纯目录校验场景）。

    只读、不修复、不写文件；错误码即稳定接口。
    """

    root = Path(root)
    if phase is None:
        phase = _detect_phase(root)
    return validate_submission(root, phase, archive_name=archive_name)