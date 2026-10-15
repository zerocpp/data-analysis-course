"""Phase F 泄露扫描（§9/§43/§51–§56）。

四类互相独立，绝不混判：

- Identity（student / submission / canonical / source / archive / template）
- Private content（gold / reference / evaluator / 私有 registry 字段名）
- Secret（API key / token / .env 凭据）
- Local path（绝对盘符路径 / 机器用户目录）

扫描对象：解包后的 package 树（文件名 + 内容）与 tar header 元数据。
公开字段不算泄露（Phase2 契约保留 FM / category / anonymous id）。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from codesetarena.assignment.contract import PRIVATE_CONTENT_TOKENS
from codesetarena.submission.anonymization import scan_for_identity_leaks

_SKIP_SUFFIXES = (".gz", ".zip", ".tgz", ".whl", ".pyc", ".png", ".jpg")

SECRET_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("sk_style_api_key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")),
    ("api_key_assignment", re.compile(
        r"\b(?:LLM_API_KEY|API_KEY|OPENAI_API_KEY|OPENCODE_API_KEY)\s*=\s*\S+",
        re.IGNORECASE)),
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._-]{20,}\b")),
    ("env_credentials", re.compile(
        r"\b(?:LLM_TOKEN|ACCESS_TOKEN|SECRET_KEY|PASSWORD)\s*=\s*\S+", re.IGNORECASE)),
)

LOCAL_PATH_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    # 需要至少两级路径段：避免把 Python 源码转义序列（如 "s:\n"）误判为盘符路径
    ("windows_drive", re.compile(r"[A-Za-z]:\\[A-Za-z0-9_.\- ]+(?:\\[A-Za-z0-9_.\- ]+)+")),
    ("windows_drive_forward", re.compile(
        r"\b[A-Za-z]:/[A-Za-z0-9_.\- ]+(?:/[A-Za-z0-9_.\- ]+)+")),
    ("wsl_mnt", re.compile(r"/mnt/[a-z]/[A-Za-z0-9_\-./]+")),
    ("home_user_dir", re.compile(r"/home/[A-Za-z0-9_.-]+/")),
    ("workspace_manual", re.compile(r"workspace[/\\]+manual", re.IGNORECASE)),
)


def build_identity_tokens(
    registry_rows: list[dict],
    *,
    extra_tokens: Iterable[str] = (),
) -> list[str]:
    """§51：Known private tokens（身份只允许存在于私有 Registry）。"""
    tokens: set[str] = set()
    for row in registry_rows:
        for key in ("student_id", "submission_id", "source_task_id", "source_agent_id",
                    "task_id", "agent_id", "original_archive"):
            value = row.get(key)
            if value:
                tokens.add(str(value))
    tokens.update(t for t in extra_tokens if t)
    return sorted(tokens)


def scan_identity(root: str | Path, tokens: Iterable[str]) -> list[dict]:
    """§51：复用正式匿名身份扫描（文件名 + 内容）。"""
    return scan_for_identity_leaks(root, list(tokens))


def scan_private_content(root: str | Path) -> list[dict]:
    """§53：复用正式私有内容 token 表（gold / reference / 私有字段名）。"""
    from codesetarena.assignment.distribution import scan_private_content_leaks

    return scan_private_content_leaks(root)


def _iter_text(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() in _SKIP_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        yield path, text


def scan_secrets(root: str | Path) -> list[dict]:
    """§54：API key / token / .env 值。普通 Task 数据（CSV 数值等）不匹配模式。"""
    hits: list[dict] = []
    root = Path(root)
    for path, text in _iter_text(root):
        for label, pattern in SECRET_PATTERNS:
            match = pattern.search(text)
            if match:
                hits.append({
                    "path": str(path.relative_to(root)),
                    "kind": label,
                    "snippet": match.group(0)[:40],
                })
    return hits


def scan_local_paths(root: str | Path) -> list[dict]:
    """§55：构建机路径 / 用户目录不得进入学生可见物。"""
    hits: list[dict] = []
    root = Path(root)
    for path, text in _iter_text(root):
        for label, pattern in LOCAL_PATH_PATTERNS:
            match = pattern.search(text)
            if match:
                hits.append({
                    "path": str(path.relative_to(root)),
                    "kind": label,
                    "snippet": match.group(0)[:60],
                })
    return hits


def scan_tar_headers(archives: list[Path], tokens: Iterable[str]) -> list[dict]:
    """§43：member 名 / uname / gname / linkname / pax header 全部在扫描范围内。"""
    hits: list[dict] = []
    token_list = [t for t in tokens if t]
    import tarfile

    for archive in archives:
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar.getmembers():
                surfaces = {
                    "name": member.name,
                    "uname": member.uname or "",
                    "gname": member.gname or "",
                    "linkname": member.linkname or "",
                }
                surfaces.update({
                    f"pax:{key}": str(value)
                    for key, value in (member.pax_headers or {}).items()
                })
                for surface, value in surfaces.items():
                    for token in token_list:
                        if token in value:
                            hits.append({
                                "archive": archive.name,
                                "surface": surface,
                                "member": member.name,
                                "token": token,
                            })
    return hits


def scan_archives_for_private_content(archives: list[Path]) -> list[dict]:
    """§53：对最终 tar.gz 的文本成员执行私有内容 token 扫描。"""
    import tarfile

    hits: list[dict] = []
    for archive in archives:
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar.getmembers():
                if not member.isfile() or member.name.lower().endswith(_SKIP_SUFFIXES):
                    continue
                handle = tar.extractfile(member)
                if handle is None:
                    continue
                text = handle.read().decode("utf-8", errors="ignore")
                for token in PRIVATE_CONTENT_TOKENS:
                    if token in text:
                        hits.append({
                            "archive": archive.name,
                            "member": member.name,
                            "token": token,
                        })
    return hits


def scan_archives_for_secrets(archives: list[Path]) -> list[dict]:
    import tarfile

    hits: list[dict] = []
    for archive in archives:
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar.getmembers():
                if not member.isfile() or member.name.lower().endswith(_SKIP_SUFFIXES):
                    continue
                handle = tar.extractfile(member)
                if handle is None:
                    continue
                text = handle.read().decode("utf-8", errors="ignore")
                for label, pattern in SECRET_PATTERNS:
                    match = pattern.search(text)
                    if match:
                        hits.append({
                            "archive": archive.name,
                            "member": member.name,
                            "kind": label,
                            "snippet": match.group(0)[:40],
                        })
                for label, pattern in LOCAL_PATH_PATTERNS:
                    match = pattern.search(text)
                    if match:
                        hits.append({
                            "archive": archive.name,
                            "member": member.name,
                            "kind": f"local_path:{label}",
                            "snippet": match.group(0)[:60],
                        })
    return hits


def scan_archives_for_identity(archives: list[Path], tokens: Iterable[str]) -> list[dict]:
    import tarfile

    hits: list[dict] = []
    token_list = [t for t in tokens if t]
    for archive in archives:
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar.getmembers():
                if not member.isfile():
                    continue
                for token in token_list:
                    if token in member.name:
                        hits.append({
                            "archive": archive.name,
                            "member": member.name,
                            "token": token,
                            "surface": "member_name",
                        })
                if member.name.lower().endswith(_SKIP_SUFFIXES):
                    continue
                handle = tar.extractfile(member)
                if handle is None:
                    continue
                text = handle.read().decode("utf-8", errors="ignore")
                for token in token_list:
                    if token in text:
                        hits.append({
                            "archive": archive.name,
                            "member": member.name,
                            "token": token,
                            "surface": "content",
                        })
    return hits
