"""Submission Contract + Trust Boundary 子系统。

架构原则（v2 §0/§45）：One Package Contract / One Single-Package Validator /
Two Independent Executions / One Server-only Batch Validator。

系统按**功能**组织，不按角色：

- build           = prepare_staging -> validate_single_package -> pack（tar.gz 正式格式）
- validate        = Archive Intake -> safe extract -> validate_single_package
- batch-validate  = N × validate_submission_archive -> validate_cross_package -> report
- ingest          = Accepted -> canonical Task / Agent + Private Registry +
                    Anonymous Mapping + Anonymous Distribution Views

学生本地（Advisory）与服务器端（Authoritative）共用同一份 Base Submission
Contract（``contract.py``）与同一份 ``validate_base_submission``；阶段扩展只
检查该阶段在 Base 之上「新增」的内容（``extension_validation.py``）。

模块（功能划分）：

- contract.py              协议常量 / 路径 / 字段所有权 / 错误码（唯一事实来源）
- safe_extract.py          统一安全解压（zip + tar.gz）+ SHA256 验证
- archive.py               正式 *.tar.gz 构建 + Archive Intake + validate_submission_archive
- base_validation.py       validate_base_submission（唯一 Base Validator）
- extension_validation.py  Phase Extension + Dispatch + validate_single_package
- models.py                统一 ValidationResult / Issue 数据结构（§9/§10）
- staging.py               prepare_submission_staging（BUILD 第一步）
- local.py                 build（学生本地入口，advisory）与本地 validate
- identity.py              外-内-Roster 身份一致性（server admission）
- receiver.py              legacy 服务器 Receive Pipeline（raw 不可变 + canonical build）
- batch_validation.py      legacy cohort 全局校验（server artifact 驱动）
- workflow.py              跨 Phase 纵向一致性
- batch/                   batch-validate：第一遍单包 + 第二遍跨包 + Excel
- registry.py              Private Registry（submission / anonymous / assignments）
- anonymization.py         匿名 Task / Agent 分发视图 + 泄露扫描
- ingest.py                ingest：canonical Task/Agent 拆分 + 私有映射
- reporting.py             机器可读落盘（summary.json / package_results.jsonl / ...）
"""
from __future__ import annotations

from .archive import (
    create_submission_archive,
    validate_archive_envelope,
    validate_submission_archive,
)
from .base_validation import validate_base_submission
from .extension_validation import (
    validate_phase1_extension,
    validate_phase2_extension,
    validate_single_package,
    validate_submission,
)
from .identity import (
    IdentityError,
    collect_inner_identity,
    cross_check_identity,
    load_roster,
    parse_archive_name,
)
from .local import SubmissionBuildError, build_submission, validate_local_zip
from .receiver import ensure_pool_layout, receive_submission, write_report
from .safe_extract import (
    SafeExtractError,
    safe_extract,
    safe_extract_archive,
    safe_extract_tar,
    sha256_file,
    verify_sha256,
)

__all__ = [
    "IdentityError",
    "SafeExtractError",
    "SubmissionBuildError",
    "build_submission",
    "collect_inner_identity",
    "create_submission_archive",
    "cross_check_identity",
    "ensure_pool_layout",
    "load_roster",
    "parse_archive_name",
    "receive_submission",
    "safe_extract",
    "safe_extract_archive",
    "safe_extract_tar",
    "sha256_file",
    "validate_archive_envelope",
    "validate_base_submission",
    "validate_local_zip",
    "validate_phase1_extension",
    "validate_phase2_extension",
    "validate_single_package",
    "validate_submission",
    "validate_submission_archive",
    "verify_sha256",
    "write_report",
]