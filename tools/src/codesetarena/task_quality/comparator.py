"""三方对账（§13/§14/§15）与第二阶段 Semantic Adjudication（§16/§17）。

对账**复用生产 comparator** `main.runner._answers_equal`（§14 禁止第二套判等）；
adjudicator 是 Blind 完成之后的 SECOND PASS，此时才允许看到双方 reference 与答案。
"""
from __future__ import annotations

import json
from typing import Callable

from ..main.runner import _answers_equal  # 生产 comparator 唯一来源（禁止第二套）
from .models import (
    ADJ_CATEGORIES_FAIL,
    ADJ_UNRESOLVED,
    GateConfig,
)

__all__ = ["answers_equal", "run_adjudication", "AdjudicationError"]

answers_equal = _answers_equal


def _build_adjudication_prompt(
    *,
    question: str,
    data_summary: str,
    declared_answer: str,
    student_reference_source: str,
    student_reference_result: str,
    independent_reference_source: str,
    independent_reference_result: str,
    blind_interpretation: str,
) -> str:
    system = (
        "You are the disagreement adjudicator of a task quality gate.\n"
        "The blind independent solver has ALREADY finished; now you may see both "
        "reference implementations, the declared answer and both execution results.\n"
        "Your job is NOT to re-solve the task blindly. Explain why the two reference "
        "implementations disagree on the same data.\n"
        "Return a single JSON object exactly:\n"
        "{\n"
        '  "category": "likely_student_reference_bug" | "likely_independent_solver_bug"'
        ' | "ambiguous_question" | "insufficient_data" | "answer_bug" | "unresolved",\n'
        '  "confidence": 0.0,\n'
        '  "explanation": "...",\n'
        '  "evidence": ["..."]\n'
        "}\n"
        "Return ONLY the JSON object."
    )
    payload = {
        "question": question,
        "data_summary": data_summary,
        "declared_answer": declared_answer,
        "student_reference_source": student_reference_source,
        "student_reference_result": student_reference_result,
        "independent_reference_source": independent_reference_source,
        "independent_reference_result": independent_reference_result,
        "blind_interpretation": blind_interpretation,
    }
    return system + "\n\nEVIDENCE_JSON:\n" + json.dumps(payload, ensure_ascii=False, indent=2)


class AdjudicationError(RuntimeError):
    """Adjudicator 结构化输出不可用（归 Gate 基础设施 → REVIEW，不归罪学生）。"""


def run_adjudication(
    *,
    question: str,
    data_summary: str,
    declared_answer: str,
    student_reference_source: str,
    student_reference_result: str,
    independent_reference_source: str,
    independent_reference_result: str,
    blind_interpretation: str,
    llm: Callable[[str], str],
    config: GateConfig,
) -> dict:
    prompt = _build_adjudication_prompt(
        question=question,
        data_summary=data_summary,
        declared_answer=declared_answer,
        student_reference_source=student_reference_source,
        student_reference_result=student_reference_result,
        independent_reference_source=independent_reference_source,
        independent_reference_result=independent_reference_result,
        blind_interpretation=blind_interpretation,
    )
    content = llm(prompt)
    cleaned = content.strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise AdjudicationError("adjudicator response is not a JSON object")
    try:
        data = json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError as exc:
        raise AdjudicationError(f"adjudicator JSON invalid: {exc}") from exc
    category = str(data.get("category") or ADJ_UNRESOLVED).strip()
    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    return {
        "status": "OK",
        "category": category,
        "is_fail_category": category in ADJ_CATEGORIES_FAIL,
        "confidence": confidence,
        "explanation": str(data.get("explanation") or ""),
        "evidence": list(data.get("evidence") or []),
        "prompt_version": config.adjudicator_prompt_version,
    }
