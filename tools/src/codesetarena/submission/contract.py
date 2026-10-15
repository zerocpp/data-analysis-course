"""Submission Contract —— 学生本地与服务器端共用的唯一 Base Submission Contract。

本模块只定义「协议」，不实现任何校验逻辑：

- 提交根目录固定结构（Base Contract）：所有 Phase 共享同一套路径；Phase 2 是
  Base 的严格超集（额外文件不得改变 Base 路径）。
- Archive 命名协议：``<course>_P<phase>_<student_id>.zip``。
- 字段所有权策略（Field Ownership Policy）：
    A. Student-authored：服务器 VALIDATE + PRESERVE，绝不替学生重写；
    B. Identity / cross-validated：student_id / task_id 做外-内-Roster 一致性
       校验；通过后 canonical 可由 server authoritative identity 重写；
    C. Server-owned / derived：学生填写任何值都不具权威性，服务器
       IGNORE + REGENERATE + OVERWRITE。
- 错误码与 Validation Layer 词汇（validate_base_submission 等实现引用）。

协议版本：``codesetarena.submission.v1``（变更即破坏性变更，需同步 bump
server_schema_version / server_validation_version）。
"""
from __future__ import annotations

import re

SCHEMA_VERSION = "codesetarena.submission.v1"
SERVER_METADATA_SCHEMA_VERSION = "codesetarena.server_metadata.v1"
VALIDATION_REPORT_SCHEMA_VERSION = "codesetarena.validation_report.v1"
SERVER_VALIDATION_VERSION = "1"

DEFAULT_COURSE_CODE = "BigData"

PHASE1 = "phase1"
PHASE2 = "phase2"
VALID_PHASES = (PHASE1, PHASE2)

# ---------------------------------------------------------------------------
# Base Submission Contract 固定路径（Phase1/Phase2 完全一致，禁止按 Phase 分叉）
# ---------------------------------------------------------------------------
MANIFEST = "manifest.json"
TASK_DIR = "task"
TASK_JSON = "task/task.json"
TASK_DATA_DIR = "task/data"
REFERENCE = "task/reference.py"  # 可选（学生自写 gold；Phase1 作者任务通常存在）
AGENT_DIR = "agent"
AGENT_MANIFEST = "agent/agent_manifest.json"
AGENT_ENTRYPOINT = "agent/agent.py"
ENV_EXAMPLE = ".env.example"  # 可选（无机密模板）

BASE_REQUIRED_FILES = (MANIFEST, TASK_JSON, AGENT_MANIFEST, AGENT_ENTRYPOINT)

# Phase 2 专属扩展路径（Base 之外的新增内容；v10.1.14 两阶段协议冻结）
# - review-comments.json : 唯一审稿记录（单文件顶层字典，替代旧 reviews/*.json 多文件协议）
# - 实验报告.docx          : 学生基于课程模板填写的 Word 实验报告（替代旧 final_report.md）
# - assignment.json       : 分配权威（anonymous_tasks 列表），审稿一致性的对照源
PHASE2_REVIEW_COMMENTS = "review-comments.json"
PHASE2_EXPERIMENT_REPORT = "实验报告.docx"
PHASE2_EXPERIMENT_REPORT_TEMPLATE = "实验报告模板.docx"
PHASE2_ASSIGNMENT = "assignment.json"

# 学生本地 advisory 校验产物（服务器唯一允许作为「非权威参考」读入的本地产物）
LOCAL_VALIDATION = "local_validation.json"

# 服务器预留路径：学生包中出现即 RESERVED_SERVER_PATH（服务器绝不使用其中内容）
RESERVED_SERVER_PATHS = (
    "validation/",
    "registry/",
    "assignment/",
    "coverage/",
    "canonical/",
    "server_metadata.json",
    "server_validation_report.json",
)

