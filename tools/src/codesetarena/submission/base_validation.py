"""validate_base_submission —— 唯一一份 Base Submission Validation。

Phase 1 与 Phase 2 共用；任何 Phase 的 Task / Agent / Identity 公共规则只允许
在这里维护一次。禁止再写 validate_phase1 / validate_phase2 复制公共逻辑。

校验范围（全部静态、严格只读、不写文件）：

- manifest.json 存在 / JSON 对象 / schema_version / submission_phase；
- Identity（manifest / task.json / agent_manifest 的 student_id、task_id 逐处一致）；
- task/task.json 存在、JSON、FM 合法性（hypothesized_failure_mode 若出现必须属于
  正式 14 类 taxonomy，服务器只校验、不重新分类）；
- task/data/ 至少一个数据文件且不超过 policy 上限；
- agent/agent_manifest.json + agent/agent.py 存在且身份一致；
- 预留服务器路径检查（学生伪装的 server artifact 一律拒绝使用）。

返回统一的 Validation Result：:

    {
      "schema_version": ...,
      "status": "PASS" | "FAIL",
      "validation_layer": "base",
      "error_code": "...",          # 第一个错误码；PASS 时为 None
      "error_count": int,
      "errors": [{"code", "location", "detail"}],
      "checks": {"submission_phase", "student_id", "task_id", "files": [...]},
    }
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..constants import HYPOTHESIZED_FM_CODES, HYPOTHESIZED_FM_ORDER
from ..policy_loader import runtime_policy
from . import contract as C

DATA_MAX_BYTES_DEFAULT = 1024 * 1024
DATA_MAX_ROWS_DEFAULT = 10000


def _data_limits() -> tuple[int, int]:
    data_cfg = runtime_policy().get("task_data") or {}
    return (
        int(data_cfg.get("max_size_bytes") or DATA_MAX_BYTES_DEFAULT),
        int(data_cfg.get("max_rows") or DATA_MAX_ROWS_DEFAULT),
    )


def _json_object(path: Path) -> dict[str, Any] | None:
    """读 JSON；返回 dict 或 None（不可解析 / 非对象）。"""

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _str_field(task: dict[str, Any], key: str) -> str:
    return str(task.get(key) or "").strip()


def _reserved_path_violations(root: Path) -> list[str]:
    """扫描提取根下的预留服务器路径（root 一级的目录名 / 文件名）。"""

    violations: list[str] = []
    for child in sorted(root.iterdir()):
        if child.is_dir():
            if (child.name + "/") in C.RESERVED_SERVER_PATHS or child.name in (
                p.rstrip("/") for p in C.RESERVED_SERVER_PATHS
            ):
                violations.append(child.name + "/")
        elif child.name in C.RESERVED_SERVER_PATHS:
            violations.append(child.name)
    return violations


def _secret_guard_violations(root: Path) -> list[str]:
    """Package Secret Guard（v10.1.10 §29）：代码级强校验。

    两层：

    1. **文件名层**：对**任意相对路径**（不只顶层）做 fnmatch 大小写不敏感匹配；
    2. **内容层**：对小体积文本文件扫描高置信度 key 模式（sk-... / 赋值的
       LLM_API_KEY / PEM 私钥头）——不管文件叫什么名字。

    命中即 ERROR SECRET_FILE。.env.example 由 DEV_TEMPLATE_FILE 警告通道单独
    处理，不在此拦（其连接字段为空，也不会命中内容层）。
    """

    import fnmatch

    violations: list[str] = []
    content_patterns = (
        "sk-[A-Za-z0-9_-]{12,}",
        "LLM_API_KEY=..",
        "-----BEGIN ",
    )
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        name = path.name.lower()
        if rel == C.ENV_EXAMPLE:
            continue
        name_hit = any(fnmatch.fnmatch(name, pat.lower()) for pat in C.SECRET_GUARD_PATTERNS)
        content_hit = False
        if not name_hit and path.stat().st_size <= 256 * 1024:
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                text = ""
            if text and any(__import__("re").search(p, text) for p in content_patterns):
                content_hit = True
        if name_hit or content_hit:
            violations.append(rel)
    return violations


def _submission_boundary_violations(root: Path) -> tuple[list[str], list[str]]:
    """Submission 边界检查（Configuration / Policy Separation）。

    返回 ``(forbidden_errors, warnings)``：

    - ERROR：.env（真实 Secret）与 runtime_policy.json（课程系统配置）禁止
      进入学生提交包（§15/§37）；
    - ERROR：runs/ / __pycache__/ / .git/ / *.pyc / .pytest_cache/；
    - WARNING：.env.example（连接配置模板，Build 已自动排除；legacy corpus
      兼容期内手工加入只警告，不阻断）。
    """

    forbidden: list[str] = []
    warnings: list[str] = []
    for child in sorted(root.iterdir()):
        name = child.name
        if name == C.ENV_EXAMPLE:
            warnings.append(name)
            continue
        if name in C.FORBIDDEN_SUBMISSION_FILES:
            forbidden.append(name)
            continue
        if child.is_dir():
            if (name + "/") in C.FORBIDDEN_TOP_LEVEL or name in (
                p.rstrip("/") for p in C.FORBIDDEN_TOP_LEVEL
            ):
                forbidden.append(name + "/")
        elif name.endswith(".pyc"):
            forbidden.append(name)
    return forbidden, warnings


def _check_identity_block(
    errors: list[dict[str, Any]],
    *,
    manifest: dict[str, Any] | None,
    task: dict[str, Any] | None,
    agent_manifest: dict[str, Any] | None,
) -> dict[str, str]:
    """manifest / task.json / agent_manifest 三处 student_id、task_id 一致性。"""

    locations: dict[str, dict[str, str]] = {}
    if manifest is not None:
        locations["manifest.json"] = {
            "student_id": _str_field(manifest, "student_id"),
            "task_id": _str_field(manifest, "task_id"),
        }
    if task is not None:
        locations["task/task.json"] = {
            "student_id": _str_field(task, "student_id"),
            "task_id": _str_field(task, "task_id"),
        }
    if agent_manifest is not None:
        locations["agent/agent_manifest.json"] = {
            "student_id": _str_field(agent_manifest, "student_id"),
            "task_id": _str_field(agent_manifest, "task_id"),
        }

    declared: dict[str, str] = {}
    for field in ("student_id", "task_id"):
        for location, values in locations.items():
            value = values[field]
            if not value:
                continue
            if field not in declared:
                declared[field] = value
            elif declared[field] != value:
                errors.append(
                    {
                        "code": C.STUDENT_ID_MISMATCH if field == "student_id" else C.TASK_ID_MISMATCH,
                        "location": location,
                        "detail": (
                            f"{field} 在 {location}={value!r} 与 "
                            f"{list(locations)[0]}={declared[field]!r} 不一致"
                        ),
                    }
                )
    return declared


def validate_base_submission(root: str | Path) -> dict[str, Any]:
    """Base Submission Validation（Phase 无关）。返回统一 Validation Result。"""

    root = Path(root)
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    checks: dict[str, Any] = {"submission_phase": None, "student_id": "", "task_id": "",
                              "files": []}

    def fail(code: str, location: str, detail: str) -> None:
        errors.append({"code": code, "location": location, "detail": detail})

    # ---- manifest ----
    manifest_path = root / C.MANIFEST
    if not manifest_path.is_file():
        fail(C.MISSING_MANIFEST, C.MANIFEST, "缺少 manifest.json")
        manifest = None
    else:
        manifest = _json_object(manifest_path)
        if manifest is None:
            fail(C.MALFORMED_JSON, C.MANIFEST, "manifest.json 不是合法 JSON 对象")
            manifest = None
        elif manifest.get("schema_version") != C.SCHEMA_VERSION:
            fail(C.BAD_MANIFEST, C.MANIFEST, "manifest.schema_version 不匹配")
        else:
            phase = str(manifest.get("submission_phase") or "").strip()
            if not phase:
                fail(C.BAD_MANIFEST, C.MANIFEST, "manifest 缺少 submission_phase")
            elif phase not in C.VALID_PHASES:
                fail(C.INVALID_SUBMISSION_PHASE, C.MANIFEST,
                     f"manifest.submission_phase={phase!r} 不是合法阶段")
            else:
                checks["submission_phase"] = phase
            student_id = _str_field(manifest, "student_id")
            if not student_id:
                fail(C.MISSING_STUDENT_ID, C.MANIFEST, "manifest 缺少 student_id")
            elif not C.STUDENT_ID_PATTERN.match(student_id):
                fail(C.INVALID_STUDENT_ID, C.MANIFEST, f"manifest.student_id={student_id!r} 格式非法")
            else:
                checks["student_id"] = student_id
            task_id = _str_field(manifest, "task_id")
            if not task_id:
                fail(C.MISSING_TASK_ID, C.MANIFEST, "manifest 缺少 task_id")
            elif not C.TASK_ID_PATTERN.match(task_id):
                fail(C.INVALID_TASK_ID, C.MANIFEST, f"manifest.task_id={task_id!r} 格式非法")
            else:
                checks["task_id"] = task_id

    # ---- task/task.json ----
    task: dict[str, Any] | None = None
    task_path = root / C.TASK_JSON
    if not task_path.is_file():
        fail(C.MISSING_TASK_JSON, C.TASK_JSON, "缺少 task/task.json")
    else:
        task = _json_object(task_path)
        if task is None:
            fail(C.MALFORMED_JSON, C.TASK_JSON, "task/task.json 不是合法 JSON 对象")
        else:
            fm = _str_field(task, "hypothesized_failure_mode")
            checks["hypothesized_failure_mode"] = fm
            if fm and fm not in HYPOTHESIZED_FM_CODES:
                fail(
                    C.INVALID_FM, C.TASK_JSON,
                    f"hypothesized_failure_mode={fm!r} 不是正式 Failure Mode"
                    f"（只能取 " + "/".join(HYPOTHESIZED_FM_ORDER) + "）",
                )

    # ---- task/data ----
    data_dir = root / C.TASK_DATA_DIR
    data_files = sorted(p for p in data_dir.iterdir() if p.is_file()) if data_dir.is_dir() else []
    if not data_dir.is_dir() or not data_files:
        fail(C.MISSING_TASK_DATA, C.TASK_DATA_DIR, "task/data/ 至少需要一个数据文件")
    else:
        max_bytes, _ = _data_limits()
        for path in data_files:
            size = path.stat().st_size
            if size > max_bytes:
                fail(C.DATA_TOO_LARGE, C.TASK_DATA_DIR + "/" + path.name,
                     f"数据文件 {path.name} 大小 {size} B 超过上限 {max_bytes} B")
        checks["files"] = [C.TASK_DATA_DIR + "/" + p.name for p in data_files]

    # ---- agent ----
    agent_manifest: dict[str, Any] | None = None
    am_path = root / C.AGENT_MANIFEST
    if not am_path.is_file():
        fail(C.MISSING_AGENT_MANIFEST, C.AGENT_MANIFEST, "缺少 agent/agent_manifest.json")
    else:
        agent_manifest = _json_object(am_path)
        if agent_manifest is None:
            fail(C.BAD_AGENT_MANIFEST, C.AGENT_MANIFEST, "agent/agent_manifest.json 不是合法 JSON 对象")
    if not (root / C.AGENT_ENTRYPOINT).is_file():
        fail(C.MISSING_AGENT, C.AGENT_ENTRYPOINT, "缺少 agent/agent.py")

    # ---- 三处身份一致性 ----
    declared = _check_identity_block(errors, manifest=manifest, task=task,
                                     agent_manifest=agent_manifest)
    if declared.get("student_id"):
        checks["student_id"] = declared["student_id"]
    if declared.get("task_id"):
        checks["task_id"] = declared["task_id"]

    # ---- 预留服务器路径 ----
    for violation in _reserved_path_violations(root):
        fail(C.RESERVED_SERVER_PATH, violation,
             "学生包中出现服务器预留路径，服务器绝不使用其中内容（IGNORE+REGENERATE）")

    # ---- Submission 边界（Configuration / Policy Separation）----
    forbidden, dev_templates = _submission_boundary_violations(root)
    for name in forbidden:
        fail(
            C.FORBIDDEN_FILE, name,
            f"禁止文件进入提交包：{name}（.env / runtime_policy.json / runs/ / "
            f"__pycache__/ / .git/ 等只属于开发环境或课程系统，不属于 Submission）",
        )
    for rel in _secret_guard_violations(root):
        fail(
            C.SECRET_FILE, rel,
            f"疑似 Secret 文件禁止进入提交包：{rel}（Package Secret Guard："
            f".env / *.key / credentials / secret 等）",
        )
    for name in dev_templates:
        warnings.append(
            {
                "code": C.DEV_TEMPLATE_FILE,
                "location": name,
                "detail": (".env.example 属于开发环境连接配置模板，不属于 Submission"
                           "（Build 已自动排除；本包为手工加入，请重新 Build）"),
            }
        )

    status = "FAIL" if errors else "PASS"
    return {
        "schema_version": C.SCHEMA_VERSION,
        "status": status,
        "validation_layer": C.LAYER_BASE,
        "error_code": errors[0]["code"] if errors else None,
        "error_count": len(errors),
        "errors": errors,
        "warnings": warnings,
        "checks": checks,
    }