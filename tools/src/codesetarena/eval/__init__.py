"""统一评测工具（Eval Runner）—— 单次执行 + 批量串行执行 + 自动结果汇总。

三条能力共用**同一个**现有 Runtime 执行核心 ``main.runner.run_project``
（与 ``datac run`` / ``datac export`` / ``datac matrix`` 完全相同的子进程
执行、Budget 资源控制、LLM 接口与 gold 判分实现），本包只做组织层四件事：

1. ``config``   —— 配置合并（系统默认 < 配置文件 < CLI 显式覆盖 < Policy 钳制）；
2. ``runner``   —— ``run_one``：组装 fresh workspace → 调 run_project →
   写 execution.log + eval_record.json（单次与批量共用的唯一单次执行入口）；
3. ``batch``    —— 批量串行循环（Model → Task → Agent 固定顺序）+ manifest；
4. ``summary``  —— 从磁盘 ``result.json`` 生成 summary.csv / summary.json。

不实现第二套 Agent 执行 / LLM / Budget / 判分逻辑；API Key 只经环境变量
间接提供，绝不写入任何结果文件、汇总表或日志。
"""

from .batch import BATCH_MANIFEST_FILENAME, run_batch
from .config import (
    BATCH_CONFIG_SCHEMA,
    DEFAULT_API_KEY_ENV,
    EVAL_CONFIG_SCHEMA,
    BatchConfig,
    EffectiveLimits,
    EvalConfigError,
    ModelConfig,
    load_batch_config,
    load_config_file,
    resolve_limits,
    resolve_model_config,
)
from .outcome import (
    FINAL_OUTCOME_VALUES,
    summarize_outcome,
)
from .runner import (
    EVAL_RECORD_FILENAME,
    EXECUTION_LOG_FILENAME,
    EvalError,
    run_one,
)
from .summary import SUMMARY_CSV_FIELDS, build_summary, write_summary
from .workspace import EvalInputError

__all__ = [
    "BATCH_CONFIG_SCHEMA",
    "BATCH_MANIFEST_FILENAME",
    "DEFAULT_API_KEY_ENV",
    "EVAL_CONFIG_SCHEMA",
    "EVAL_RECORD_FILENAME",
    "EXECUTION_LOG_FILENAME",
    "FINAL_OUTCOME_VALUES",
    "SUMMARY_CSV_FIELDS",
    "BatchConfig",
    "EffectiveLimits",
    "EvalConfigError",
    "EvalError",
    "EvalInputError",
    "ModelConfig",
    "build_summary",
    "load_batch_config",
    "load_config_file",
    "resolve_limits",
    "resolve_model_config",
    "run_batch",
    "run_one",
    "summarize_outcome",
    "write_summary",
]