# Archive 命名协议：<course>_P<phase>_<student_id>.<ext>（正式：BigData_P1_20260001.zip /
# BigData_P2_20260001.zip）。**BigData v10.1.10 起正式提交格式为 .zip**（Two
# Submissions, One Exposure 契约）；.tar.gz 保留为 legacy 兼容格式（接收端仍可验证）。
# 允许教师端补交重传后缀 _v<digits>（外层身份仍是 student_id 本体，内层必须一致）。
ARCHIVE_EXTENSIONS = (".zip", ".tar.gz")
FORMAL_ARCHIVE_EXTENSION = ".zip"
LEGACY_ARCHIVE_EXTENSION = ".tar.gz"
ARCHIVE_NAME_PATTERN = re.compile(
    r"^(?P<course>[A-Za-z0-9_]+)_P(?P<phase>[12])_"
    r"(?P<student_id>[0-9A-Za-z_]{2,32}?)(?:_v\d+)?\.(?:zip|tar\.gz)$"
)

# task_id 合法形态：T_<uuid4hex>（Export/canonicalize 生成）或旧式字母数字编号（DA01 等）
TASK_ID_PATTERN = re.compile(r"^(?:T_[0-9a-f]{32}|[A-Za-z0-9_]{2,64})$")

# student_id 合法形态：课程学号（数字为主，允许少量字母/下划线）
STUDENT_ID_PATTERN = re.compile(r"^[0-9A-Za-z_]{2,32}$")

# ---------------------------------------------------------------------------
# Validation Layer
# ---------------------------------------------------------------------------
LAYER_IDENTITY = "identity"
LAYER_SAFE_EXTRACT = "safe_extract"
LAYER_BASE = "base"
LAYER_PACKAGE = "package_name"
LAYER_P1_EXTENSION = "phase1_extension"
LAYER_P2_EXTENSION = "phase2_extension"
LAYER_BATCH = "server_batch"
LAYER_WORKFLOW = "workflow"
LAYER_INGEST = "ingest"

# Batch（单包 PASS Candidate 之上）跨包层错误码：学生端永远看不到
BATCH_STUDENT_ID_DUPLICATE = "DUPLICATE_ACTIVE_STUDENT_ID"
BATCH_TASK_ID_DUPLICATE = "DUPLICATE_TASK_ID"
BATCH_ARCHIVE_NAME_DUPLICATE = "DUPLICATE_ARCHIVE"

# 校验通过的组合报告里 validation_layer 的取值
LAYER_ALL = "all"

# ---------------------------------------------------------------------------
# Base 错误码（validation_layer = base / identity / safe_extract）
# ---------------------------------------------------------------------------
MALFORMED_JSON = "MALFORMED_JSON"
MISSING_MANIFEST = "MISSING_MANIFEST"
BAD_MANIFEST = "BAD_MANIFEST"
INVALID_SUBMISSION_PHASE = "INVALID_SUBMISSION_PHASE"
MISSING_STUDENT_ID = "MISSING_STUDENT_ID"
INVALID_STUDENT_ID = "INVALID_STUDENT_ID"
MISSING_TASK_ID = "MISSING_TASK_ID"
INVALID_TASK_ID = "INVALID_TASK_ID"
MISSING_TASK_JSON = "MISSING_TASK_JSON"
TASK_ID_MISMATCH = "TASK_ID_MISMATCH"
MISSING_TASK_DATA = "MISSING_TASK_DATA"
DATA_TOO_LARGE = "DATA_TOO_LARGE"
MISSING_AGENT = "MISSING_AGENT"
MISSING_AGENT_MANIFEST = "MISSING_AGENT_MANIFEST"
BAD_AGENT_MANIFEST = "BAD_AGENT_MANIFEST"
INVALID_FM = "INVALID_FM"
STUDENT_ID_MISMATCH = "STUDENT_ID_MISMATCH"
RESERVED_SERVER_PATH = "RESERVED_SERVER_PATH"

# identity layer
INVALID_ARCHIVE_NAME = "INVALID_ARCHIVE_NAME"
UNKNOWN_STUDENT_ID = "UNKNOWN_STUDENT_ID"
SUBMISSION_PHASE_MISMATCH = "SUBMISSION_PHASE_MISMATCH"

# safe_extract layer
ZIP_PATH_TRAVERSAL = "ZIP_PATH_TRAVERSAL"
ZIP_EXTRACT_ERROR = "ZIP_EXTRACT_ERROR"

