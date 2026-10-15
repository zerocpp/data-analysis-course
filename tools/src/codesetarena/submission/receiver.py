"""Server Receive Pipeline —— 服务器端权威防线（UNTRUSTED INPUT 从零验证）。

正式流程（§2 / §12 / §13）::

    Untrusted ZIP
      -> Archive Filename Parse
      -> Raw 存档（不可变，永不修改）
      -> Safe Extract
      -> Identity Cross-Check（外-内-Roster）
      -> Base Submission Validation
      -> Phase Extension Validation
      -> Server Canonical Build（含 Server-owned Metadata 重新生成）
      -> Accepted Canonical Submission

硬规则：

- 学生本地 validation 状态 / validation_report / registry / assignment /
  server_metadata 一律不是 server truth：服务器 IGNORE + REGENERATE；
- raw/<archive> 是该 sid+phase 的唯一 **ACTIVE 槽位**（v10.1.12）：校验 FAIL 的包
  不占槽位（history/rejected/），同 SHA 重复 = DUPLICATE_ARCHIVE，同名不同 SHA =
  新内容，替换 ACTIVE 需显式 --supersede（旧包入 history/superseded/）；
- 身份校验必须先于 canonical 化（先覆盖再验证会掩盖身份问题）；
- 拒绝（FAIL）的包不写 canonical，只留 history/rejected 证据 + validation report；
- student-authored 内容（task/task.json、task/data、agent/agent.py、
  task/reference.py）在 canonical 中逐字节保留。
"""
from __future__ import annotations

import json
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from . import contract as C
from .base_validation import validate_base_submission
from .extension_validation import validate_submission
from .identity import (
    IdentityError,
    collect_inner_identity,
    cross_check_identity,
    load_roster,
    parse_archive_name,
)
from .safe_extract import SafeExtractError, safe_extract, sha256_file

SERVER_METADATA = "server_metadata.json"
BATCH_REPORT = "batch_report.json"


def ensure_pool_layout(pool_root: str | Path) -> Path:
    """创建 pool 标准子目录并返回根路径。"""

    root = Path(pool_root)
    for name in C.POOL_SUBDIRS:
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Report 组装
# ---------------------------------------------------------------------------


def _combined_report(
    *,
    archive_name: str,
    outer: dict[str, str],
    identity: dict[str, Any] | None,
    base: dict[str, Any] | None,
    extension: dict[str, Any] | None,
    batch: str,
    package_sha256: str,
) -> dict[str, Any]:
    layers: list[dict[str, Any]] = []
    for result in (identity, base, extension):
        if result is not None:
            layers.append(
                {"layer": result["validation_layer"], "status": result["status"],
                 "error_code": result.get("error_code"), "errors": result.get("errors", [])}
            )
    failed = [layer for layer in layers if layer["status"] != "PASS"]
    first = failed[0] if failed else None
    return {
        "schema_version": C.VALIDATION_REPORT_SCHEMA_VERSION,
        "status": "FAIL" if failed else "PASS",
        "validated_at": _now_iso(),
        "submission_archive": archive_name,
        "student_id": (identity or {}).get("identity", {}).get("outer_student_id")
        or (identity or {}).get("identity", {}).get("inner_student_ids", [""])[0] or "",
        "submission_phase": outer.get("phase", ""),
        "submission_batch": batch,
        "package_sha256": package_sha256,
        "validation_layer": first["layer"] if first else C.LAYER_ALL,
        "error_code": first["error_code"] if first else None,
        "error_count": sum(len(layer["errors"]) for layer in failed),
        "errors": [e for layer in failed for e in layer["errors"]],
        "layers": layers,
    }


def write_report(report: dict[str, Any], validation_dir: str | Path, name: str = "validation_report.json") -> Path:
    path = Path(validation_dir) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Server Canonical Build（身份校验通过之后才允许执行）
# ---------------------------------------------------------------------------


def _server_manifest(
    outer: dict[str, str],
    task_id: str,
    student_id: str,
    *,
    archive_name: str,
    package_sha256: str,
    batch: str,
    base_checks: dict[str, Any],
) -> dict[str, Any]:
    """服务器重新生成的 canonical manifest（Student 值全部忽略）。"""

    return {
        "schema_version": C.SCHEMA_VERSION,
        "course": outer.get("course", ""),
        "submission_phase": outer["phase"],
        "student_id": student_id,
        "task_id": task_id,
        "built_at": _now_iso(),
        "build_status": "passed",  # Server-owned：由本次 receive 重新推导
        "files": base_checks.get("checks", {}).get("files", []),
        "server": {
            "received_timestamp": _now_iso(),
            "raw_archive": archive_name,
            "package_sha256": package_sha256,
            "submission_batch": batch,
            "server_schema_version": C.SCHEMA_VERSION,
            "server_validation_version": C.SERVER_VALIDATION_VERSION,
            "accepted": True,
        },
    }


