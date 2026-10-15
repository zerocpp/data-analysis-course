"""批量串行执行 —— N Tasks × M Agents × K Model Configs → 自动汇总。

组织层唯一职责（不重新实现任何 Runtime）::

    for model_config in model_configs:      # 固定顺序：Model → Task → Agent
        for task in tasks:
            for agent in agents:
                run_one(...)                # 与 `datac eval run` 完全相同的单次入口
    # 全部落盘后：从磁盘 result.json 生成 summary.csv + summary.json

- 只串行：前一条运行完成并落盘后才执行下一条；
- 单条失败（答案错误 / 超时 / API 错误 / 崩溃 / 输入资产非法）只记录不中断，
  批量继续执行其余组合；
- 中断安全：外部 Ctrl-C 时保留已完成结果，剩余组合标记 not_executed 并
  照常生成部分汇总；
- 不静默覆盖：输出目录已存在时拒绝执行，除非显式 --resume（跳过已完成的
  组合并续跑）或 --force（整目录重建）。
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .config import (
    BATCH_CONFIG_SCHEMA,
    BatchConfig,
    EffectiveLimits,
    ModelConfig,
    load_batch_config,
    resolve_limits,
    resolve_model_config,
)
from .runner import EvalError, run_one
from .summary import build_summary, write_summary
from .workspace import load_task_id

BATCH_MANIFEST_FILENAME = "batch_manifest.json"
MANIFEST_SCHEMA = "datac-eval-batch-v1"


def _sanitize_label(text: str) -> str:
    label = re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("._-")
    return label or "model"


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _plan_combos(
    batch: BatchConfig, output_root: Path
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """组合规划（Model → Task → Agent 固定顺序）+ 模型标签去重。"""

    seen_labels: dict[str, int] = {}
    models: list[dict[str, Any]] = []
    for config_text in batch.model_configs:
        base_label = _sanitize_label(Path(config_text).stem)
        count = seen_labels.get(base_label, 0) + 1
        seen_labels[base_label] = count
        label = base_label if count == 1 else f"{base_label}_{count}"
        models.append({"label": label, "config": config_text})

    combos: list[dict[str, Any]] = []
    for model_entry in models:
        for task_text in batch.tasks:
            for agent_text in batch.agents:
                task_dir = Path(task_text)
                agent_dir = Path(agent_text)
                try:
                    task_json, _data, _reference = _locate_for_plan(task_dir)
                    task_id = load_task_id(task_json, task_dir.name)
                except Exception:  # noqa: BLE001 - 规划期 ID 只求尽力，非法资产由 run_one 记录
                    task_id = task_dir.name
                combo_dir = output_root / model_entry["label"] / f"{task_id}__{agent_dir.name}"
                combos.append(
                    {
                        "combo_key": f"{model_entry['label']}/{task_id}__{agent_dir.name}",
                        "model_label": model_entry["label"],
                        "model_config": model_entry["config"],
                        "task": str(task_dir),
                        "agent": str(agent_dir),
                        "task_id": task_id,
                        "agent_id": agent_dir.name,
                        "combo_dir": str(combo_dir),
                        "status": "pending",
                    }
                )
    return combos, models


def _locate_for_plan(task_dir: Path):
    from .workspace import locate_task_files

    return locate_task_files(task_dir)


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def run_batch(
    batch_config: str | Path,
    output_dir: str | Path,
    *,
    timeout: float | None = None,
    token_budget: int | None = None,
    api_key_env: str | None = None,
    resume: bool = False,
    force: bool = False,
    batch_id: str | None = None,
    llm_factory: Callable[[ModelConfig, str, str], Any] | None = None,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """执行完整批次并自动生成汇总；返回含统计与文件路径的结果 dict。

    ``llm_factory`` 仅用于离线 fixture / 测试注入；生产路径为 None。
    """

    emit = log or (lambda *_: None)
    batch = load_batch_config(batch_config)
    output = Path(output_dir)
    batch_id = batch_id or batch.batch_id or output.name

    # 模型配置在批次开始前统一解析：任何一个配置文件缺失 / 非法 JSON /
    # 缺少 API Key 都是配置级错误（整体不执行），绝不带病起跑。
    # 标签去重逻辑必须与 _plan_combos 完全一致（同序遍历 model_configs）。
    seen_labels: dict[str, int] = {}
    model_configs: dict[str, ModelConfig] = {}
    limits_by_label: dict[str, EffectiveLimits] = {}
    for config_text in batch.model_configs:
        model_config = resolve_model_config(
            config_text,
            api_key_env=api_key_env,
            require_api_key=llm_factory is None,
        )
        base_label = _sanitize_label(Path(config_text).stem)
        count = seen_labels.get(base_label, 0) + 1
        seen_labels[base_label] = count
        label = base_label if count == 1 else f"{base_label}_{count}"
        model_configs[label] = model_config
        limits_by_label[label] = resolve_limits(Path(config_text), timeout, token_budget)

    out_root = output.resolve()
    manifest_path = out_root / BATCH_MANIFEST_FILENAME
    resumed = False
    if out_root.exists():
        if resume:
            if not manifest_path.is_file():
                raise EvalError(
                    f"--resume 需要已有批次目录（含 {BATCH_MANIFEST_FILENAME}）: {out_root}"
                )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            resumed = True
        elif force:
            shutil.rmtree(out_root)
        else:
            raise EvalError(
                f"输出目录已存在（默认不覆盖已有结果；--resume 续跑 / --force 重建）: {out_root}"
            )
    if not resumed:
        combos, models = _plan_combos(batch, out_root)
        manifest = {
            "schema": MANIFEST_SCHEMA,
            "batch_config_schema": BATCH_CONFIG_SCHEMA,
            "batch_id": batch_id,
            "created_at": _now_iso(),
            "batch_config": str(Path(batch_config)),
            "serial": True,
            "overrides": {
                "timeout": timeout,
                "token_budget": token_budget,
                "api_key_env": api_key_env,
            },
            "models": [
                {
                    "label": model["label"],
                    "config": model["config"],
                    "model": model_configs[model["label"]].redacted(),
                    "limits": limits_by_label[model["label"]].as_dict(),
                }
                for model in models
            ],
            "combos": combos,
        }
    else:
        combos = manifest["combos"]
        models = manifest["models"]
        planned_keys = [
            (c["model_label"], c["task_id"], c["agent_id"]) for c in combos
        ]
        try:
            fresh_combos, fresh_models = _plan_combos(batch, out_root)
        except Exception as exc:  # noqa: BLE001 - 规划失败说明配置与 manifest 不再一致
            raise EvalError(f"--resume 组合规划失败（批量配置已变化？）: {exc}") from exc
        fresh_keys = [
            (c["model_label"], c["task_id"], c["agent_id"]) for c in fresh_combos
        ]
        if planned_keys != fresh_keys:
            raise EvalError(
                "--resume 拒绝：当前批量配置与 manifest 中的计划组合不一致"
                "（请使用新的输出目录）"
            )
        models = fresh_models if fresh_models else models

    out_root.mkdir(parents=True, exist_ok=True)
    manifest["updated_at"] = _now_iso()
    _write_manifest(manifest_path, manifest)

    total = len(combos)
    interrupted = False
    try:
        for index, combo in enumerate(combos, 1):
            if combo["status"] == "executed":
                emit(f"[{index}/{total}] {combo['task_id']} × {combo['agent_id']}"
                     f" × {combo['model_label']}")
                emit("      -> cached（--resume 跳过已完成组合）")
                continue
            emit(
                f"[{index}/{total}] {combo['task_id']} × {combo['agent_id']}"
                f" × {combo['model_label']}"
            )
            combo_dir = Path(combo["combo_dir"])
            if combo_dir.exists():
                shutil.rmtree(combo_dir)
            label = combo["model_label"]
            record = run_one(
                task_dir=combo["task"],
                agent_dir=combo["agent"],
                model_config=model_configs[label],
                limits=limits_by_label[label],
                output_dir=combo_dir,
                batch_id=batch_id,
                combo_key=combo["combo_key"],
                config_source=combo["model_config"],
                llm_factory=llm_factory,
                log=emit,
            )
            combo["status"] = record["status"]
            manifest["updated_at"] = _now_iso()
            _write_manifest(manifest_path, manifest)
    except KeyboardInterrupt:
        interrupted = True
        for combo in combos:
            if combo["status"] == "pending":
                combo["status"] = "not_executed"
        manifest["updated_at"] = _now_iso()
        manifest["interrupted"] = True
        _write_manifest(manifest_path, manifest)
        emit("[batch] 收到中断信号：已保留全部已完成结果，剩余组合标记 not_executed")

    rows, summary = build_summary(
        batch_id=batch_id,
        output_dir=out_root,
        combos=combos,
        models=manifest["models"],
        interrupted=interrupted,
        batch_config=str(Path(batch_config)),
    )
    csv_path, json_path = write_summary(out_root, rows, summary)

    executed_text = (
        f"{summary['executed_total']}/{total} executed"
        + ("" if not interrupted else " (interrupted)")
    )
    emit(f"Batch finished: {executed_text}")
    emit(
        "  counts: "
        + ", ".join(f"{key}={value}" for key, value in summary["counts"].items())
    )
    emit(f"Results: {out_root}")
    emit(f"Summary: {csv_path}")
    return {
        "batch_id": batch_id,
        "output_dir": str(out_root),
        "planned_total": total,
        "executed_total": summary["executed_total"],
        "counts": summary["counts"],
        "complete": summary["complete"],
        "interrupted": interrupted,
        "summary_csv": str(csv_path),
        "summary_json": str(json_path),
        "rows": rows,
        "summary": summary,
    }