# safe_extract layer（tar.gz 正式格式；与 ZIP 同一套安全语义，另有硬链接/设备文件拒绝）
TAR_PATH_TRAVERSAL = "TAR_PATH_TRAVERSAL"
TAR_UNSAFE_LINK = "TAR_UNSAFE_LINK"
TAR_EXTRACT_ERROR = "TAR_EXTRACT_ERROR"
TAR_ARCHIVE_BOMB = "TAR_ARCHIVE_BOMB"

# Structure layer：学生提交包禁止出现的顶层路径（ERROR；build 自动排除，validate 拒绝）。
# .env.example（无机密模板）与 local_validation.json（本地 advisory 产物）不属于禁止项。
FORBIDDEN_FILE = "FORBIDDEN_FILE"
FORBIDDEN_TOP_LEVEL = (
    ".env",
    "runs/",
    "__pycache__/",
    ".git/",
    ".pytest_cache/",
    "*.pyc",
)

# Submission 边界（Configuration / Policy Separation，§15–§17/§37）：
# - .env（真实 Secret）与 runtime_policy.json（课程系统配置）禁止进入提交包（ERROR）；
# - .env.example（连接配置模板）也非 Submission Artifact：Build 自动排除；
#   legacy 冻结 corpus 包内含 .env.example（兼容期）-> WARNING（DEV_TEMPLATE_FILE）。
FORBIDDEN_SUBMISSION_FILES = (
    ".env",
    "runtime_policy.json",
)
DEV_TEMPLATE_FILE = "DEV_TEMPLATE_FILE"

# Package Secret Guard（v10.1.10 §29）：代码级强校验，不依赖「正常情况下不会 copy」。
# 命中任意 pattern（顶层或任意子路径，fnmatch 大小写不敏感）=> ERROR SECRET_FILE。
SECRET_FILE = "SECRET_FILE"
SECRET_GUARD_PATTERNS = (
    ".env",
    ".env.*",
    "*.key",
    "*.pem",
    "*.p12",
    "*.pfx",
    "id_rsa*",
    "credentials",
    "credentials.*",
    "secret",
    "secret.*",
    "*.secret",
    "secrets.*",
)

# Phase 1 extension（作者任务契约：task_statement + hypothesized_failure_mode 必填）
MISSING_TASK_STATEMENT = "MISSING_TASK_STATEMENT"
MISSING_HYPOTHESIZED_FM = "MISSING_HYPOTHESIZED_FM"

# Phase 2 extension（v10.1.14：实验报告 + 唯一 review-comments.json）
MISSING_EXPERIMENT_REPORT = "MISSING_EXPERIMENT_REPORT"
INVALID_EXPERIMENT_REPORT = "INVALID_EXPERIMENT_REPORT"
MISSING_REVIEW_COMMENTS = "MISSING_REVIEW_COMMENTS"
REVIEW_COMMENTS_NOT_OBJECT = "REVIEW_COMMENTS_NOT_OBJECT"
DUPLICATE_REVIEW_TASK_ID = "DUPLICATE_REVIEW_TASK_ID"
MISSING_REVIEW_TASK = "MISSING_REVIEW_TASK"
UNKNOWN_REVIEW_TASK = "UNKNOWN_REVIEW_TASK"
REVIEW_TASK_ID_MISMATCH = "REVIEW_TASK_ID_MISMATCH"
REVIEW_FIELD_MISSING = "REVIEW_FIELD_MISSING"
REVIEW_FIELD_UNKNOWN = "REVIEW_FIELD_UNKNOWN"
INVALID_REVIEW_SCORE = "INVALID_REVIEW_SCORE"
INVALID_REVIEW_COMMENTS = "INVALID_REVIEW_COMMENTS"
INVALID_REVIEW_STRUCTURE = "INVALID_STRUCTURE"
MISSING_ASSIGNMENT = "MISSING_ASSIGNMENT"
INVALID_ASSIGNMENT = "INVALID_ASSIGNMENT"

