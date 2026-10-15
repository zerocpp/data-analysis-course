"""Server Batch Validation —— 助教 / 服务器专属层（v2 §24–§33）。

架构（v2 §45：One Contract / One Single-Package Validator / Two Independent
Executions / One Server-only Batch Validator）:

    输入目录（一批 *.zip / *.tar.gz）
      -> collector.find_archives()
      -> 对每个 archive：validate_submission_archive()
              Archive Intake（可打开 + 解压炸弹护栏 + safe_extract）
              -> 共享 Single-Package Validator（validate_single_package）
      -> 收集全部 Package Result（第一遍，§13）
      -> 只对 Single-Package PASS Candidate 执行 validate_cross_package()
         （第二遍：student_id / task_id / archive 唯一性，§14/§28–§30）
      -> final_status（ACCEPTABLE / REJECT / NOT_RUN，§15）
      -> 机器报告（reporting.py：summary.json / package_results.jsonl /
         batch_conflicts.json / validation_report.xlsx，§16）

硬规则：

- 学生本地 validation 状态绝不作为 server truth；每个 archive 从零重验；
- 跨包 reject 条件只作用于 Single-Package PASS Candidate（v2 §27）；
- 所有可解析包中观察到的重复记录进 diagnostics（供助教人工排查），
  与正式 reject 条件分离；
- Batch Validator 只读，不修复任何包。
"""
from __future__ import annotations

from ..archive import validate_submission_archive
from .collector import aggregate, find_archives
from .validator import validate_archive, validate_batch, validate_cross_package
from .excel_report import write_batch_xlsx

__all__ = [
    "aggregate",
    "find_archives",
    "validate_archive",
    "validate_batch",
    "validate_cross_package",
    "validate_submission_archive",
    "write_batch_xlsx",
]