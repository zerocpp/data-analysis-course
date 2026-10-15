"""``run_one`` —— 统一单次执行入口（``datac eval run`` 与批量串行循环共用）。

职责只有组织层四件事：

1. 组装全新平铺 workspace（Task × Agent × Model，见 ``workspace.py``）；
2. 调用现有唯一执行核心 ``main.runner.run_project``（与 ``datac run`` /
   ``datac export`` / ``datac matrix`` 同一套 Runtime：子进程 + Budget
   Supervisor + BudgetedLLM + gold 判分 + result.json 落盘）；
3. 把 CLI / 配置文件合并出的资源约束以 ``Budget`` 注入现有 Runtime；
4. 写 ``execution.log``（脱敏）与 ``eval_record.json``（评测层元数据），
   汇总表随后从磁盘 ``result.json`` 读取真实数据。

不实现第二套 Agent 执行 / LLM 客户端 / 超时 / 预算 / 判分逻辑；API Key
绝不写入任何落盘文件。
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from ..main.budget import Budget
from ..main.build import BuildError
from ..main.project import ProjectError
from ..main.runner import MainError, run_project
from ..main.validation import ValidationError
from ..policy_loader import runtime_policy
from .config import EffectiveLimits, ModelConfig
from .outcome import FINAL_OUTCOME_UNEVALUATED, summarize_outcome
from .workspace import (
    EvalInputError,
    assemble_workspace,
    locate_agent_file,
    locate_task_files,
    load_task_id,
)

EVAL_RECORD_SCHEMA = "datac-eval-run-v1"
EVAL_RECORD_FILENAME = "eval_record.json"
EVAL_ERROR_FILENAME = "eval_error.json"
EXECUTION_LOG_FILENAME = "execution.log"
ERROR_MESSAGE_LIMIT = 300


class EvalError(RuntimeError):
    """评测组织层错误（如输出目录冲突）；与单条执行的输入错误严格区分。"""


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _sanitize_message(value: Any, secrets: list[str]) -> str:
    """单行化 + 截断 + 抹除已知机密（错误描述进入日志 / 汇总前的最后防线）。"""

    text = re.sub(r"\s+", " ", str(value or "")).strip()
    for secret in secrets:
        if len(secret) >= 8 and secret in text:
            text = text.replace(secret, "[REDACTED]")
    return text[:ERROR_MESSAGE_LIMIT]


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _eval_budget(limits: EffectiveLimits) -> Budget:
    """把合并后的资源约束装进现有 Budget（memory / disk 上限仍取自 policy）。"""

    agent_cfg = runtime_policy().get("agent") or {}
    memory = agent_cfg.get("memory_limit_mb")
    disk = agent_cfg.get("disk_limit_mb")
    return Budget(
        timeout_seconds=limits.supervisor_timeout_seconds,
        max_memory_mb=float(memory) if memory else None,
        max_disk_mb=float(disk) if disk else None,
        max_tokens=limits.max_tokens,
    )


def _input_error_record(
    *,
    task_dir: Path,
    agent_dir: Path,
    model_config: ModelConfig,
    limits: EffectiveLimits,
    batch_id: str | None,
    combo_key: str | None,
    config_source: str | None,
    message: str,
    secrets: list[str],
    start_iso: str,
) -> dict[str, Any]:
    detail = _sanitize_message(message, secrets)
    return {
        "schema": EVAL_RECORD_SCHEMA,
        "batch_id": batch_id,
        "combo_key": combo_key,
        "task_dir": str(task_dir),
        "agent_dir": str(agent_dir),
        "task_id": task_dir.name,
        "agent_id": agent_dir.name,
        "model": model_config.model,
        "model_config": model_config.redacted(),
        "limits": limits.as_dict(),
        "config_source": config_source,
        "status": "input_error",
        "run_id": None,
        "final_outcome": FINAL_OUTCOME_UNEVALUATED,
        "error_category": "invalid_input",
        "error_message": detail,
        "started_at": start_iso,
        "finished_at": _now_iso(),
        "wall_seconds": None,
    }


def _write_execution_log(
    log_path: Path,
    *,
    record: dict[str, Any],
    task_dir: Path,
    agent_dir: Path,
    llm_injected: bool,
    result: dict[str, Any] | None,
    secrets: list[str],
) -> None:
    """运行证据日志（键值行；绝不包含 API Key 值）。"""

    limits = record["limits"]
    lines = [
        "# datac eval execution log",
        f"schema={EVAL_RECORD_SCHEMA}",
        f"started_at={record['started_at']}",
        f"batch_id={record['batch_id'] or ''}",
        f"combo_key={record['combo_key'] or ''}",
        f"task_id={record.get('task_id', '')} task_dir={task_dir}",
        f"agent_id={record.get('agent_id', '')} agent_dir={agent_dir}",
        f"model={record['model']} base_url={record['model_config']['base_url']}"
        f" api_key_env={record['model_config']['api_key_env']}",
        f"timeout_business_seconds={limits['business_timeout_seconds']}"
        f" timeout_supervisor_seconds={limits['supervisor_timeout_seconds']}"
        f" token_budget={limits['max_tokens']}",
        f"timeout_requested={limits['requested_timeout_seconds']}"
        f" token_budget_requested={limits['requested_token_budget']}"
        f" clamped={'|'.join(limits['clamped'])}",
        f"config_source={record['config_source'] or ''}",
        f"llm={'injected (offline fixture)' if llm_injected else 'workspace .env'}",
    ]
    if record["status"] == "input_error":
        lines.append(f"error=input_error detail={record['error_message']}")
    else:
        lines.append(f"step=workspace_assembled workspace={record['workspace']}")
        lines.append(f"step=runtime_finished run_id={record['run_id']}")
        if result is not None:
            lines.append(
                f"runtime_status={result.get('status')}"
                f" outcome_type={result.get('outcome_type')}"
                f" internal_error_category={result.get('internal_error_category')}"
            )
            lines.append(
                f"final_outcome={record['final_outcome']}"
                f" correct={result.get('correct')}"
                f" elapsed_seconds={result.get('elapsed_seconds')}"
                f" tokens_used={result.get('total_tokens')}"
            )
            gold = (result.get("result") or {}).get("declared_gold_answer")
            answer = (result.get("result") or {}).get("result")
            lines.append(f"agent_answer={_sanitize_message(json.dumps(answer, ensure_ascii=False), secrets)}")
            lines.append(f"declared_gold_answer={_sanitize_message(json.dumps(gold, ensure_ascii=False), secrets)}")
        else:
            lines.append("error=result.json 缺失或不可读（见 eval_record.json）")
        lines.append(f"error_category={record['error_category'] or ''}")
        if record["error_message"]:
            lines.append(f"error_message={record['error_message']}")
        lines.append(f"result_path={record['result_path'] or ''}")
    lines.append(f"finished_at={record['finished_at']} wall_seconds={record['wall_seconds']}")
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_one(
    *,
    task_dir: str | Path,
    agent_dir: str | Path,
    model_config: ModelConfig,
    limits: EffectiveLimits,
    output_dir: str | Path,
    batch_id: str | None = None,
    combo_key: str | None = None,
    config_source: str | None = None,
    llm_factory: Callable[[ModelConfig, str, str], Any] | None = None,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """执行一次完整 Agent 生命周期并落盘结构化结果。

    ``llm_factory`` 仅用于离线 fixture / 测试注入（与 ``matrix.run_matrix``
    的 llm_factory 同一机制）；生产路径为 None，LLM 由 workspace .env 构造。
    返回 eval_record dict；批量模式按记录继续，不向上传播执行失败。
    """

    emit = log or (lambda *_: None)
    output = Path(output_dir)
    if output.exists():
        raise EvalError(
            f"输出目录已存在（默认不覆盖已有结果；如需重建请先删除或使用 --force/--resume）: {output}"
        )
    task_root = Path(task_dir)
    agent_root = Path(agent_dir)
    secrets = [model_config.api_key] if model_config.api_key else []
    record: dict[str, Any] | None = None
    started = time.monotonic()
    start_iso = _now_iso()
    try:
        task_json, data_csv, reference_py = locate_task_files(task_root)
        agent_py = locate_agent_file(agent_root)
        task_id = load_task_id(task_json, task_root.name)
        agent_id = agent_root.name
        assemble_workspace(
            output,
            task_json=task_json,
            data_csv=data_csv,
            reference_py=reference_py,
            agent_py=agent_py,
            model_config=model_config,
            offline=llm_factory is not None,
        )
    except (EvalInputError, OSError) as exc:
        output.mkdir(parents=True, exist_ok=True)
        record = _input_error_record(
            task_dir=task_root,
            agent_dir=agent_root,
            model_config=model_config,
            limits=limits,
            batch_id=batch_id,
            combo_key=combo_key,
            config_source=config_source,
            message=str(exc),
            secrets=secrets,
            start_iso=start_iso,
        )
        record["workspace"] = str(output)
        record["result_path"] = ""
        record["log_path"] = str(output / EXECUTION_LOG_FILENAME)
        (output / EVAL_ERROR_FILENAME).write_text(
            json.dumps(
                {"error_category": "invalid_input", "error_message": record["error_message"]},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        _write_execution_log(
            output / EXECUTION_LOG_FILENAME,
            record=record,
            task_dir=task_root,
            agent_dir=agent_root,
            llm_injected=False,
            result=None,
            secrets=secrets,
        )
        (output / EVAL_RECORD_FILENAME).write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        emit(f"      -> {record['final_outcome']} (input_error: {record['error_message']})")
        return record

    llm = llm_factory(model_config, task_id, agent_id) if llm_factory is not None else None
    runtime_result: dict[str, Any] | None = None
    try:
        summary = run_project(
            output,
            llm=llm,
            agent_id=agent_id,
            budget=_eval_budget(limits),
        )
        run_id = str(summary["run_id"])
        result_path = output / "runs" / run_id / "result.json"
        runtime_result = _read_json(result_path)
    except (ProjectError, ValidationError, BuildError, MainError, OSError) as exc:
        run_id = None
        result_path = None
        failure = str(exc)
    except Exception as exc:  # noqa: BLE001 - 评测壳层自身异常也必须留下记录
        run_id = None
        result_path = None
        failure = f"eval_runner_exception: {type(exc).__name__}: {exc}"

    finished = time.monotonic()
    if runtime_result is not None:
        final_outcome, error_category = summarize_outcome(runtime_result)
        error_message = _sanitize_message(
            runtime_result.get("internal_error_detail") or runtime_result.get("error") or "",
            secrets,
        )
        record = {
            "schema": EVAL_RECORD_SCHEMA,
            "batch_id": batch_id,
            "combo_key": combo_key,
            "task_id": task_id,
            "task_dir": str(task_root),
            "agent_id": agent_id,
            "agent_dir": str(agent_root),
            "model": model_config.model,
            "model_config": model_config.redacted(),
            "limits": limits.as_dict(),
            "config_source": config_source,
            "status": "executed",
            "run_id": run_id,
            "workspace": str(output),
            "result_path": str(result_path),
            "log_path": str(output / EXECUTION_LOG_FILENAME),
            "final_outcome": final_outcome,
            "error_category": error_category,
            "error_message": error_message,
            "started_at": start_iso,
            "finished_at": _now_iso(),
            "wall_seconds": round(finished - started, 3),
        }
    else:
        record = _input_error_record(
            task_dir=task_root,
            agent_dir=agent_root,
            model_config=model_config,
            limits=limits,
            batch_id=batch_id,
            combo_key=combo_key,
            config_source=config_source,
            message=failure,
            secrets=secrets,
            start_iso=start_iso,
        )
        record["workspace"] = str(output)
        record["result_path"] = ""
        record["log_path"] = str(output / EXECUTION_LOG_FILENAME)
        (output / EVAL_ERROR_FILENAME).write_text(
            json.dumps(
                {"error_category": record["error_category"], "error_message": record["error_message"]},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    record["log_path"] = str(output / EXECUTION_LOG_FILENAME)
    _write_execution_log(
        output / EXECUTION_LOG_FILENAME,
        record=record,
        task_dir=task_root,
        agent_dir=agent_root,
        llm_injected=llm is not None,
        result=runtime_result,
        secrets=secrets,
    )
    (output / EVAL_RECORD_FILENAME).write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if record["status"] == "executed":
        result = runtime_result or {}
        emit(
            f"      -> {record['final_outcome']}"
            f" (elapsed={result.get('elapsed_seconds')}s, tokens={result.get('total_tokens')})"
        )
    else:
        emit(f"      -> {record['final_outcome']} ({record['error_message']})")
    return record