def _server_metadata_dict(
    *,
    archive_name: str,
    student_id: str,
    task_id: str,
    phase: str,
    batch: str,
    package_sha256: str,
    canonical_path: str,
) -> dict[str, Any]:
    """Server-owned metadata（学生伪造值一律被覆盖）。"""

    return {
        "schema_version": C.SERVER_METADATA_SCHEMA_VERSION,
        "student_id": student_id,
        "task_id": task_id,
        "submission_phase": phase,
        "submission_batch": batch,
        "raw_archive": archive_name,
        "archive_sha256": package_sha256,
        "canonical_path": canonical_path,
        "received_timestamp": _now_iso(),
        "accepted": True,
        "validation_status": "PASS",
        "server_schema_version": C.SCHEMA_VERSION,
        "server_validation_version": C.SERVER_VALIDATION_VERSION,
    }


def _regenerate_agent_manifest(
    root: Path, *, student_id: str, task_id: str
) -> dict[str, Any]:
    """服务器重新生成 agent/agent_manifest.json（entrypoint 哈希重算）。"""

    entrypoint = root / C.AGENT_ENTRYPOINT
    entry_sha = sha256_file(entrypoint) if entrypoint.is_file() else ""
    return {
        "schema_version": C.SCHEMA_VERSION,
        "student_id": student_id,
        "task_id": task_id,
        "entrypoint": "agent.py",
        "built_at": _now_iso(),
        "files": {"agent/agent.py": entry_sha},
    }


