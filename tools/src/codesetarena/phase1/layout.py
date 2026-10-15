"""Phase 1 pool 目录布局常量。

pool root 下固定子目录（与 §4/§34 建议一致，但脚本参数允许覆盖路径）::

    <pool>/
    ├── raw/          原始学生提交（不可变证据，禁止任何修改）
    ├── canonical/    人工确认 / Build 后的正式运行副本（operative copy）
    ├── validation/   validation_report.json
    ├── registry/     phase1_task_registry.csv / task_to_owner.json / student_to_tasks.json
    ├── coverage/     FM distribution（main 与 current 快照）
    ├── assignment/   main_assignment.json / reviewer_to_tasks.json / task_to_reviewers.json ...
    └── audit/        task_id_corrections.jsonl
"""
from __future__ import annotations

from pathlib import Path

RAW = "raw"
CANONICAL = "canonical"
VALIDATION = "validation"
REGISTRY = "registry"
COVERAGE = "coverage"
ASSIGNMENT = "assignment"
AUDIT = "audit"

SUBDIRS = (RAW, CANONICAL, VALIDATION, REGISTRY, COVERAGE, ASSIGNMENT, AUDIT)

# 默认 pool 根：项目根 `phase1/`（CSA_ROOT 解析规则与 policy 一致，见 AGENTS.md）。
DEFAULT_POOL_ROOT_NAME = "phase1"


def ensure_pool_layout(pool_root: str | Path) -> Path:
    """创建（如不存在）标准子目录并返回 pool 根 Path。"""

    root = Path(pool_root)
    for name in SUBDIRS:
        (root / name).mkdir(parents=True, exist_ok=True)
    return root