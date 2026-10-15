"""Unified Safe Extract —— 所有学生 ZIP / tar.gz / Workspace ZIP / Fixture Overlay 的唯一解压入口。

禁止任何测试脚本 / 服务器代码自行 ``extractall``。安全规则（zip 与 tar 一致）：

- 拒绝绝对路径成员；
- 拒绝 ``..`` 路径穿越（任何路径段为 ``..``）；
- 拒绝 Windows 盘符 / UNC 路径；
- 拒绝符号链接成员（解压会写穿目标目录）；
- tar 额外拒绝硬链接 / 设备文件 / FIFO；
- 解压炸弹护栏：成员数与总解压大小上限；
- 所有成员必须落在 target_dir 内部。

本模块同时提供 fixture SHA256 验证（测试前后调用），保证测试过程不修改原始
fixture（fixture 是测试输入真相，永不原地解压 / 修改 / 重建）。
"""
from __future__ import annotations

import hashlib
import tarfile
import zipfile
from pathlib import Path

from .contract import (
    TAR_ARCHIVE_BOMB,
    TAR_EXTRACT_ERROR,
    TAR_PATH_TRAVERSAL,
    TAR_UNSAFE_LINK,
    ZIP_EXTRACT_ERROR,
    ZIP_PATH_TRAVERSAL,
)

# tar.gz Archive Intake 护栏（v2 §8/§18：解压炸弹 / 异常文件数 / 总解压大小）
TAR_MAX_MEMBERS = 2000
TAR_MAX_TOTAL_BYTES = 256 * 1024 * 1024


class SafeExtractError(RuntimeError):
    """解压被拒绝（含错误码，供上层生成 validation report）。"""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def verify_sha256(path: str | Path, expected: str) -> bool:
    """校验文件 SHA256（case.json 记录的 fixture 哈希）。"""

    return sha256_file(path) == expected.lower()


