"""评测结果映射 —— 现有 Outcome Model → 评测汇总词汇（唯一映射层）。

本模块**不做**任何新的失败判定：只把 ``main.outcome`` / ``result.json`` 里
已经归类的字段翻译成汇总表使用的 ``final_outcome`` 词汇：

    completed            -> correct
    analysis_failure     -> wrong_answer
    internal_error:
      llm_api            -> api_error
      token_budget       -> token_budget_exceeded
      resource_limit:time    -> timeout
      resource_limit:memory/disk -> runtime_error（资源 kill，error_category 保留细分）
      runtime_crash      -> runtime_error
      reference_error    -> unevaluated（教师侧 gold 失败，不是 Agent 错误）

Internal Error 绝不与 wrong_answer 混同：未产生有效评价的执行一律落
``unevaluated``（``correct`` 列留空），保证汇总统计不失真。
"""

from __future__ import annotations

from typing import Any

from ..constants import (
    INTERNAL_LLM_API,
    INTERNAL_REFERENCE_ERROR,
    INTERNAL_RESOURCE_LIMIT,
    INTERNAL_RUNTIME_CRASH,
    INTERNAL_TOKEN_BUDGET,
    OUTCOME_ANALYSIS_FAILURE,
    OUTCOME_COMPLETED,
    OUTCOME_INTERNAL_ERROR,
    RESOURCE_KIND_TIME,
)

FINAL_OUTCOME_CORRECT = "correct"
FINAL_OUTCOME_WRONG = "wrong_answer"
FINAL_OUTCOME_TIMEOUT = "timeout"
FINAL_OUTCOME_TOKEN_BUDGET = "token_budget_exceeded"
FINAL_OUTCOME_API_ERROR = "api_error"
FINAL_OUTCOME_RUNTIME_ERROR = "runtime_error"
FINAL_OUTCOME_UNEVALUATED = "unevaluated"

FINAL_OUTCOME_VALUES = (
    FINAL_OUTCOME_CORRECT,
    FINAL_OUTCOME_WRONG,
    FINAL_OUTCOME_TIMEOUT,
    FINAL_OUTCOME_TOKEN_BUDGET,
    FINAL_OUTCOME_API_ERROR,
    FINAL_OUTCOME_RUNTIME_ERROR,
    FINAL_OUTCOME_UNEVALUATED,
)


def summarize_outcome(record: dict[str, Any]) -> tuple[str, str]:
    """runtime ``result.json`` 记录 → ``(final_outcome, error_category)``。

    ``error_category`` 保留 runtime 细分（如 ``resource_limit:time``）；
    正常完成时为空字符串。
    """

    outcome_type = str(record.get("outcome_type") or "")
    category = record.get("internal_error_category")
    resource_kind = record.get("resource_kind")
    if outcome_type == OUTCOME_COMPLETED:
        return FINAL_OUTCOME_CORRECT, ""
    if outcome_type == OUTCOME_ANALYSIS_FAILURE:
        return FINAL_OUTCOME_WRONG, ""
    if outcome_type == OUTCOME_INTERNAL_ERROR:
        if category == INTERNAL_LLM_API:
            return FINAL_OUTCOME_API_ERROR, str(category)
        if category == INTERNAL_TOKEN_BUDGET:
            return FINAL_OUTCOME_TOKEN_BUDGET, str(category)
        if category == INTERNAL_RESOURCE_LIMIT:
            kind = str(resource_kind or "")
            if kind == RESOURCE_KIND_TIME:
                return FINAL_OUTCOME_TIMEOUT, f"{INTERNAL_RESOURCE_LIMIT}:{kind}"
            return FINAL_OUTCOME_RUNTIME_ERROR, (
                f"{INTERNAL_RESOURCE_LIMIT}:{kind}" if kind else str(category)
            )
        if category == INTERNAL_RUNTIME_CRASH:
            return FINAL_OUTCOME_RUNTIME_ERROR, str(category)
        if category == INTERNAL_REFERENCE_ERROR:
            return FINAL_OUTCOME_UNEVALUATED, str(category)
    return FINAL_OUTCOME_UNEVALUATED, str(category or "unknown_outcome")
