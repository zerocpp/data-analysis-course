"""Local Build / Validate —— 学生本地（Advisory 防线）。

Local 的作用只是尽早帮助学生发现错误；「本地通过」绝不等于「服务器可信任」。
学生与课程管理端共用**同一份** Single-Package Validator（v2 §0/§6）：

    BUILD    = prepare_staging -> validate_submission_tree -> pack（§52）
    VALIDATE = safe extract -> validate_submission_tree

本模块与 ``staging.py`` / ``archive.py`` 一起实现 BUILD 与 VALIDATE 两个功能
入口；服务器 / 助教复用的包内校验核心只有
``extension_validation.validate_single_package`` 一份。

硬规则：

- 本地 Build 对 locally-derived / generated metadata 一律 recompute + overwrite；
- Student-authored 内容逐字节保留，Build 绝不改写；
- task_id：已有保留（Export 语义），缺失生成 ``T_<uuid4hex>``；
- 正式提交格式为 *.zip（BigData v10.1.10：``archive_format="zip"``，
  ``BigData_P<1|2>_<student_id>.zip``）；legacy 兼容 tar.gz
  （``archive_format="tar.gz"``，历史 corpus / Reviewer Package 复用）；
- .env（机密）与 runs/ 永不进入提交包；
- **先校验最终 Staging Tree，再原样打包同一个 Tree**（v2 §4/§19）。
"""
from __future__ import annotations

import json
import posixpath
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from . import contract as C
from .archive import create_submission_archive
from .extension_validation import validate_single_package, validate_submission
from .safe_extract import safe_extract_archive
from .staging import StagingError, prepare_submission_staging

_ZIP_STAMP = (2026, 1, 1, 0, 0, 0)  # 固定时间戳：fixture 生成确定性

VALID_ARCHIVE_FORMATS = ("zip", "tar.gz")

# Phase 2 自动识别标记（v10.1.14：阶段信息从包内 metadata 读取，学生不手输 --phase）
_PHASE2_MARKERS = (C.PHASE2_ASSIGNMENT, C.PHASE2_REVIEW_COMMENTS, C.PHASE2_EXPERIMENT_REPORT)


class SubmissionBuildError(RuntimeError):
    """本地构建失败（workspace 网关校验未通过 / 非法参数）。"""


