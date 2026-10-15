"""Archive Transport —— 正式提交产物（*.tar.gz）构建 + Archive Intake（v2 §5/§8/§39）。

功能（不是角色）：

- ``create_submission_archive``：把最终 Staging Tree **原样**打包为确定性
  *.tar.gz（同一棵树两次打包字节一致；固定 mtime / uid / gid / gzip 头）。
- ``validate_archive_envelope``：Archive Intake 第一道护栏 —— 能否打开、是否
  损坏、成员数上限、总解压大小上限（tar bomb 防护）。
- ``validate_submission_archive``：VALIDATE 模式核心 —— envelope →
  ``safe_extract_archive`` → 共享 Single-Package Validator
  （``validate_single_package``）。

同一个 ``validate_submission_archive`` 同时被：

- ``submission --mode validate``（CLI）
- ``submission --mode batch-validate`` 第一阶段（N × validate，§13）

复用，绝不各自实现第二套解包 / 校验逻辑。

正式提交格式为 *.tar.gz（v2 §2/Q7）；.zip 作为 legacy 兼容格式仍可被
``validate_submission_archive`` 接收（永久 fixture corpus 驱动）。
"""
from __future__ import annotations

import calendar
import gzip
import io
import posixpath
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from . import contract as C
from .extension_validation import validate_single_package
from .safe_extract import (
    SafeExtractError,
    TAR_MAX_MEMBERS,
    TAR_MAX_TOTAL_BYTES,
    safe_extract_archive,
)

_TAR_STAMP = (2026, 1, 1, 0, 0, 0)  # 固定时间戳：确定性字节（与 ZIP fixture 一致）


# ---------------------------------------------------------------------------
# BUILD 的 pack 一步：Staging Tree -> *.tar.gz
# ---------------------------------------------------------------------------


def create_submission_archive(
    tree: str | Path,
    output_path: str | Path,
    *,
    prefix: str | None = None,
) -> Path:
    """把 staging tree 原样打包为确定性 tar.gz（成员排序 + 固定元数据）。

    ``prefix`` 提供时为全部成员加统一顶层目录（Reviewer Package 导出复用同一
    builder）；缺省 None 保持成员位于包根。
    """

    tree = Path(tree)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = calendar.timegm(_TAR_STAMP)  # UTC 固定 mtime（确定性）

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as bundle:
        for path in sorted(p for p in tree.rglob("*") if p.is_file()):
            rel = posixpath.join(*path.relative_to(tree).parts)
            arcname = posixpath.join(prefix, rel) if prefix else rel
            info = bundle.gettarinfo(path, arcname=arcname)
            info.mtime = stamp
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mode = 0o644  # 归一化权限：不同文件系统（如 DRVFS 0777）不影响字节
            info.pax_headers = {}  # PAX 扩展头清空：不同文件系统的 atime/ctime 不进入包
            with open(path, "rb") as handle:
                bundle.addfile(info, handle)
    tar_bytes = buffer.getvalue()

    # gzip 头固定 mtime=0、filename=""：同一棵树两次打包字节一致
    with open(output_path, "wb") as out, gzip.GzipFile(
        fileobj=out, mode="wb", mtime=0, filename=""
    ) as gz:
        gz.write(tar_bytes)
    return output_path


# ---------------------------------------------------------------------------
# Archive Intake（v2 §8/§18：只是 transport protection，不是第二套 Validator）
# ---------------------------------------------------------------------------


def _is_tar_archive(path: Path) -> bool:
    name = path.name.lower()
    return name.endswith(".tar.gz") or name.endswith(".tgz") or name.endswith(".tar")


