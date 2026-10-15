"""Phase F 契约：状态、输出文件名、Gate 表（§63/§70/§71）。

只放常量与稳定词汇，不放逻辑。
"""

from __future__ import annotations

CONTRACT_ID = "codesetarena.release.phase_f.v1"
SCHEMA_VERSION = "phase_f.v1"

# ---------------------------------------------------------------------------
# 状态（§18/§63/§64：验证 ≠ 发放）
# ---------------------------------------------------------------------------

PHASE1_FINAL_VALIDATED = "PHASE1_FINAL_VALIDATED"
PHASE1_VALIDATION_FAILED = "PHASE1_FINAL_VALIDATION_FAILED"
RC_STATUS_VERIFIED = "RELEASE_CANDIDATE_VERIFIED"
RC_STATUS_FAILED = "RELEASE_CANDIDATE_FAILED"
RELEASE_AWAITING_EXPLICIT = "AWAITING_EXPLICIT_RELEASE"
RELEASE_NOT_RELEASED = "NOT_RELEASED"

# 结构化失败码（§17/§21/§23）
CANONICAL_DRIFT = "PHASE1_CANONICAL_DRIFT"
ASSIGNMENT_INPUT_DRIFT = "ASSIGNMENT_INPUT_DRIFT"
ASSIGNMENT_ARTIFACT_DRIFT = "ASSIGNMENT_ARTIFACT_DRIFT"
ARCHIVE_MUTATION = "PHASE1_ARCHIVE_MUTATION"
ARCHIVE_COUNT_MISMATCH = "PHASE1_ARCHIVE_COUNT_MISMATCH"

RC_DIR_NAME = "release_candidates"


class PhaseFError(RuntimeError):
    """Release Gate 结构化失败（携带 gate / code）。"""

    def __init__(self, code: str, message: str, *, gate: str | None = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.gate = gate
        self.message = message


# ---------------------------------------------------------------------------
# Gate 表（§70）
# ---------------------------------------------------------------------------

GATES: tuple[tuple[str, str], ...] = (
    ("G01", "Phase1 Archive Count = 200"),
    ("G02", "Phase1 Single Validation 200/200"),
    ("G03", "Phase1 Cross-Package Validation"),
    ("G04", "Canonical Provenance"),
    ("G05", "Assignment Artifact Integrity"),
    ("G06", "Assignment Verifier Re-Run"),
    ("G07", "RC Package Count = 200"),
    ("G08", "RC Package Validation 200/200"),
    ("G09", "No-Self (Package Level)"),
    ("G10", "Task Exposure = 10"),
    ("G11", "Reverse Reconstruction Bit-Exact"),
    ("G12", "Identity Leakage = 0"),
    ("G13", "Private Content Leakage = 0"),
    ("G14", "Secret Leakage = 0"),
    ("G15", "Local Path Leakage = 0"),
    ("G16", "Hash Freeze Complete"),
)

GATE_IDS = tuple(gid for gid, _ in GATES)

# ---------------------------------------------------------------------------
# 输出文件名（§12/§14/§57–§58/§65/§71/§72/§73/§80–§83）
# ---------------------------------------------------------------------------

PHASE1_ARCHIVE_INVENTORY = "phase1_archive_inventory.jsonl"
PHASE1_FINAL_ACCEPTANCE_MANIFEST = "phase1_final_acceptance_manifest.jsonl"
PHASE1_FINAL_VALIDATION_REPORT = "phase1_final_validation_report.xlsx"
PHASE1_FINAL_SUMMARY = "phase1_final_validation_summary.json"
PHASE1_SET_HASH_KEY = "phase1_submission_set_sha256"

RC_MANIFEST = "phase2_release_candidate_manifest.jsonl"
RC_PUBLIC_MANIFEST = "phase2_rc_public_manifest.jsonl"
RC_PRIVATE_MANIFEST = "phase2_rc_private_manifest.jsonl"
RC_REPORT = "phase2_release_candidate_report.xlsx"
RC_SUMMARY = "phase2_rc_summary.json"
RC_LEAKAGE_REPORT = "phase2_rc_leakage_report.json"
RC_RECONSTRUCTION_REPORT = "phase2_rc_reconstruction_report.json"
RC_SPOT_CHECK = "rc_spot_check.json"
RC_SET_HASH_KEY = "phase2_release_candidate_set_sha256"

PHASE1_LOG = "phase1_final_validation.log"
RC_BUILD_LOG = "phase2_rc_build.log"
RC_VERIFY_LOG = "phase2_rc_verification.log"

RELEASE_GATE = "release_gate.json"
RELEASE_LINEAGE = "release_lineage.json"