def detect_workspace_phase(workspace: str | Path) -> str:
    """从 workspace 内的 Phase 2 标记文件自动识别阶段（缺省 P1）。"""

    ws = Path(workspace)
    if any((ws / marker).is_file() for marker in _PHASE2_MARKERS):
        return C.PHASE2
    return C.PHASE1


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def build_submission(
    workspace: str | Path,
    *,
    phase: str | None = None,
    student_id: str,
    output_path: str | Path | None = None,
    course: str | None = None,
    archive_format: str = "zip",
) -> dict[str, Any]:
    """Workspace -> Submission Archive（v2 BUILD，§4/§52）。

    ``prepare_submission_staging``（Workspace -> 最终 Staging Tree）
    -> ``validate_submission`` 对**同一个 Tree** 做正式 Single-Package 校验
    -> ``_zip_tree`` / ``create_submission_archive`` 原样打包。

    ``phase=None``（v10.1.14 缺省）时从 workspace 自动识别阶段：
    存在 assignment.json / review-comments.json / 实验报告.docx 即为 P2，否则 P1。

    - 正式格式：``archive_format="zip"``（BigData v10.1.10：
      ``<course>_P<phase>_<student_id>.zip``，缺省 course=BigData）；
    - legacy 兼容：``archive_format="tar.gz"``（历史 corpus / Reviewer Package）。

    退出语义由 CLI 层映射：网关失败 -> 2；本地校验 FAIL -> 1（仍产出包，
    便于学生检查 local_validation.json）；PASS -> 0。
    """

    if archive_format not in VALID_ARCHIVE_FORMATS:
        raise SubmissionBuildError(f"非法 archive_format {archive_format!r}（zip / tar.gz）")
    workspace = Path(workspace)
    phase = phase or detect_workspace_phase(workspace)
    try:
        tree, task_id, resolved_course = prepare_submission_staging(
            workspace, phase=phase, student_id=student_id, course=course,
        )
    except StagingError as exc:
        raise SubmissionBuildError(str(exc)) from exc

    try:
        if output_path is None:
            phase_token = "1" if phase == C.PHASE1 else "2"
            ext = C.FORMAL_ARCHIVE_EXTENSION if archive_format == "zip" else C.LEGACY_ARCHIVE_EXTENSION
            output_path = workspace / "submission" / (
                f"{resolved_course}_P{phase_token}_{student_id}{ext}"
            )
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # ---- 正式 Single-Package 校验（与服务器同一份 Validator）----
        # 包名（<course>_P<phase>_<student_id>.<ext>）也是 Contract 的一部分：
        # 用最终目标文件名校验，保证「学生校验的东西就是实际提交的东西」。
        local_result = validate_submission(tree, phase, archive_name=output_path.name)
        local_validation = {
            "schema_version": C.VALIDATION_REPORT_SCHEMA_VERSION,
            "status": local_result["status"],
            "validation_layer": local_result["validation_layer"],
            "error_code": local_result["error_code"],
            "error_count": local_result["error_count"],
            "errors": local_result["errors"],
            "validated_at": _now_iso(),
        }
        (tree / C.LOCAL_VALIDATION).write_text(
            json.dumps(local_validation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

        if local_result["status"] != "PASS":
            # v2 §4/§49-B：校验失败 -> 打印结构化错误 -> 不生成正式 archive
            return {
                "status": "validation_failed",
                "package_path": None,
                "task_id": task_id,
                "student_id": student_id,
                "submission_phase": phase,
                "archive_format": archive_format,
                "local_validation": local_validation,
                "exit_code": 1,
            }

        if archive_format == "zip":
            _zip_tree(tree, output_path)
        else:
            create_submission_archive(tree, output_path)

        return {
            "status": "built",
            "package_path": str(output_path),
            "task_id": task_id,
            "student_id": student_id,
            "submission_phase": phase,
            "archive_format": archive_format,
            "local_validation": local_validation,
            "exit_code": 0 if local_validation["status"] == "PASS" else 1,
        }
    finally:
        shutil.rmtree(tree.parent, ignore_errors=True)


def _zip_tree(tree: Path, output_path: Path) -> None:
    """把 tree 打包为 zip（成员相对路径；固定时间戳保证确定性）。"""

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(p for p in tree.rglob("*") if p.is_file()):
            info = zipfile.ZipInfo(posixpath.join(*path.relative_to(tree).parts))
            info.date_time = _ZIP_STAMP
            info.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(info, path.read_bytes())


def validate_local_zip(archive_path: str | Path, phase: str | None = None) -> dict[str, Any]:
    """本地校验已构建的 Submission Archive（zip / tar.gz，复用与服务器完全
    相同的 Single-Package Validator；archive 文件名也是 Contract 一部分）。
    """

    archive_path = Path(archive_path)
    staging = Path(tempfile.mkdtemp(prefix="csa_local_validate_"))
    try:
        from .safe_extract import SafeExtractError

        safe_extract_archive(archive_path, staging)
        return validate_single_package(staging, archive_name=archive_path.name, phase=phase)
    except SafeExtractError as exc:
        return {
            "schema_version": C.SCHEMA_VERSION,
            "status": "FAIL",
            "validation_layer": C.LAYER_SAFE_EXTRACT,
            "error_code": exc.code,
            "error_count": 1,
            "errors": [{"code": exc.code, "location": "archive", "detail": exc.detail}],
            "warnings": [],
        }
    finally:
        shutil.rmtree(staging, ignore_errors=True)