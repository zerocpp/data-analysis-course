"""Private Submission Registry / Anonymous Mapping / Assignments（v2 §25–§29/§33）。

Repository 布局（v2 §20，适配当前工程）：

    course_repository/
    ├── received/                        原始 Submission Archive（审计原件，不可变）
    ├── tasks/<T000001>/                 独立 Canonical Task（§23，匿名化可分发）
    ├── agents/<A000001>/                独立 Canonical Agent（§24）
    ├── registry/
    │   ├── submission_registry.jsonl    唯一权威身份映射（§27，PRIVATE）
    │   ├── anonymous_mapping.jsonl       canonical ↔ 匿名 ID（§29，PRIVATE）
    │   └── assignments.jsonl             reviewer 匿名任务指派（§33，PRIVATE）
    ├── validation/                      batch / ingest 机器报告（§16）
    ├── distributions/tasks/<AT001>/    匿名 Task View（§31）
    ├── distributions/agents/<AA001>/   匿名 Agent View（§32）
    └── quarantine/                      未通过批次的隔离（§19）

ID 规则（§26/§36）：四种对象四套 ID，互不混用，且 canonical ID 不能由
student_id / source id 直接推断：

- ``submission_id`` : S000001...
- canonical ``task_id`` : T000001...
- canonical ``agent_id`` : A000001...
- ``anonymous_task_id`` : AT001... / ``anonymous_agent_id`` : AA001...

Registry 是**私有数据**：student_id ↔ submission_id ↔ task_id ↔ agent_id 的
映射只存在于 ``registry/`` 下；Task / Agent / 匿名分发对象一律不带 student_id。

全部写盘原子化（temp + ``os.replace``）；同一 archive 重复 ingest 由
``lookup_archive_sha256`` 幂等拦截（§35）。
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_SUBDIRS = (
    "received", "tasks", "agents", "registry", "validation", "distributions",
    "assignments", "quarantine",
)
SUBMISSION_REGISTRY = "submission_registry.jsonl"
ANONYMOUS_MAPPING = "anonymous_mapping.jsonl"
ASSIGNMENTS = "assignments.jsonl"

_JSONL_ENCODING = "utf-8"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def ensure_repo(repo_root: str | Path) -> Path:
    """创建 repository 标准目录并返回根路径。"""

    root = Path(repo_root)
    for name in REPO_SUBDIRS:
        (root / name).mkdir(parents=True, exist_ok=True)
    (root / "distributions" / "tasks").mkdir(parents=True, exist_ok=True)
    (root / "distributions" / "agents").mkdir(parents=True, exist_ok=True)
    (root / "quarantine" / "single_validation_failed").mkdir(parents=True, exist_ok=True)
    (root / "quarantine" / "cross_package_conflict").mkdir(parents=True, exist_ok=True)
    return root


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding=_JSONL_ENCODING).splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _write_jsonl_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=_JSONL_ENCODING) as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class SubmissionRegistry:
    """Private Registry（v2 §27）：行即 JSONL 记录；commit 前全部在内存中。"""

    def __init__(self, repo_root: str | Path):
        self.root = Path(repo_root)
        registry_dir = self.root / "registry"
        self.submission_path = registry_dir / SUBMISSION_REGISTRY
        self.mapping_path = registry_dir / ANONYMOUS_MAPPING
        self.assignment_path = registry_dir / ASSIGNMENTS
        self.rows: list[dict[str, Any]] = _read_jsonl(self.submission_path)
        self.mappings: list[dict[str, Any]] = _read_jsonl(self.mapping_path)
        self.assignments: list[dict[str, Any]] = _read_jsonl(self.assignment_path)
        self._start_row_count = len(self.rows)
        self._start_mapping_count = len(self.mappings)

    # ---- lookup（幂等判断，§35）----

    def lookup_archive_sha256(self, sha256: str) -> dict[str, Any] | None:
        for row in self.rows:
            if row.get("archive_sha256") == sha256:
                return row
        return None

    def lookup_submission(
        self, *, student_id: str, phase: str, source_task_id: str
    ) -> dict[str, Any] | None:
        for row in self.rows:
            if (row.get("student_id") == student_id
                    and row.get("phase") == phase
                    and row.get("source_task_id") == source_task_id):
                return row
        return None

    # ---- ID 分配（§36：与 student_id 无对应关系）----

    @staticmethod
    def _next_id(rows: list[dict[str, Any]], key: str, prefix: str, width: int) -> str:
        maximum = 0
        for row in rows:
            value = str(row.get(key) or "")
            if value.startswith(prefix) and value[len(prefix):].isdigit():
                maximum = max(maximum, int(value[len(prefix):]))
        return f"{prefix}{maximum + 1:0{width}d}"

    def next_submission_id(self) -> str:
        return self._next_id(self.rows, "submission_id", "S", 6)

    def next_task_id(self) -> str:
        return self._next_id(self.rows, "task_id", "T", 6)

    def next_agent_id(self) -> str:
        return self._next_id(self.rows, "agent_id", "A", 6)

    def next_anonymous_task_id(self) -> str:
        return self._next_id(self.mappings, "anonymous_task_id", "AT", 3)

    def next_anonymous_agent_id(self) -> str:
        return self._next_id(self.mappings, "anonymous_agent_id", "AA", 3)

    def find_anonymous_task(self, task_id: str) -> dict[str, Any] | None:
        for mapping in self.mappings:
            if mapping.get("task_id") == task_id:
                return mapping
        return None

    def find_canonical_task(self, anonymous_task_id: str) -> dict[str, Any] | None:
        for mapping in self.mappings:
            if mapping.get("anonymous_task_id") == anonymous_task_id:
                return mapping
        return None

    def find_canonical_agent(self, anonymous_agent_id: str) -> dict[str, Any] | None:
        for mapping in self.mappings:
            if mapping.get("anonymous_agent_id") == anonymous_agent_id:
                return mapping
        return None

    # ---- 写（§34：全部步骤成功后再 commit；失败时调用方回滚，这里不落盘）----

    def add_submission(self, entry: dict[str, Any]) -> None:
        self.rows.append(entry)

    def add_mapping(self, entry: dict[str, Any]) -> None:
        self.mappings.append(entry)

    @property
    def changed(self) -> bool:
        return (len(self.rows) != self._start_row_count
                or len(self.mappings) != self._start_mapping_count)

    def commit(self) -> None:
        """原子落盘 registry 三个文件（只有全部改动成功才算 ingest 成功）。"""

        _write_jsonl_atomic(self.submission_path, self.rows)
        _write_jsonl_atomic(self.mapping_path, self.mappings)
        _write_jsonl_atomic(self.assignment_path, self.assignments)


def write_assignments(repo_root: str | Path, entries: list[dict[str, Any]]) -> Path:
    """初始化 / 覆盖 assignment 映射（§33；私有）。"""

    root = Path(repo_root)
    path = root / "registry" / ASSIGNMENTS
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_jsonl_atomic(path, entries)
    return path


__all__ = [
    "ANONYMOUS_MAPPING",
    "ASSIGNMENTS",
    "REPO_SUBDIRS",
    "SUBMISSION_REGISTRY",
    "SubmissionRegistry",
    "ensure_repo",
    "now_iso",
    "write_assignments",
]