"""task_id_corrections.jsonl 审计日志（人工修正 / canonicalize 生成全局 ID 均记录）。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


def append_task_id_correction(log_path: str | Path, record: dict[str, Any]) -> None:
    """追加一条修正记录（jsonl 每行一个对象）。不会覆盖已有行。"""

    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = dict(record)
    record.setdefault("timestamp", datetime.now().astimezone().isoformat(timespec="seconds"))
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_corrections(log_path: str | Path) -> list[dict[str, Any]]:
    """读取全部修正记录（文件不存在时返回空列表）。"""

    path = Path(log_path)
    if not path.is_file():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records