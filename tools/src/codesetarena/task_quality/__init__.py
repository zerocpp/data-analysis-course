"""Task Quality Gate —— 进入评测/曝光前的任务质量门禁（Blind Independent Reference）。

目标：学生不能通过「reference.py 与声明答案一起写错但彼此一致」骗过质量检查。

核心机制（Blind Boundary，代码级隔离而非 prompt 约定）：

    Full Task（含 reference.py / declared_answer / 执行结果）
        ↓  build_blind_task_view()
    BlindTaskView（只有题面 + 数据 schema/样本）
        ↓  audit_leaks()（对**实际序列化 prompt**做哨兵审计，fail-closed）
    Blind Independent Solver（task_quality_blind_solver_v1）
        ↓  独立生成 independent_reference.py
    受控 Sandbox 真实执行（隔离目录 / 超时 / 无密钥环境 / 文件系统审计）
        ↓
    Independent Result
        ↓  【Blind 阶段到此结束，之后才允许读取学生材料】
    三方交叉核验（复用生产 comparator `_answers_equal`，禁止第二套判等）
        ↓  冲突时
    Adjudication（第二阶段，此时才允许看双方 reference 与答案）
        ↓
    PASS / FAIL / REVIEW + student_feedback + task_quality_report.json

原则（§28/§44/§9）：
- LLM 基础设施失败 / 生成的代码跑不起来 ⇒ GATE_INFRASTRUCTURE / REVIEW，
  绝不计为学生任务 FAIL；
- 硬证据（reference crash、reference≠声明答案）直接 FAIL；
- LLM-only 分歧先 REVIEW + adjudication，避免误杀。
"""
from .gate import GateConfig, run_task_quality_gate
from .blind_view import audit_leaks, audit_sandbox_files, build_blind_task_view
from .models import GATE_INFRASTRUCTURE_FAILURE, GATE_VERSION, PROMPT_VERSION

__all__ = [
    "GATE_VERSION",
    "PROMPT_VERSION",
    "GATE_INFRASTRUCTURE_FAILURE",
    "GateConfig",
    "run_task_quality_gate",
    "build_blind_task_view",
    "audit_leaks",
    "audit_sandbox_files",
]
