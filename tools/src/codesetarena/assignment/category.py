"""Task Category Schema（生产 Assignment 的正式 14 类词汇）。

课程正式 taxonomy = Phase 1 ``hypothesized_failure_mode``：14 个 Failure Mode
（``constants.HYPOTHESIZED_FM_ORDER`` = FM01..FM14 完整 slug 键，v2.0）。

Solver 内部把 canonical category 映射为稳定整数索引 **1..14**（与
Benchmark Case 的 category universe 语义一致）；映射表在此集中定义，
禁止在执行路径中硬编码 ``range(1, 15)`` 之外的类别词汇。

category 只读取 canonical Task 元数据（ingest 时已固化），Assignment 阶段
绝不再用 LLM 推断 / 随机补 / 默认填充。
"""

from __future__ import annotations

from codesetarena import constants as K

# canonical category code（正式 slug 键：FM01_REQUIREMENT ... FM14_PROMPT_INJECTION）
CATEGORY_CODES: tuple[str, ...] = tuple(K.HYPOTHESIZED_FM_ORDER)
CATEGORY_INDEX_BY_CODE: dict[str, int] = {
    code: idx + 1 for idx, code in enumerate(CATEGORY_CODES)
}
# 兼容两位码（FM01..FM14）归一化
_TWO_LETTER_INDEX = {code: idx + 1 for idx, (code, _) in enumerate(K.FAILURE_MODE_OPTIONS)}
CATEGORY_CODE_BY_INDEX: dict[int, str] = {idx: code for code, idx in CATEGORY_INDEX_BY_CODE.items()}


def normalize_category_code(value) -> str | None:
    """canonical category 归一化：完整 slug 键或两位码 -> 正式 slug 键。

    None = 非法 / 缺失（调用方按 ASSIGN_INPUT_MISSING_CATEGORY 或
    ASSIGN_INPUT_INVALID_CATEGORY 处理）。不猜、不补、不重推。
    """
    if value is None:
        return None
    text = str(value).strip()
    canonical = K.normalize_failure_mode(text)
    if canonical is not None:
        return CATEGORY_CODES[K.CANONICAL_FAILURE_MODES.index(canonical)]
    if text in CATEGORY_INDEX_BY_CODE:
        return text
    if text in _TWO_LETTER_INDEX:
        return CATEGORY_CODES[_TWO_LETTER_INDEX[text] - 1]
    return None


def category_index_of(code: str) -> int:
    """正式 slug 键 -> 1..14 稳定索引（非法输入抛 KeyError，调用方先 normalize）。"""
    return CATEGORY_INDEX_BY_CODE[code]


def category_code_of(index: int) -> str:
    """1..14 索引 -> 正式 slug 键。"""
    return CATEGORY_CODE_BY_INDEX[index]