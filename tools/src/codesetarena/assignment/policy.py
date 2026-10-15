"""Assignment Policy（评审分配策略，与 runtime_policy 严格分离）。

``policy/assignment_policy.json`` 定义 Reviewer Assignment Strategy：
method / r / swap budget / seed 规则。独立于 Agent Execution Resource
Policy（``policy/runtime_policy.json``），绝不合并。

v1 主实验参数来自 Phase B 已经验证的配置（不重新调优）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from codesetarena.assignment.contract import ASSIGNMENT_CONTRACT_ID, AssignmentError


def _default_policy_path() -> Path:
    """项目根 = CSA_ROOT 或向上找到含 policy/assignment_policy.json 的目录。"""
    env = os.environ.get("CSA_ROOT")
    if env:
        candidate = Path(env) / "policy" / "assignment_policy.json"
        if candidate.is_file():
            return candidate
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "policy" / "assignment_policy.json"
        if candidate.is_file():
            return candidate
    return here.parents[3] / "policy" / "assignment_policy.json"


def load_assignment_policy(path=None) -> dict:
    """加载并校验 assignment_policy.json；损坏 = AssignmentError（退出码 2）。"""
    policy_path = Path(path) if path else _default_policy_path()
    try:
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AssignmentError("ASSIGN_POLICY_INVALID", f"assignment policy 不可读: {exc}") from exc
    validate_assignment_policy(policy, path=str(policy_path))
    return policy


def validate_assignment_policy(policy: dict, *, path: str = "policy") -> None:
    errors: list[str] = []
    method = policy.get("method")
    if not isinstance(method, str) or not method:
        errors.append("method 缺失")
    r = policy.get("reviews_per_task")
    tpr = policy.get("tasks_per_reviewer")
    if not isinstance(r, int) or r <= 0:
        errors.append("reviews_per_task 必须为正整数")
    if not isinstance(tpr, int) or tpr <= 0:
        errors.append("tasks_per_reviewer 必须为正整数")
    if isinstance(r, int) and isinstance(tpr, int) and r != tpr:
        errors.append("reviews_per_task != tasks_per_reviewer（v1 contract 要求相等）")
    iterations = policy.get("max_swap_iterations")
    if not isinstance(iterations, int) or iterations <= 0:
        errors.append("max_swap_iterations 必须为正整数")
    if policy.get("no_silent_fallback") is not True:
        errors.append("no_silent_fallback 必须为 true（生产禁止静默 fallback）")
    if errors:
        raise AssignmentError("ASSIGN_POLICY_INVALID", f"{path}: " + "; ".join(errors))
    if method not in ("CATEGORY_AWARE_SWAP", "RANDOM_FEASIBLE", "RANDOM_SWAP",
                      "CATEGORY_AWARE_GREEDY", "OPTIMIZED_ASSIGNMENT"):
        raise AssignmentError("ASSIGN_POLICY_INVALID", f"{path}: 未知 method {method!r}")


def policy_solver_config(policy: dict) -> dict:
    """Solver config（只透传算法实验参数，不含课程 runtime policy）。"""
    return {
        "max_iterations": int(policy["max_swap_iterations"]),
        "tie_accept_probability": float(policy.get("tie_accept_probability", 0.5)),
    }


def policy_version(policy: dict) -> str:
    return str(policy.get("schema_version", ASSIGNMENT_CONTRACT_ID))