"""Task Quality Gate 的常量与轻量数据结构（无业务逻辑）。"""
from __future__ import annotations

from dataclasses import dataclass, field

GATE_VERSION = "task_quality_gate_v1"
PROMPT_VERSION = "task_quality_blind_solver_v1"
ADJUDICATOR_PROMPT_VERSION = "task_quality_adjudicator_v1"

GATE_INFRASTRUCTURE_FAILURE = "GATE_INFRASTRUCTURE_FAILURE"

# Blind Solver 结构化状态（§8）
SOLVER_SOLVED = "SOLVED"
SOLVER_AMBIGUOUS = "AMBIGUOUS"
SOLVER_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
SOLVER_UNSOLVABLE = "UNSOLVABLE"
SOLVER_INTERNAL_ERROR = "INTERNAL_ERROR"
SOLVER_STATUSES = (
    SOLVER_SOLVED,
    SOLVER_AMBIGUOUS,
    SOLVER_INSUFFICIENT_DATA,
    SOLVER_UNSOLVABLE,
    SOLVER_INTERNAL_ERROR,
)

# Gate 终态（§19，只有三种）
STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_REVIEW = "REVIEW"

# Adjudicator 类别（§16）
ADJ_STUDENT_REF_BUG = "likely_student_reference_bug"
ADJ_INDEPENDENT_BUG = "likely_independent_solver_bug"
ADJ_AMBIGUOUS_QUESTION = "ambiguous_question"
ADJ_INSUFFICIENT_DATA = "insufficient_data"
ADJ_ANSWER_BUG = "answer_bug"
ADJ_UNRESOLVED = "unresolved"

# 直接判 FAIL 的 adjudication 类别（硬证据方向）；其余进 REVIEW
ADJ_CATEGORIES_FAIL = (ADJ_STUDENT_REF_BUG, ADJ_ANSWER_BUG, ADJ_INSUFFICIENT_DATA)

# Blind Sandbox 中禁止出现的文件名（§11）
SANDBOX_FORBIDDEN_FILES = (
    "reference.py",
    "answer.json",
    "answer.txt",
    "result.json",
    "gold.json",
    ".env",
)


@dataclass
class GateConfig:
    """Gate 运行配置；全部有默认值，不引入新 policy 文件。"""

    gate_version: str = GATE_VERSION
    prompt_version: str = PROMPT_VERSION
    adjudicator_prompt_version: str = ADJUDICATOR_PROMPT_VERSION
    max_generation_attempts: int = 2  # §29：有限自动修复，禁止无限循环
    reference_timeout_seconds: float | None = None  # None ⇒ 复用 runtime_policy
    max_reference_source_lines: int = 200  # 与 validation 默认一致


@dataclass
class BlindTaskView:
    """独立求解唯一可见的数据结构（§3）。

    允许字段：task_id（不含答案语义）、题面、数据 schema/样本、answer_type。
    禁止字段：answer / reference 源码 / reference 执行结果 / 任何 gold 语义
    —— build 阶段即不读取这些来源，Solver 函数签名层面也不接受它们。
    """

    task_id: str
    description: str
    task_statement: str
    answer_type: str
    data_files: list = field(default_factory=list)  # [{name, sha256, rows, columns, dtypes, preview_rows}]

    def to_payload(self) -> dict:
        return {
            "task_id": self.task_id,
            "description": self.description,
            "task_statement": self.task_statement,
            "answer_type": self.answer_type,
            "data_files": self.data_files,
        }
