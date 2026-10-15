"""评测 Workspace 组装 —— Task × Agent × Model → 全新平铺 workspace。

布局契约与 ``main.matrix.prepare_workspace`` 完全一致（矩阵链路已验证）::

    agent.py + task.json + data.csv + reference.py + .env + .env.example

``.env`` 只写 **Connection Configuration**（LLM_BASE_URL / LLM_API_KEY）与
模型选择（LLM_MODEL）；Runtime 资源约束（timeout / token budget）仍由
``policy/runtime_policy.json`` 决定（Configuration / Policy Separation）。
组装后的 workspace 交给现有 ``main.runner.run_project`` 执行，本模块不实现
任何执行逻辑。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import ModelConfig

TASK_JSON = "task.json"
DATA_CSV = "data.csv"
REFERENCE_PY = "reference.py"
AGENT_PY = "agent.py"
ENV_FILE = ".env"
ENV_EXAMPLE = ".env.example"
ENV_EXAMPLE_CONTENT = "LLM_BASE_URL=\nLLM_API_KEY=\nLLM_MODEL=\n"


class EvalInputError(RuntimeError):
    """Task / Agent 输入资产非法（缺文件 / 不可解析）。

    单次模式下映射为退出码 2 的输入错误；批量模式下按单条组合记录
    input_error，不中断其余组合。
    """


def locate_task_files(task_dir: str | Path) -> tuple[Path, Path, Path]:
    """返回 (task.json, data.csv, reference.py)；data.csv 允许在 data/ 子目录。"""

    task_root = Path(task_dir)
    if not task_root.is_dir():
        raise EvalInputError(f"task 目录不存在: {task_root}")
    task_json = task_root / TASK_JSON
    if not task_json.is_file():
        raise EvalInputError(f"task 目录缺少 {TASK_JSON}: {task_root}")
    data_csv = task_root / DATA_CSV
    if not data_csv.is_file():
        nested = task_root / "data" / DATA_CSV
        if not nested.is_file():
            raise EvalInputError(f"task 目录缺少 {DATA_CSV}: {task_root}")
        data_csv = nested
    reference_py = task_root / REFERENCE_PY
    if not reference_py.is_file():
        raise EvalInputError(f"task 目录缺少 {REFERENCE_PY}: {task_root}")
    return task_json, data_csv, reference_py


def locate_agent_file(agent_dir: str | Path) -> Path:
    """返回 agent.py 路径（agent 目录契约：目录下直接放 agent.py）。"""

    agent_root = Path(agent_dir)
    if not agent_root.is_dir():
        raise EvalInputError(f"agent 目录不存在: {agent_root}")
    agent_py = agent_root / AGENT_PY
    if not agent_py.is_file():
        raise EvalInputError(f"agent 目录缺少 {AGENT_PY}: {agent_root}")
    return agent_py


def load_task_id(task_json: Path, fallback: str) -> str:
    """task.json 的 task_id；缺失 / 不可解析时回落目录名（不在此判定非法）。"""

    try:
        task = json.loads(task_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback
    task_id = str((task or {}).get("task_id") or "").strip()
    return task_id or fallback


def assemble_workspace(
    dest: Path,
    *,
    task_json: Path,
    data_csv: Path,
    reference_py: Path,
    agent_py: Path,
    model_config: ModelConfig,
    offline: bool = False,
) -> None:
    """组装全新平铺 workspace（dest 必须不存在，由调用方负责冲突策略）。

    ``offline=True``（LLM 注入的离线 fixture / 测试路径）允许空 API Key 回落
    占位值 ``offline-fixture``（与 ``matrix._write_offline_env`` 同一先例），
    使 workspace 校验可以通过；真实请求路径（offline=False）绝不占位。
    """

    dest.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(task_json, dest / TASK_JSON)
    shutil.copyfile(data_csv, dest / DATA_CSV)
    shutil.copyfile(reference_py, dest / REFERENCE_PY)
    shutil.copyfile(agent_py, dest / AGENT_PY)
    api_key = model_config.api_key or ("offline-fixture" if offline else "")
    (dest / ENV_FILE).write_text(
        f"LLM_BASE_URL={model_config.base_url}\n"
        f"LLM_API_KEY={api_key}\n"
        f"LLM_MODEL={model_config.model}\n",
        encoding="utf-8",
    )
    (dest / ENV_EXAMPLE).write_text(ENV_EXAMPLE_CONTENT, encoding="utf-8")
