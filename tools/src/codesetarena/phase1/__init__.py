"""Phase 1 正式运行流程（教师端 Batch Build → Validator → Registry → FM Coverage → Frozen Main Pool → Assignment）。

数据流单向（Assignment 唯一合法输入是 validated registry / frozen main pool，
禁止直接扫描 raw ZIP）::

    Export (Task_ID + hypothesized_failure_mode 固化)
        -> Raw Submission（不可变）
        -> Canonical Working Copy
        -> Batch Validator（只读，全局唯一性检查）
        -> Task Registry（phase1_task_registry.csv + owner mappings）
        -> FM Distribution（FM01-FM14 固定顺序，count=0 也保留）
        -> Frozen Main Pool（不可变快照）
        -> Assignment（main = FM-Stratified Sequential Min-Count Sampling；
                       late = 同一 sampling policy 增量）

模块：

- prepare.py      原始提交 -> canonical 工作副本（只拷运行文件，缺失生成 T_ ID）
- validation.py   Batch Validator（单包 + cohort 全局检查，严格只读）
- registry.py     Task Registry 与 task->owner / student->tasks mapping
- coverage.py     FM Distribution（CSV / JSON / PNG，main 与 current 快照）
- snapshot.py     Main Assignment Pool 冻结 / 加载
- assignment.py   FM 分层顺序采样分配（main）+ 同一策略 late 增量 + 最终双向 mapping
- optimize.py     输出落盘 + Diversity / Task Exposure Audit + Summary（含 legacy 层）
- audit.py        task_id_corrections.jsonl 审计日志
"""

from .audit import append_task_id_correction
from .assignment import (
    ASSIGNMENT_SUMMARY_JSON,
    DEFAULT_REVIEWS_PER_STUDENT,
    LATE_EXACT_METHOD,
    LATE_GREEDY_METHOD,
    LATE_METHOD,
    MAIN_METHOD,
    REVIEWS_PER_STUDENT,
    TASK_EXPOSURE_CSV,
    AssignmentCapacityError,
    InsufficientMainPoolError,
    assign_late,
    assign_main,
    check_main_constraints,
    merge_final_mappings,
    records_after_main,
    task_exposure_stats,
    verify_main_unchanged,
    verify_reverse_consistency,
    write_assignment_outputs,
    write_assignment_summary,
    write_task_exposure_audit,
)
from .coverage import build_fm_distribution, load_fm_distribution
from .optimize import (
    ASSIGNMENT_DIVERSITY_CSV,
    ASSIGNMENT_OPTIMIZATION_JSON,
    OBJECTIVE_NAME,
    OPTIMIZED_METHOD,
    OPTIMIZER_STDLIB,
    assert_hard_invariants,
    build_main_records,
    compute_assignment_objective,
    compute_diversity_stats,
    compute_reviewer_diversity,
    diversity_upper_bounds,
    optimize_main_assignment,
    run_main_assignment_pipeline,
    write_diversity_audit,
    write_optimization_summary,
)
from .prepare import ensure_task_id, prepare_canonical
from .registry import (
    append_registry_rows,
    build_registry,
    load_registry,
    write_registry,
)
from .snapshot import freeze_main_pool, load_main_pool
from .validation import (
    DUPLICATE_ACTIVE_STUDENT_ID,
    DUPLICATE_TASK_ID,
    INVALID_FM,
    MALFORMED_JSON,
    MISSING_FIELD,
    STUDENT_ID_MISMATCH,
    scan_canonical,
    validate_batch,
)

__all__ = [
    "ASSIGNMENT_DIVERSITY_CSV",
    "ASSIGNMENT_OPTIMIZATION_JSON",
    "ASSIGNMENT_SUMMARY_JSON",
    "DEFAULT_REVIEWS_PER_STUDENT",
    "LATE_EXACT_METHOD",
    "LATE_GREEDY_METHOD",
    "LATE_METHOD",
    "MAIN_METHOD",
    "OBJECTIVE_NAME",
    "OPTIMIZED_METHOD",
    "OPTIMIZER_STDLIB",
    "REVIEWS_PER_STUDENT",
    "TASK_EXPOSURE_CSV",
    "AssignmentCapacityError",
    "InsufficientMainPoolError",
    "DUPLICATE_ACTIVE_STUDENT_ID",
    "DUPLICATE_TASK_ID",
    "INVALID_FM",
    "MALFORMED_JSON",
    "MISSING_FIELD",
    "STUDENT_ID_MISMATCH",
    "append_registry_rows",
    "append_task_id_correction",
    "assert_hard_invariants",
    "assign_late",
    "assign_main",
    "build_fm_distribution",
    "build_main_records",
    "build_registry",
    "check_main_constraints",
    "compute_assignment_objective",
    "compute_diversity_stats",
    "compute_reviewer_diversity",
    "diversity_upper_bounds",
    "ensure_task_id",
    "freeze_main_pool",
    "load_fm_distribution",
    "load_main_pool",
    "load_registry",
    "merge_final_mappings",
    "optimize_main_assignment",
    "prepare_canonical",
    "records_after_main",
    "run_main_assignment_pipeline",
    "scan_canonical",
    "task_exposure_stats",
    "validate_batch",
    "verify_main_unchanged",
    "verify_reverse_consistency",
    "write_assignment_outputs",
    "write_assignment_summary",
    "write_diversity_audit",
    "write_optimization_summary",
    "write_registry",
    "write_task_exposure_audit",
]