"""Batch Collector —— 输入目录中 *.zip 的发现与 Batch 汇总（无业务规则）。

只做两件事：

- ``find_archives``：列出输入目录下的提交 archive（按文件名排序，确定性）；
- ``aggregate``：对第一遍 Single-Package Validation 的结果做简单计数。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import contract as C


def find_archives(input_dir: str | Path) -> list[Path]:
    """列出输入目录下的提交包（*.zip legacy + *.tar.gz 正式格式，按名排序）。

    两个格式都属于 Submission Archive（Contract 统一校验）；正式格式为 tar.gz。
    """

    root = Path(input_dir)
    if not root.is_dir():
        return []
    return sorted(
        p for p in root.iterdir()
        if p.is_file() and (p.name.lower().endswith(".zip") or p.name.lower().endswith(".tar.gz"))
    )


def aggregate(archive_results: list[dict[str, Any]]) -> dict[str, Any]:
    """对第一遍结果计数（single_package PASS / FAIL 与按阶段分布）。"""

    packages = archive_results
    single_pass = [r for r in packages if r["single_package_status"] == "PASS"]
    single_fail = [r for r in packages if r["single_package_status"] != "PASS"]
    by_phase: dict[str, int] = {}
    for package in packages:
        phase = package.get("submission_phase") or C.PHASE1
        by_phase[phase] = by_phase.get(phase, 0) + 1
    return {
        "package_count": len(packages),
        "single_pass_count": len(single_pass),
        "single_fail_count": len(single_fail),
        "candidate_count": len(single_pass),
        "by_phase": by_phase,
    }