def validate_archive_envelope(
    archive: str | Path,
    *,
    max_members: int = TAR_MAX_MEMBERS,
    max_total_bytes: int = TAR_MAX_TOTAL_BYTES,
) -> None:
    """Archive Intake：能否打开 / 是否损坏 / 成员数与总解压大小护栏。

    非法时抛 ``SafeExtractError``（含稳定错误码），返回 None 表示通过。
    """

    archive = Path(archive)
    if _is_tar_archive(archive):
        with tarfile.open(archive, "r:*") as bundle:
            members = bundle.getmembers()
            total = sum(member.size for member in members if member.isreg())
            if len(members) > max_members:
                raise SafeExtractError(
                    C.TAR_ARCHIVE_BOMB,
                    f"tar 成员数 {len(members)} 超过上限 {max_members}（疑似解压炸弹）",
                )
            if total > max_total_bytes:
                raise SafeExtractError(
                    C.TAR_ARCHIVE_BOMB,
                    f"tar 解压总大小 {total} B 超过上限 {max_total_bytes} B",
                )
        return
    with zipfile.ZipFile(archive) as bundle:
        members = bundle.infolist()
        total = sum(info.file_size for info in members)
        if len(members) > max_members:
            raise SafeExtractError(
                C.ZIP_EXTRACT_ERROR, f"zip 成员数 {len(members)} 超过上限 {max_members}"
            )
        if total > max_total_bytes:
            raise SafeExtractError(
                C.ZIP_EXTRACT_ERROR, f"zip 解压总大小 {total} B 超过上限 {max_total_bytes} B"
            )


def _intake_failure(result: dict[str, Any], exc: SafeExtractError) -> dict[str, Any]:
    result.update(
        single_package_status="FAIL",
        status="FAIL",
        validation_layer=C.LAYER_SAFE_EXTRACT,
        error_code=exc.code,
        error_detail=exc.detail,
        errors=[{"code": exc.code, "location": result["archive"], "detail": exc.detail}],
    )
    return result


# ---------------------------------------------------------------------------
# VALIDATE 模式核心（batch-validate 第一阶段复用）
# ---------------------------------------------------------------------------


def validate_submission_archive(archive: str | Path, phase: str | None = None) -> dict[str, Any]:
    """VALIDATE：Archive Intake -> safe extract -> 共享 Single-Package Validator。

    返回统一 ValidationResult（§9）：archive / single_package_status / status /
    validation_layer / error_code / student_id / task_id / submission_phase /
    errors / warnings / checks。只读，不修复任何内容。
    """

    archive = Path(archive)
    result: dict[str, Any] = {
        "archive": archive.name,
        "single_package_status": "PASS",
        "status": "PASS",
        "validation_layer": C.LAYER_ALL,
        "error_code": None,
        "student_id": "",
        "task_id": "",
        "submission_phase": "",
        "error_detail": "",
        "errors": [],
        "warnings": [],
        "checks": {},
    }
    try:
        validate_archive_envelope(archive)
    except SafeExtractError as exc:
        return _intake_failure(result, exc)
    except (zipfile.BadZipFile, tarfile.TarError, OSError) as exc:
        return _intake_failure(
            result,
            SafeExtractError(
                C.TAR_EXTRACT_ERROR if _is_tar_archive(archive) else C.ZIP_EXTRACT_ERROR,
                f"archive 无法读取: {type(exc).__name__}: {exc}",
            ),
        )

    with tempfile.TemporaryDirectory(prefix="csa_validate_extract_") as tmp:
        try:
            safe_extract_archive(archive, tmp)
        except SafeExtractError as exc:
            return _intake_failure(result, exc)
        body = validate_single_package(tmp, archive_name=archive.name, phase=phase)

    checks = body.get("checks") or {}
    result.update(
        single_package_status=body.get("status", "FAIL"),
        status=body.get("status", "FAIL"),
        validation_layer=body.get("validation_layer", ""),
        error_code=body.get("error_code"),
        student_id=checks.get("student_id") or "",
        task_id=checks.get("task_id") or "",
        submission_phase=checks.get("submission_phase") or "",
        error_detail=next(iter(body.get("errors") or []), {}).get("detail") or "",
        errors=body.get("errors", []),
        warnings=body.get("warnings", []),
        checks=checks,
    )
    return result