def sha256_file(path: str | Path) -> str:
    path = Path(path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _member_name(member: str) -> str:
    """规范化 zip 成员名：统一正斜杠、去前导 ./。非法形态返回 None。"""

    name = member.replace("\\", "/")
    while name.startswith("./"):
        name = name[2:]
    if name.startswith("/"):
        return None  # 绝对路径
    if ":" in name.split("/")[0]:
        return None  # 盘符/UNC
    segments = [seg for seg in name.split("/") if seg not in ("", ".")]
    if any(seg == ".." for seg in segments):
        return None  # 路径穿越
    return "/".join(segments)


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0o170000
    return mode == 0o120000


def safe_extract(zip_path: str | Path, target_dir: str | Path) -> list[str]:
    """安全解压 zip 到 target_dir；返回解压出的相对路径列表。

    任何一个成员非法即整体失败（抛 SafeExtractError），并清理已写出的部分，
    保证不会留下半解压的不可信内容。
    """

    zip_path = Path(zip_path)
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    relative_names: list[str] = []
    try:
        with zipfile.ZipFile(zip_path) as bundle:
            for info in bundle.infolist():
                name = _member_name(info.filename)
                if name is None:
                    _rollback(written)
                    raise SafeExtractError(
                        ZIP_PATH_TRAVERSAL, f"zip 成员路径非法: {info.filename!r}"
                    )
                if _is_symlink(info):
                    _rollback(written)
                    raise SafeExtractError(
                        ZIP_PATH_TRAVERSAL, f"zip 成员是符号链接，拒绝: {info.filename!r}"
                    )
                if info.is_dir():
                    continue
                target = (target_dir / name).resolve()
                if not target.is_relative_to(target_dir.resolve()):
                    _rollback(written)
                    raise SafeExtractError(
                        ZIP_PATH_TRAVERSAL, f"zip 成员逃逸目标目录: {info.filename!r}"
                    )
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(info) as src, open(target, "wb") as out:
                    out.write(src.read())
                written.append(target)
                relative_names.append(name)
    except SafeExtractError:
        raise
    except zipfile.BadZipFile as exc:
        _rollback(written)
        raise SafeExtractError(ZIP_EXTRACT_ERROR, f"zip 无法读取: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - 任何解压异常一律按 extract error 处理
        _rollback(written)
        raise SafeExtractError(ZIP_EXTRACT_ERROR, f"zip 解压失败: {type(exc).__name__}: {exc}") from exc
    return relative_names


def _rollback(written: list[Path]) -> None:
    """删除已写出的部分文件（按写入顺序倒序，先文件后空目录）。"""

    for path in reversed(written):
        try:
            if path.is_file():
                path.unlink()
            parent = path.parent
            while parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent
        except OSError:
            pass
    written.clear()


# ---------------------------------------------------------------------------
# tar.gz（正式提交格式）安全解压
# ---------------------------------------------------------------------------


def _tar_member_name(member: tarfile.TarInfo) -> str | None:
    """规范化 tar 成员名；非法形态（绝对路径 / ../ / 盘符）返回 None。"""

    name = member.name.replace("\\", "/").strip()
    while name.startswith("./"):
        name = name[2:]
    if name.startswith("/"):
        return None
    if ":" in name.split("/")[0]:
        return None
    segments = [seg for seg in name.split("/") if seg not in ("", ".")]
    if any(seg == ".." for seg in segments):
        return None
    return "/".join(segments)


def safe_extract_tar(
    tar_path: str | Path,
    target_dir: str | Path,
    *,
    max_members: int = TAR_MAX_MEMBERS,
    max_total_bytes: int = TAR_MAX_TOTAL_BYTES,
) -> list[str]:
    """安全解压 tar / tar.gz 到 target_dir；返回相对路径列表。

    与 ``safe_extract``（zip）同一套安全语义：任何成员非法即整体失败并回滚，
    不留半解压内容。额外拒绝：符号链接、硬链接、设备文件、FIFO。
    """

    tar_path = Path(tar_path)
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    relative_names: list[str] = []
    count = 0
    total_size = 0
    try:
        with tarfile.open(tar_path, "r:*") as bundle:
            for member in bundle:
                count += 1
                if count > max_members:
                    _rollback(written)
                    raise SafeExtractError(
                        TAR_ARCHIVE_BOMB, f"tar 成员数 {count} 超过上限 {max_members}"
                    )
                name = _tar_member_name(member)
                if member.isdir():
                    continue
                if name is None or not name:
                    _rollback(written)
                    raise SafeExtractError(
                        TAR_PATH_TRAVERSAL, f"tar 成员路径非法: {member.name!r}"
                    )
                if member.issym() or member.islnk():
                    _rollback(written)
                    raise SafeExtractError(
                        TAR_UNSAFE_LINK, f"tar 成员是链接（symlink/hardlink），拒绝: {member.name!r}"
                    )
                if not member.isreg():
                    _rollback(written)
                    raise SafeExtractError(
                        TAR_UNSAFE_LINK, f"tar 成员是特殊文件（设备/FIFO），拒绝: {member.name!r}"
                    )
                if member.size > max_total_bytes - total_size:
                    _rollback(written)
                    raise SafeExtractError(
                        TAR_ARCHIVE_BOMB,
                        f"tar 解压总大小超过上限 {max_total_bytes} B（成员 {member.name!r}）",
                    )
                total_size += member.size
                target = (target_dir / name).resolve()
                if not target.is_relative_to(target_dir.resolve()):
                    _rollback(written)
                    raise SafeExtractError(
                        TAR_PATH_TRAVERSAL, f"tar 成员逃逸目标目录: {member.name!r}"
                    )
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.extractfile(member) as src, open(target, "wb") as out:
                    out.write(src.read())
                written.append(target)
                relative_names.append(name)
    except SafeExtractError:
        raise
    except tarfile.TarError as exc:
        _rollback(written)
        raise SafeExtractError(TAR_EXTRACT_ERROR, f"tar 无法读取: {exc}") from exc
    except OSError as exc:
        _rollback(written)
        raise SafeExtractError(TAR_EXTRACT_ERROR, f"tar 解压失败: {exc}") from exc
    return relative_names


def safe_extract_archive(archive_path: str | Path, target_dir: str | Path) -> list[str]:
    """统一条目：按后缀分发到 tar.gz / zip 安全解压（v2 §5/§8）。"""

    name = str(archive_path).lower()
    if name.endswith(".tar.gz") or name.endswith(".tgz") or name.endswith(".tar"):
        return safe_extract_tar(archive_path, target_dir)
    return safe_extract(archive_path, target_dir)