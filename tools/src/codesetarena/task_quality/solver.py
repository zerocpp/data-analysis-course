"""Blind Independent Solver 适配层（§6/§7/§8/§27/§29）。

LLM 以可注入 callable ``prompt -> str`` 接入：生产默认绑
``model_client.managed_completion``（workspace .env 配置），测试注入 Fake。
本模块只负责 prompt 组装、结构化解析与有限自动修复，不解析自然语言业务结论。
"""
from __future__ import annotations

import json
import re
from typing import Callable

from .models import (
    SOLVER_INTERNAL_ERROR,
    SOLVER_STATUSES,
    GateConfig,
)

_REQUIRED_FIELDS = (
    "status",
    "interpretation",
    "assumptions",
    "ambiguities",
    "data_sufficiency",
    "algorithm_summary",
    "generated_reference_code",
    "confidence",
)


class BlindSolverError(RuntimeError):
    """结构化响应无法解析 / 状态非法（归属 Gate 基础设施，不归罪学生）。"""


def _extract_json(text: str) -> dict:
    cleaned = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.S)
    if fenced:
        cleaned = fenced.group(1).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        cleaned = cleaned[start:end + 1]
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise BlindSolverError("structured response is not a JSON object")
    return data


def parse_solver_response(content: str) -> dict:
    data = _extract_json(content)
    missing = [f for f in _REQUIRED_FIELDS if f not in data]
    if missing:
        raise BlindSolverError(f"structured response missing fields: {missing}")
    status = str(data.get("status") or "").strip().upper()
    if status not in SOLVER_STATUSES:
        raise BlindSolverError(f"unknown solver status {status!r}")
    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    return {
        "status": status,
        "interpretation": str(data.get("interpretation") or ""),
        "assumptions": list(data.get("assumptions") or []),
        "ambiguities": list(data.get("ambiguities") or []),
        "data_sufficiency": str(data.get("data_sufficiency") or ""),
        "algorithm_summary": str(data.get("algorithm_summary") or ""),
        "generated_reference_code": str(data.get("generated_reference_code") or ""),
        "confidence": confidence,
    }


def repair_prompt(previous_prompt: str, response_or_error: str, problem: str) -> str:
    """§29 有限自动修复：把具体错误回喂一次，要求只修 JSON/代码问题。"""
    return (
        previous_prompt
        + "\n\nPREVIOUS_ATTEMPT_PROBLEM:\n"
        + problem
        + "\n\nPREVIOUS_RAW_RESPONSE:\n"
        + response_or_error[:4000]
        + "\n\nReturn the corrected single JSON object now."
    )


def run_blind_solver(
    prompt: str,
    llm: Callable[[str], str],
    *,
    config: GateConfig,
) -> tuple[dict, list[dict]]:
    """调用 Blind Solver（含 ≤ max_generation_attempts 次有限修复）。

    返回 (verdict, generation_log)。结构化解析失败 / 状态非法抛 BlindSolverError。
    """
    attempts: list[dict] = []
    current = prompt
    last_content = ""
    for attempt in range(1, max(config.max_generation_attempts, 1) + 1):
        content = llm(current)
        last_content = content
        attempts.append({"attempt": attempt, "response_chars": len(content),
                         "response_head": content[:400]})
        try:
            verdict = parse_solver_response(content)
        except (BlindSolverError, json.JSONDecodeError) as exc:
            if attempt < config.max_generation_attempts:
                current = repair_prompt(prompt, content, f"invalid structure: {exc}")
                continue
            raise BlindSolverError(str(exc)) from exc
        if verdict["status"] == SOLVER_INTERNAL_ERROR:
            raise BlindSolverError("solver reported INTERNAL_ERROR")
        code = verdict["generated_reference_code"]
        if verdict["status"] == "SOLVED" and not code.strip():
            if attempt < config.max_generation_attempts:
                current = repair_prompt(prompt, content, "empty generated_reference_code")
                continue
            raise BlindSolverError("empty generated_reference_code")
        verdict["generation_attempts"] = attempt
        return verdict, attempts
    raise BlindSolverError(f"exhausted {config.max_generation_attempts} attempts; "
                           f"last response head: {last_content[:200]}")
