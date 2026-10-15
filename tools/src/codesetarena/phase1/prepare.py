"""Raw Submission -> Canonical Working Copy 准备（教师端 Batch Build 的第一步）。

只读 raw：学生 ZIP / 提交目录永不修改、永不重命名内部内容、不自动修复 JSON。
canonical 是「用于课程运行的正式副本」：只拷贝运行必需的白名单文件
（task.json / data.csv / reference.py / agent.py / .env.example / metadata.json），
绝不拷贝 .env（机密）、runs/、__pycache__/、build_manifest.json 等非运行文件。

Task_ID 固化规则（与 Export 一致，见 main/export.ensure_task_identity）：task.json
已有 task_id 一律保留（rebuild 不换 ID）；缺失/为空时生成 ``T_<uuid4hex>`` 并
只写回 canonical 副本。产生的 ID 变更记录到 audit/task_id_corrections.jsonl。
"""
from __future__ import annotations

import json
import shutil
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from ..constants import TASK_ID_PREFIX
from .audit import append_task_id_correction

CANONICAL_FILE_WHITELIST = (
    "task.json", "data.csv", "reference.py", "agent.py", ".env.example", "metadata.json",
)
_NON_CANONICAL = (".env", "runs", "__pycache__", "build_manifest.json", "export_manifest.json")


def ensure_task_id(task: dict[str, Any], *, log: dict[str, Any] | None = None) -> str:
    """返回固化后的 task_id：已有保留，缺失生成 ``T_<uuid4hex>``。"""

    current = str(task.get("task_id") or "").strip()
    if current:
        return current
    new_id = f"{TASK_ID_PREFIX}{uuid.uuid4().hex}"
    task["task_id"] = new_id
    if log is not None:
        log["original_task_id"] = log.get("original_task_id") or ""
        log["canonical_task_id"] = new_id
        log["reason"] = "canonicalize_generate_global_id"
    return new_id


def _student_id_from_task(task_path: Path) -> str:
    try:
        task = json.loads(task_path.read_text(encoding="utf-8"))
        value = str(task.get("student_id") or "").strip()
        return value
    except Exception:
        return ""


def _copy_canonical_files(source: Path, dest: Path) -> dict[str, str]:
    """把白名单文件复制进 dest；返回 {name: sha256}。task.json 单独处理（需固化 ID）。"""

    copied: dict[str, str] = {}
    for name in CANONICAL_FILE_WHITELIST:
        if name == "task.json":
            continue
        src = source / name
        if src.is_file():
            dst = dest / name
            if dst.exists() and dst.read_bytes() == src.read_bytes():
                pass  # 已存在且一致，跳过写
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            copied[name] = _sha256(src)
    return copied


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare_canonical(
    source: str | Path,
    canonical_root: str | Path,
    *,
    audit_log: str | Path | None = None,
) -> Path | None:
    """把一个原始提交（目录或学生 ZIP）整理为 canonical/<student_id>/ 工作副本。

    - source 为 .zip 时只解压白名单成员（zip-slip 安全：仅取扁平文件名）；
    - source 为目录时只复制白名单文件；
    - 返回 canonical 目录；无法定位 student_id / 无 task.json 时返回 None（由
      Batch Validator 报错，prepare 不自动修复）。
    """

    source = Path(source)
    canonical_root = Path(canonical_root)
    canonical_root.mkdir(parents=True, exist_ok=True)

    working = source
    extracted_tmp: Path | None = None
    if source.is_file() and zipfile.is_zipfile(source):
        extracted_tmp = canonical_root / f".staging_{uuid.uuid4().hex}"
        extracted_tmp.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(source) as bundle:
            for member in bundle.namelist():
                name = Path(member).name
                if not name or name.startswith("."):
                    continue
                if name in _NON_CANONICAL:
                    continue
                if ".." in member or member.startswith("/"):
                    continue
                if name in CANONICAL_FILE_WHITELIST:
                    target = extracted_tmp / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.open(member) as src, open(target, "wb") as out:
                        out.write(src.read())
        working = extracted_tmp
    elif not source.is_dir():
        return None

    task_path = working / "task.json"
    if not task_path.is_file():
        if extracted_tmp is not None:
            shutil.rmtree(extracted_tmp, ignore_errors=True)
        return None

    try:
        task = json.loads(task_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        if extracted_tmp is not None:
            shutil.rmtree(extracted_tmp, ignore_errors=True)
        return None
    if not isinstance(task, dict):
        if extracted_tmp is not None:
            shutil.rmtree(extracted_tmp, ignore_errors=True)
        return None

    student_id = str(task.get("student_id") or "").strip()
    if not student_id:
        if extracted_tmp is not None:
            shutil.rmtree(extracted_tmp, ignore_errors=True)
        return None

    dest = canonical_root / student_id
    dest.mkdir(parents=True, exist_ok=True)

    # 只读 task.json 到内存，固化 Task_ID 后写回 canonical 副本（raw 不受影响）
    note: dict[str, Any] = {
        "student_id": student_id,
        "original_task_id": str(task.get("task_id") or ""),
        "canonical_task_id": str(task.get("task_id") or ""),
        "reason": "no_change",
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    ensure_task_id(task, log=note)
    (dest / "task.json").write_text(
        json.dumps(task, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    _copy_canonical_files(working, dest)

    if extracted_tmp is not None:
        shutil.rmtree(extracted_tmp, ignore_errors=True)

    if note["reason"] != "no_change" and audit_log is not None:
        original_hash: str | None = None
        if source.is_file() and zipfile.is_zipfile(source):
            original_hash = _sha256(source)
        note["original_package_hash"] = original_hash
        note["canonical_package_hash"] = _sha256(dest / "task.json")
        append_task_id_correction(audit_log, note)
    return dest