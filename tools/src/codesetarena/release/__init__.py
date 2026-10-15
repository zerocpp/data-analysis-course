"""Phase F —— Phase1 Final Acceptance + Phase2 Release Candidate（Release Gate）。

职责边界（本轮「验证不发放」）：

- Gate A：对**已存在**的 200 个 Phase1 Submission Archive 从原始字节重新执行
  独立校验（不复用历史 PASS），冻结 Archive SHA256 + Final Acceptance Manifest；
- Gate B：仅在 Gate A 200/200 后，基于已冻结的 Assignment Run（AR######）
  用**正式** Reviewer Package builder 构建与未来发布字节一致的 Release
  Candidate，并完成包校验 / No-Self / Exposure / 反向重建 / 泄露扫描 / 哈希冻结。

硬边界：

- 不重新生成 Phase1 Submission、不重跑 solver、不重建 canonical；
- 绝不写正式 ``distributions/<AR>/reviewers/``，RC 只落在
  ``release_candidates/`` 隔离区；
- 终态只能是 ``RELEASE_CANDIDATE_VERIFIED`` + ``AWAITING_EXPLICIT_RELEASE``，
  不产生任何学生可见发放。

模块：``contract``（状态/文件名/Gate 表）、``inventory``（Archive 清单与
SHA 冻结）、``final_validation``（Gate A）、``candidate``（Gate B）、
``leakage``（身份/私有/Secret/本地路径扫描）、``lineage``（Set Hash +
Release Lineage）、``reporting``（JSON/Excel）、``orchestrator``（编排）。
"""

from codesetarena.release.contract import (  # noqa: F401
    ASSIGNMENT_ARTIFACT_DRIFT,
    ASSIGNMENT_INPUT_DRIFT,
    CANONICAL_DRIFT,
    GATES,
    PHASE1_FINAL_VALIDATED,
    PHASE1_VALIDATION_FAILED,
    RC_STATUS_FAILED,
    RC_STATUS_VERIFIED,
    RELEASE_AWAITING_EXPLICIT,
    RELEASE_NOT_RELEASED,
    PhaseFError,
)

__all__ = [
    "ASSIGNMENT_ARTIFACT_DRIFT",
    "ASSIGNMENT_INPUT_DRIFT",
    "CANONICAL_DRIFT",
    "GATES",
    "PHASE1_FINAL_VALIDATED",
    "PHASE1_VALIDATION_FAILED",
    "RC_STATUS_FAILED",
    "RC_STATUS_VERIFIED",
    "RELEASE_AWAITING_EXPLICIT",
    "RELEASE_NOT_RELEASED",
    "PhaseFError",
]
