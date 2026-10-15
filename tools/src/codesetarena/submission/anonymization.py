"""Anonymization —— canonical Task / Agent 的匿名分发视图（v2 §29–§32/§44）。

职责：

- ``build_anonymous_task_view``：``distributions/tasks/<ATxxx>/`` —— 只保留
  question / public data / public metadata / capability 信息；剥离 student_id、
  source task id、submission id、archive 文件名、private gold / reference、
  private mapping。
- ``build_anonymous_agent_view``：``distributions/agents/<AAxxx>/`` —— 只暴露
  课程允许学生看到的内容，metadata 不含任何身份。
- ``scan_for_identity_leaks``：自动匿名泄露扫描（§44）。针对 distributions/**
  扫描 student_id / source id / submission id / archive 名 / registry 路径等
  token，出现即测试 FAIL，不依赖人工检查。

匿名的含义是「学生不知道作者是谁」，不是「系统也不知道」：canonical ↔ 匿名
映射存放在私有 Registry（``registry/anonymous_mapping.jsonl``），管理端可逆推。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from . import contract as C

# 匿名分发必须剥离的 task.json 字段（身份 + private gold + 平台私有）
TASK_VIEW_STRIP_FIELDS = frozenset({
    "hypothesized_failure_mode",   # v10.1.18：作者预设假设属私有设计元数据
    "student_id",
    "student_number",
    "author_student_number",
    "task_id",
    "canonical_task_id",
    "source_task_id",
    "submission_id",
    "original_archive",
    "archive_sha256",
    "declared_goal_answer",
    "validated_goal_answer",
    "gold_answer",
    "gold",
    "expected_answer",
    "answer",
    "reference_solution",
    "reference",
    "evaluator",
    "evaluator_config",
    "private",
    "server_metadata",
    # ---- v10.1.11 Exposure Contract Fix #2 ----
    # 以下字段与 exposure.contract.FORBIDDEN_TASK_KEYS 对齐（子集契约：
    # FORBIDDEN_TASK_KEYS ⊆ TASK_VIEW_STRIP_FIELDS，由
    # tests/test_exposure_contract_fix.py::test_forbidden_keys_subset_of_strip_fields
    # 静态锁定）。语义区分：
    #   expected_failure_mode / author_expected = 教师侧期望标签（gold-like）→ 剥离；
    #   hypothesized_failure_mode = 作者 P1 公开的 Failure Hypothesis → 保留，不剥离。
    "author",
    "author_name",
    "author_id",
    "owner",
    "submitted_by",
    "declared_answer",
    "answer_key",
    "expected_failure_mode",
    "author_expected",
})

# canonical task.json 至少剥离学生身份（student_id 只允许存在于 Private Registry）
TASK_CANONICAL_STRIP_FIELDS = frozenset({
    "student_id",
    "student_number",
    "author_student_number",
    "submission_id",
    "original_archive",
    "archive_sha256",
})

# 匿名 Agent 目录不允许存在的文件 / 字段
AGENT_VIEW_FORBIDDEN_FILES = frozenset({"agent_manifest.json", "metadata.json"})
AGENT_METADATA_FORBIDDEN_FIELDS = frozenset({
    "student_id", "task_id", "source_agent_id", "source_task_id", "submission_id",
})

_SKIP_SCAN_SUFFIXES = (".pyc", ".gz", ".zip", ".tgz", ".whl")


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _closed_view(task: dict[str, Any], strip: frozenset[str]) -> dict[str, Any]:
    return {key: value for key, value in task.items() if key not in strip}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Anonymous Task View（§31）
# ---------------------------------------------------------------------------


def build_anonymous_task_view(
    canonical_task_dir: str | Path,
    anonymous_task_id: str,
    out_dir: str | Path,
) -> Path:
    """生成匿名 Task 分发视图。返回 out_dir。

    Canonical 中的 reference.py（私有 gold）与身份 / 私有字段绝不进入视图。
    """

    canonical = Path(canonical_task_dir)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    task_path = canonical / C.TASK_JSON
    if task_path.is_file():
        task = json.loads(task_path.read_text(encoding="utf-8"))
        view = _closed_view(task, TASK_VIEW_STRIP_FIELDS)
        view.pop("task_id", None)
        view["anonymous_task_id"] = anonymous_task_id
        write_json(out / C.TASK_JSON, view)

    data_dir = canonical / C.TASK_DATA_DIR
    if data_dir.is_dir():
        import shutil

        (out / C.TASK_DATA_DIR).mkdir(parents=True, exist_ok=True)
        for path in sorted(p for p in data_dir.iterdir() if p.is_file()):
            shutil.copy2(path, out / C.TASK_DATA_DIR / path.name)

    write_json(
        out / "metadata.json",
        {
            "anonymous_task_id": anonymous_task_id,
            "created_at": _now_iso(),
            "contract_version": C.SCHEMA_VERSION,
        },
    )
    return out


# ---------------------------------------------------------------------------
# Anonymous Agent View（§32）
# ---------------------------------------------------------------------------


def build_anonymous_agent_view(
    canonical_agent_dir: str | Path,
    anonymous_agent_id: str,
    out_dir: str | Path,
) -> Path:
    """生成匿名 Agent 分发视图：只含 agent 实现文件 + 匿名 metadata。"""

    import shutil

    canonical = Path(canonical_agent_dir)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    for path in sorted(p for p in canonical.iterdir() if p.is_file()):
        if path.name in AGENT_VIEW_FORBIDDEN_FILES:
            continue
        shutil.copy2(path, out / path.name)

    write_json(
        out / "metadata.json",
        {
            "anonymous_agent_id": anonymous_agent_id,
            "entrypoint": "agent.py",
            "created_at": _now_iso(),
            "contract_version": C.SCHEMA_VERSION,
        },
    )
    return out


# ---------------------------------------------------------------------------
# 匿名泄露自动扫描（§44）
# ---------------------------------------------------------------------------


def scan_for_identity_leaks(
    root: str | Path,
    secret_tokens: Iterable[str],
) -> list[dict[str, str]]:
    """扫描 root（通常是 distributions/**）中出现的任何身份 token。

    返回 ``[{"path", "token"}]``；空列表 = 无泄露。
    同时检查文件内容与文件名（“273001_agent.py” 同样泄露）。
    """

    tokens = sorted({str(token).strip() for token in secret_tokens if str(token).strip()})
    hits: list[dict[str, str]] = []
    root = Path(root)
    if not root.is_dir() or not tokens:
        return hits
    for path in sorted(root.rglob("*")):
        if path.name in tokens:
            hits.append({"path": str(path.relative_to(root)), "token": path.name})
        if not path.is_file() or path.suffix in _SKIP_SCAN_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for token in tokens:
            if token in text:
                hits.append({"path": str(path.relative_to(root)), "token": token})
    return hits


__all__ = [
    "AGENT_VIEW_FORBIDDEN_FILES",
    "TASK_CANONICAL_STRIP_FIELDS",
    "TASK_VIEW_STRIP_FIELDS",
    "build_anonymous_agent_view",
    "build_anonymous_task_view",
    "scan_for_identity_leaks",
]