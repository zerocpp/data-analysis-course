"""统一评测配置 —— Config 文件默认值 + CLI 显式覆盖 + 强制 Policy 钳制。

单一合并实现（``datac eval run`` 与 ``datac eval batch`` 共用，禁止第二套解析）::

    系统默认值 < 配置文件默认值 < CLI 显式参数 < 强制 Policy 校验（钳制）

- 模型连接（model / base_url / api_key_env）：配置文件提供默认值，CLI 可显式
  覆盖；API Key 只通过**环境变量名**间接提供（绝不入 CLI 明文参数、结果
  文件、汇总表或日志）。
- 执行资源（timeout_seconds / token_budget）：CLI / 配置文件可指定，但只允许
  在冻结 Policy（``policy/runtime_policy.json``）上限内**收紧或维持**；超出
  上限的请求一律钳制到 Policy 上限并记录 clamped 标记 —— 评测可以调整参数，
  绝不突破课程强制资源限制（Configuration / Policy Separation）。

argparse 默认值一律为 ``None``（"未显式传入"），只有显式传入才覆盖 Config。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..policy_loader import runtime_policy

DEFAULT_API_KEY_ENV = "LLM_API_KEY"
EVAL_CONFIG_SCHEMA = "datac-eval-config-v1"
BATCH_CONFIG_SCHEMA = "datac-eval-batch-v1"

_MODEL_STRING_FIELDS = ("model", "base_url", "api_key_env")
_MODEL_LIMIT_FIELDS = ("token_budget", "timeout_seconds")


class EvalConfigError(RuntimeError):
    """评测配置非法（文件缺失 / JSON 非法 / 字段类型错误 / 预算非法 / 缺 Key）。"""


@dataclass(frozen=True)
class ModelConfig:
    """一次评测使用的模型连接配置（api_key 只存在于内存，绝不序列化）。"""

    model: str
    base_url: str
    api_key_env: str
    api_key: str
    source: str

    def redacted(self) -> dict[str, str]:
        """可安全写入日志 / manifest 的脱敏视图。"""

        return {
            "model": self.model,
            "base_url": self.base_url,
            "api_key_env": self.api_key_env,
            "api_key": f"<env:{self.api_key_env}>" if self.api_key else "<missing>",
            "source": self.source,
        }


@dataclass(frozen=True)
class EffectiveLimits:
    """最终生效的执行资源约束（已通过 Policy 钳制）。

    ``supervisor_timeout_seconds`` 是真正传入 Budget.timeout_seconds 的值
    （业务时限 + policy boot_grace 安全垫，与课程 ``_execution_budget`` 同一
    语义）；``business_timeout_seconds`` 是课程语义下的业务时限。
    """

    business_timeout_seconds: float
    supervisor_timeout_seconds: float
    max_tokens: int
    requested_timeout_seconds: float | None = None
    requested_token_budget: int | None = None
    clamped: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "business_timeout_seconds": self.business_timeout_seconds,
            "supervisor_timeout_seconds": self.supervisor_timeout_seconds,
            "max_tokens": self.max_tokens,
            "requested_timeout_seconds": self.requested_timeout_seconds,
            "requested_token_budget": self.requested_token_budget,
            "clamped": list(self.clamped),
        }


def load_config_file(path: str | Path | None) -> dict[str, Any]:
    """读取评测配置文件（缺失 / 非法 JSON / 非 JSON 对象 → EvalConfigError）。

    返回原始 dict；已知字段类型在此校验，未知字段忽略（向前兼容）。
    """

    if path is None:
        return {}
    config_path = Path(path)
    if not config_path.is_file():
        raise EvalConfigError(f"配置文件不存在: {config_path}")
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvalConfigError(f"配置文件不可读或不是合法 JSON: {config_path}: {exc}") from exc
    if not isinstance(config, dict):
        raise EvalConfigError(f"配置文件必须是 JSON 对象: {config_path}")
    for field in _MODEL_STRING_FIELDS:
        value = config.get(field)
        if value is not None and not isinstance(value, str):
            raise EvalConfigError(f"配置文件字段 {field} 必须是字符串: {config_path}")
    for field in _MODEL_LIMIT_FIELDS:
        value = config.get(field)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise EvalConfigError(f"配置文件字段 {field} 必须是数字: {config_path}")
        if value <= 0:
            raise EvalConfigError(f"配置文件字段 {field} 必须为正数: {config_path}")
    return config


def resolve_model_config(
    config_path: str | Path | None,
    *,
    model: str | None = None,
    base_url: str | None = None,
    api_key_env: str | None = None,
    require_api_key: bool = True,
) -> ModelConfig:
    """模型连接合并：系统默认 < 配置文件 < CLI 显式参数。

    ``require_api_key=False`` 仅供离线 fixture / 注入式 LLM 的测试路径
    （真实 CLI 恒为 True）：此时允许 api_key 为空，由调用方自担后果。
    """

    config = load_config_file(config_path)
    merged_model = str(
        model if model is not None else (config.get("model") or "")
    ).strip()
    merged_base_url = str(
        base_url if base_url is not None else (config.get("base_url") or "")
    ).strip()
    merged_api_key_env = str(
        api_key_env
        if api_key_env is not None
        else (config.get("api_key_env") or DEFAULT_API_KEY_ENV)
    ).strip() or DEFAULT_API_KEY_ENV

    if not merged_model:
        raise EvalConfigError(
            "缺少模型名（model）：请在配置文件提供 model 字段或用 --model 显式指定"
        )
    if not merged_base_url:
        raise EvalConfigError(
            "缺少 base_url：请在配置文件提供 base_url 字段或用 --base-url 显式指定"
        )

    api_key = os.environ.get(merged_api_key_env, "").strip()
    if require_api_key and not api_key:
        raise EvalConfigError(
            f"API Key 环境变量 {merged_api_key_env} 未设置或不为空；"
            "评测凭据只允许通过环境变量提供（不得写入命令行或配置文件明文）"
        )
    return ModelConfig(
        model=merged_model,
        base_url=merged_base_url,
        api_key_env=merged_api_key_env,
        api_key=api_key,
        source=str(config_path) if config_path is not None else "<cli-defaults>",
    )


def resolve_limits(
    config_path: str | Path | None,
    cli_timeout: float | None = None,
    cli_token_budget: int | None = None,
) -> EffectiveLimits:
    """执行资源合并 + 强制 Policy 钳制（run 与 batch 共用的唯一实现）。

    优先级：policy 默认 < 配置文件（timeout_seconds / token_budget）< CLI
    显式参数 < Policy 上限（超出即钳制）。任何时刻都不会超过
    ``policy/runtime_policy.json`` 的 ``agent.wall_clock_seconds`` 与
    ``llm.token_budget_per_execution``。
    """

    config = load_config_file(config_path)
    file_timeout = config.get("timeout_seconds")
    file_budget = config.get("token_budget")

    def _positive(value: Any, name: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise EvalConfigError(f"{name} 必须为正数，当前为 {value!r}")
        return float(value)

    timeout_chain: list[tuple[str, float | None]] = [
        ("config", float(file_timeout) if file_timeout is not None else None),
        (
            "cli",
            _positive(cli_timeout, "--timeout") if cli_timeout is not None else None,
        ),
    ]
    budget_chain: list[tuple[str, int | None]] = [
        ("config", int(file_budget) if file_budget is not None else None),
        (
            "cli",
            int(_positive(cli_token_budget, "--token-budget"))
            if cli_token_budget is not None
            else None,
        ),
    ]
    requested_timeout = timeout_chain[-1][1]
    requested_budget = budget_chain[-1][1]

    policy = runtime_policy()
    agent_cfg = policy.get("agent") or {}
    llm_cfg = policy.get("llm") or {}
    main_cfg = policy.get("main") or {}
    wall_clock = float(agent_cfg.get("wall_clock_seconds") or 90)
    boot_grace = float(main_cfg.get("boot_grace_seconds") or 0)
    token_cap = int(llm_cfg.get("token_budget_per_execution") or 0)

    effective_timeout = wall_clock
    clamped: list[str] = []
    for _source, value in timeout_chain:
        if value is None:
            continue
        effective_timeout = min(float(value), wall_clock)
    if requested_timeout is not None and requested_timeout > wall_clock:
        clamped.append("timeout")

    effective_budget = token_cap
    for _source, value in budget_chain:
        if value is None:
            continue
        effective_budget = min(int(value), token_cap)
    if requested_budget is not None and token_cap and requested_budget > token_cap:
        clamped.append("token_budget")

    return EffectiveLimits(
        business_timeout_seconds=effective_timeout,
        supervisor_timeout_seconds=effective_timeout + boot_grace,
        max_tokens=effective_budget,
        requested_timeout_seconds=requested_timeout,
        requested_token_budget=requested_budget,
        clamped=tuple(clamped),
    )


@dataclass(frozen=True)
class BatchConfig:
    """批量配置（tasks / agents / model_configs 三列表；串行组合 Model→Task→Agent）。"""

    path: Path
    tasks: tuple[str, ...]
    agents: tuple[str, ...]
    model_configs: tuple[str, ...]
    batch_id: str | None


def load_batch_config(path: str | Path) -> BatchConfig:
    """读取批量配置；列表缺失 / 为空 / 含重复项 → EvalConfigError（整体不执行）。"""

    config_path = Path(path)
    config = load_config_file(config_path)

    def _entries(field: str) -> tuple[str, ...]:
        value = config.get(field)
        if not isinstance(value, list) or not value:
            raise EvalConfigError(
                f"批量配置缺少非空 {field} 列表: {config_path}"
            )
        entries: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if not text:
                raise EvalConfigError(f"批量配置 {field} 含空条目: {config_path}")
            if text in entries:
                raise EvalConfigError(f"批量配置 {field} 存在重复条目 {text!r}: {config_path}")
            entries.append(text)
        return tuple(entries)

    batch_id = config.get("batch_id")
    if batch_id is not None:
        batch_id = str(batch_id).strip() or None
    return BatchConfig(
        path=config_path,
        tasks=_entries("tasks"),
        agents=_entries("agents"),
        model_configs=_entries("model_configs"),
        batch_id=batch_id,
    )