# server batch / workflow
DUPLICATE_TASK_ID = "DUPLICATE_TASK_ID"
DUPLICATE_ACTIVE_STUDENT_ID = "DUPLICATE_ACTIVE_STUDENT_ID"
MISSING_SUBMISSION = "MISSING_SUBMISSION"
DUPLICATE_ARCHIVE = "DUPLICATE_ARCHIVE"

# ingest layer
INGEST_NOT_ACCEPTED = "INGEST_NOT_ACCEPTED"  # 只有 Single+Batch 都 PASS 才允许入库
INGEST_ALREADY_KNOWN = "INGEST_ALREADY_KNOWN"  # 同一 archive 已入库（幂等 SKIP）

# ---------------------------------------------------------------------------
# Field Ownership Policy（字段所有权）
# ---------------------------------------------------------------------------
# A. Student-authored：服务器只 VALIDATE + PRESERVE
STUDENT_AUTHORED_FIELDS = (
    "task_statement",
    "hypothesized_failure_mode",  # 只校验 taxonomy 合法性，不重新分类
    "description",
    "task data",        # task/data/*
    "agent implementation",  # agent/agent.py
    "reference.py",     # task/reference.py（Phase1 学生自写 gold）
    "review-comments.json",  # Phase2 唯一审稿记录（单文件顶层字典）
    "experiment report",     # Phase2 实验报告（Word）
)

# B. Identity / cross-validated：外-内-Roster 一致性验证后 canonical 可重写
IDENTITY_FIELDS = ("student_id", "task_id")

# C. Server-owned：学生填写一律 IGNORE + REGENERATE + OVERWRITE
SERVER_OWNED_FIELDS = (
    "validation_status",
    "build_status",
    "received_timestamp",
    "submission_batch",
    "canonical_path",
    "package_sha256",
    "server_schema_version",
    "server_validation_version",
    "accepted",
    "registry",
    "coverage",
    "assignment",
    "anonymous id",
    "server execution metadata",
)

# manifest.json 中被服务器忽略的学生自填 server 字段（警告级，不阻断）
IGNORED_STUDENT_SERVER_FIELDS = (
    "validation_status",
    "build_passed",
    "server_status",
    "accepted",
    "received_timestamp",
    "package_sha256",
    "submission_batch",
    "server_schema_version",
)

# ---------------------------------------------------------------------------
# 目录布局（pool root 下）
# ---------------------------------------------------------------------------
RAW = "raw"
SUBMISSIONS = "submissions"
CANONICAL = "canonical"
VALIDATION = "validation"
REGISTRY = "registry"
ASSIGNMENT = "assignment"
COVERAGE = "coverage"

# ---------------------------------------------------------------------------
# Resubmission（v10.1.12）—— raw 单文件 active 槽位 + history/audit 语义
# ---------------------------------------------------------------------------
# pool/raw/<archive>            = 该 sid+phase 的唯一 ACTIVE 槽位
# pool/history/rejected/        = 校验失败包存档（<stem>__<sha8><ext>），不占 active 槽
# pool/history/superseded/      = 被 --supersede 替换的旧 ACTIVE 存档
# pool/audit/submission_attempts.jsonl = 每次 receive 一行的 append-only 审计
# pool/FROZEN                   = 冻结标记（存在 => 任何 receive 返回 PHASE_FROZEN）
HISTORY = "history"
HISTORY_REJECTED = "history/rejected"
HISTORY_SUPERSEDED = "history/superseded"
AUDIT = "audit"
ATTEMPTS_LOG = "submission_attempts.jsonl"
FREEZE_MARKER = "FROZEN"

PHASE_FROZEN = "PHASE_FROZEN"
ACTIVE_SUBMISSION_EXISTS = "ACTIVE_SUBMISSION_EXISTS"

POOL_SUBDIRS = (
    RAW, SUBMISSIONS, CANONICAL, VALIDATION,
    HISTORY_REJECTED, HISTORY_SUPERSEDED, AUDIT,
)


def phase_dir(phase: str) -> str:
    """submissions 下的阶段子目录名（phase1 / phase2）。"""

    return "phase1" if phase == PHASE1 else "phase2"