def _write_operative_mirror(
    submissions_dir: Path,
    *,
    student_id: str,
    phase: str,
    canonical_root: Path,
    checks: dict[str, Any],
) -> Path:
    """生成现有教师流水线可用的扁平 operative workspace（白名单文件）。

    canonical/<student_id>_<phase>/ 下的 task.json / data.csv / agent.py /
    reference.py / .env.example 来自学生内容（逐字节），metadata.json 由服务器
    生成（build_status 为 Server-owned 字段）。
    """

    mirror = canonical_root / f"{student_id}_{phase}"
    mirror.mkdir(parents=True, exist_ok=True)

    src_task = submissions_dir / C.TASK_JSON
    if src_task.is_file():
        shutil.copy2(src_task, mirror / "task.json")

    data_files = sorted((submissions_dir / C.TASK_DATA_DIR).glob("*")) if (
        submissions_dir / C.TASK_DATA_DIR
    ).is_dir() else []
    if data_files:
        shutil.copy2(data_files[0], mirror / "data.csv")

    for rel, name in ((C.AGENT_ENTRYPOINT, "agent.py"), (C.REFERENCE, "reference.py"),
                      (C.ENV_EXAMPLE, ".env.example")):
        src = submissions_dir / rel
        if src.is_file():
            shutil.copy2(src, mirror / name)

    task: dict[str, Any] = {}
    task_path = mirror / "task.json"
    if task_path.is_file():
        try:
            task = json.loads(task_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            task = {}
    files_sha = {}
    for name in ("task.json", "data.csv", "agent.py", "reference.py", ".env.example"):
        path = mirror / name
        if path.is_file():
            files_sha[name] = sha256_file(path)
    metadata = {
        "workspace": f"{student_id}_{phase}",
        "student_id": student_id,
        "task_id": str(task.get("task_id") or ""),
        "submission_phase": phase,
        "built_at": _now_iso(),
        "build_status": "passed",  # Server-owned：与 raw/canonical 一致，非学生值
        "files": files_sha,
    }
    (mirror / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return mirror


# ---------------------------------------------------------------------------
# Receive 顶层
# ---------------------------------------------------------------------------


def _append_attempt(pool_root: Path, entry: dict[str, Any]) -> None:
    """append-only 审计：pool/audit/submission_attempts.jsonl（v10.1.12 §8）。"""

    entry = {"received_at": _now_iso(), **entry}
    log_path = pool_root / C.AUDIT / C.ATTEMPTS_LOG
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _history_name(archive: Path, sha256: str) -> str:
    """history 存档名：<stem>__<sha8><suffix>（如 BigData_P1_20260201__ab12cd34.zip）。"""

    return f"{archive.stem}__{sha256[:8]}{archive.suffix}"


def receive_submission(
    archive: str | Path,
    pool_root: str | Path,
    roster: str | Path,
    *,
    batch: str = "main",
    operative_root: str | Path | None = None,
    supersede: bool = False,
) -> dict[str, Any]:
    """服务器收包全流程（v10.1.12 Resubmission 语义）。返回 {status, exit_code, ...}。

    - ``pool/raw/<archive>`` 是该 sid+phase 的唯一 **ACTIVE 槽位**；
    - 校验 FAIL 的包 **不占 active 槽位**：存入 ``history/rejected/<stem>__<sha8><ext>``；
    - ``DUPLICATE_ARCHIVE`` 仅用于**完全相同的包**（incoming SHA256 == active SHA256）；
      同名不同 SHA = 新内容，按新提交走完整校验；
    - active 已存在且新包不同 SHA：默认拒绝（``ACTIVE_SUBMISSION_EXISTS``，旧 active
      不变）；``supersede=True`` 且新包 PASS 时旧包进入 ``history/superseded/``，
      新包原子替换 active 槽位；
    - ``<pool>/FROZEN`` 标记存在 => 任何 receive 返回 ``PHASE_FROZEN``（收包冻结，
      补交走 scripts/add_late_submission.py）；
    - 每次 receive 追加一行审计到 ``pool/audit/submission_attempts.jsonl``。
    """

    archive = Path(archive)
    pool_root = ensure_pool_layout(pool_root)
    validation_dir = pool_root / C.VALIDATION
    mirror_root = Path(operative_root) if operative_root else pool_root / C.CANONICAL
    report: dict[str, Any] | None = None
    extra: dict[str, Any] = {"raw_path": "", "canonical_path": "", "mirror_path": ""}

    # [0] Freeze boundary（收包冻结；补交走 late submission 入口）
    if (pool_root / C.FREEZE_MARKER).exists():
        report = _combined_report(
            archive_name=archive.name, outer={"phase": "", "student_id": ""},
            identity={"validation_layer": C.LAYER_IDENTITY, "status": "FAIL",
                      "error_code": C.PHASE_FROZEN,
                      "errors": [{"code": C.PHASE_FROZEN, "location": "pool",
                                  "detail": "pool 已冻结（FROZEN 标记存在）：普通 receive / "
                                            "--supersede 均不得改变 active submission"}]},
            base=None, extension=None, batch=batch,
            package_sha256=sha256_file(archive) if archive.is_file() else "",
        )
        write_report(report, validation_dir)
        _append_attempt(pool_root, {"student_id": "", "phase": "", "sha256": "",
                                    "status": C.PHASE_FROZEN, "error_code": C.PHASE_FROZEN,
                                    "supersedes_sha256": None, "archive": archive.name})
        return {"status": "rejected", "exit_code": 1, "error_code": C.PHASE_FROZEN,
                "report_path": str(validation_dir / "validation_report.json"), **extra}

    # [1] Archive Filename Parse
    try:
        outer = parse_archive_name(archive.name)
    except IdentityError as exc:
        report = _combined_report(
            archive_name=archive.name, outer={"phase": "", "student_id": ""},
            identity={"validation_layer": C.LAYER_IDENTITY, "status": "FAIL",
                      "error_code": exc.code, "errors": [{"code": exc.code, "location": "archive-filename",
                                                           "detail": exc.detail}]},
            base=None, extension=None, batch=batch,
            package_sha256=sha256_file(archive) if archive.is_file() else "",
        )
        write_report(report, validation_dir)
        _append_attempt(pool_root, {"student_id": "", "phase": "", "sha256": "",
                                    "status": "REJECTED", "error_code": exc.code,
                                    "supersedes_sha256": None, "archive": archive.name})
        return {"status": "rejected", "exit_code": 1,
                "error_code": exc.code, "report_path": str(validation_dir / "validation_report.json"), **extra}

    package_sha256 = sha256_file(archive)

    # [2] Active 槽位探测（SHA 语义的 DUPLICATE_ARCHIVE，v10.1.12 §4）
    raw_path = pool_root / C.RAW / archive.name
    active_sha: str | None = sha256_file(raw_path) if raw_path.exists() else None
    if active_sha is not None and active_sha == package_sha256:
        # 完全相同的包（同 SHA）：真重复，拒绝接收
        report = _combined_report(
            archive_name=archive.name, outer=outer,
            identity={"validation_layer": C.LAYER_IDENTITY, "status": "FAIL",
                      "error_code": C.DUPLICATE_ARCHIVE,
                      "errors": [{"code": C.DUPLICATE_ARCHIVE, "location": "raw",
                                  "detail": f"raw/{archive.name} 已存在相同 SHA256 的 ACTIVE 包"
                                            f"（{active_sha[:12]}…），拒绝重复接收"}]},
            base=None, extension=None, batch=batch, package_sha256=package_sha256,
        )
        write_report(report, validation_dir)
        _append_attempt(pool_root, {"student_id": outer.get("student_id", ""),
                                    "phase": outer.get("phase", ""), "sha256": package_sha256,
                                    "status": C.DUPLICATE_ARCHIVE, "error_code": C.DUPLICATE_ARCHIVE,
                                    "supersedes_sha256": None, "archive": archive.name})
        return {"status": "rejected", "exit_code": 1, "error_code": C.DUPLICATE_ARCHIVE,
                "report_path": str(validation_dir / "validation_report.json"), **extra}
    # 同名不同 SHA = 新提交内容：继续走完整校验（不是 DUPLICATE_ARCHIVE）

    staging = Path(tempfile.mkdtemp(prefix="csa_submission_"))
    try:
        # [2] Safe Extract
        try:
            safe_extract(archive, staging)
        except SafeExtractError as exc:
            rejected_path = pool_root / C.HISTORY_REJECTED / _history_name(archive, package_sha256)
            shutil.copy2(archive, rejected_path)
            report = _combined_report(
                archive_name=archive.name, outer=outer,
                identity={"validation_layer": C.LAYER_SAFE_EXTRACT, "status": "FAIL",
                          "error_code": exc.code,
                          "errors": [{"code": exc.code, "location": "zip",
                                      "detail": exc.detail}]},
                base=None, extension=None, batch=batch, package_sha256=package_sha256,
            )
            write_report(report, validation_dir)
            _append_attempt(pool_root, {
                "student_id": outer.get("student_id", ""),
                "phase": outer.get("phase", ""), "sha256": package_sha256,
                "status": "REJECTED", "error_code": exc.code,
                "supersedes_sha256": None, "archive": archive.name,
                "saved_path": str(rejected_path),
            })
            return {"status": "rejected", "exit_code": 1, "error_code": exc.code,
                    "rejected_copy": str(rejected_path),
                    "report_path": str(validation_dir / "validation_report.json"), **extra}

        # [3] Identity Cross-Check
        inner = collect_inner_identity(staging)
        roster_students = load_roster(roster)
        identity = cross_check_identity(outer, inner, roster_students)

        # [4] Base Validation（Phase 无关）
        base = validate_base_submission(staging)

        # [5] Phase Extension Validation（复用同一份 Single-Package Validator；
        # archive_name 用实际上传文件名重查包名 ↔ manifest，v2 §8/§10）
        extension = (
            validate_submission(staging, outer["phase"], archive_name=archive.name)
            if base["status"] == "PASS" else None
        )

        # [6] Report（身份必须先于一切校验；FAIL 不写 canonical、不占 active 槽）
        report = _combined_report(
            archive_name=archive.name, outer=outer, identity=identity,
            base=base, extension=extension, batch=batch, package_sha256=package_sha256,
        )
        report_path = write_report(report, validation_dir)
        extra["report_path"] = str(report_path)

        if report["status"] != "PASS":
            # v10.1.12 §3：REJECTED 包不占 active 槽位 → history/rejected/<stem>__<sha8>
            rejected_path = pool_root / C.HISTORY_REJECTED / _history_name(archive, package_sha256)
            shutil.copy2(archive, rejected_path)
            _append_attempt(pool_root, {
                "student_id": report.get("student_id", ""),
                "phase": outer.get("phase", ""), "sha256": package_sha256,
                "status": "REJECTED", "error_code": report["error_code"],
                "supersedes_sha256": None, "archive": archive.name,
                "saved_path": str(rejected_path),
            })
            return {"status": "rejected", "exit_code": 1,
                    "error_code": report["error_code"],
                    "rejected_copy": str(rejected_path),
                    "report_path": extra["report_path"], **extra}

        # [6.5] ACTIVE 槽位裁决（v10.1.12 §5/§6）
        if active_sha is not None and not supersede:
            # 已有 ACTIVE、新包不同 SHA、未显式 --supersede：拒绝替换，旧 active 不变
            report = _combined_report(
                archive_name=archive.name, outer=outer, identity=identity,
                base=base, extension=extension, batch=batch, package_sha256=package_sha256,
            )
            report["status"] = "FAIL"
            report["error_code"] = C.ACTIVE_SUBMISSION_EXISTS
            report["error_count"] = report.get("error_count", 0) + 1
            report["errors"] = [*report.get("errors", []),
                                {"code": C.ACTIVE_SUBMISSION_EXISTS, "location": "raw",
                                 "detail": "raw 槽位已有 ACTIVE 包（不同 SHA256）；替换需显式 --supersede"}]
            extra["report_path"] = str(write_report(report, validation_dir))
            _append_attempt(pool_root, {
                "student_id": report.get("student_id", ""),
                "phase": outer.get("phase", ""), "sha256": package_sha256,
                "status": "REFUSED_SUPERSEDE", "error_code": C.ACTIVE_SUBMISSION_EXISTS,
                "supersedes_sha256": None, "archive": archive.name,
            })
            return {"status": "rejected", "exit_code": 1,
                    "error_code": C.ACTIVE_SUBMISSION_EXISTS,
                    "report_path": extra["report_path"], **extra}

        # ---- ACTIVE 槽位原子替换（先验证后替换；绝不"先删旧再验证"）----
        superseded_sha: str | None = None
        if active_sha is not None and supersede:
            superseded = pool_root / C.HISTORY_SUPERSEDED / _history_name(archive, active_sha)
            shutil.copy2(raw_path, superseded)
            superseded_sha = active_sha
        raw_tmp = raw_path.with_name(raw_path.name + ".incoming")
        shutil.copy2(archive, raw_tmp)
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        import os

        os.replace(raw_tmp, raw_path)  # 原子落位
        extra["raw_path"] = str(raw_path)
        _append_attempt(pool_root, {
            "student_id": identity["identity"]["outer_student_id"],
            "phase": outer.get("phase", ""), "sha256": package_sha256,
            "status": "ACTIVE", "error_code": None,
            "supersedes_sha256": superseded_sha, "archive": archive.name,
        })

        # [7] Server Canonical Build（身份校验通过后才允许）
        student_id = identity["identity"]["outer_student_id"]
        task_id = next(iter(inner["task_ids"]), "")
        task_dir = pool_root / C.SUBMISSIONS / student_id / C.phase_dir(outer["phase"]) / str(task_id or "task")
        if task_dir.exists():
            shutil.rmtree(task_dir)
        task_dir.mkdir(parents=True, exist_ok=True)

        for child in staging.iterdir():
            if child.name in ("validation", "registry", "assignment", "coverage"):
                continue
            if child.is_dir():
                shutil.copytree(child, task_dir / child.name, dirs_exist_ok=True)
            else:
                shutil.copy2(child, task_dir / child.name)

        # Server-owned 覆盖：manifest / agent_manifest / server_metadata
        server_manifest = _server_manifest(
            outer, task_id, student_id,
            archive_name=archive.name, package_sha256=package_sha256, batch=batch,
            base_checks=base,
        )
        (task_dir / C.MANIFEST).write_text(
            json.dumps(server_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (task_dir / C.AGENT_MANIFEST).write_text(
            json.dumps(_regenerate_agent_manifest(task_dir, student_id=student_id, task_id=task_id),
                       ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        server_metadata = _server_metadata_dict(
            archive_name=archive.name, student_id=student_id, task_id=task_id,
            phase=outer["phase"], batch=batch, package_sha256=package_sha256,
            canonical_path=str(task_dir),
        )
        (task_dir / SERVER_METADATA).write_text(
            json.dumps(server_metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        extra["canonical_path"] = str(task_dir)

        # [8] Operative mirror（现有教师流水线可消费的扁平副本）
        mirror = _write_operative_mirror(
            task_dir, student_id=student_id, phase=outer["phase"],
            canonical_root=mirror_root, checks=base,
        )
        extra["mirror_path"] = str(mirror)

        return {
            "status": "accepted",
            "exit_code": 0,
            "error_code": None,
            "report_path": extra["report_path"],
            "student_id": student_id,
            "task_id": task_id,
            "submission_phase": outer["phase"],
            "canonical_path": extra["canonical_path"],
            "raw_path": extra["raw_path"],
            **extra,
        }
    finally:
        shutil.rmtree(staging, ignore_errors=True)