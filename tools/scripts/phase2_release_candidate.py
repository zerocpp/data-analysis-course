"""CodeSetArena Phase F —— Phase1 Final Acceptance + Phase2 Release Candidate CLI。

职责仅 orchestration（§87）；底层全部复用正式 Submission Validator / Batch
Validator / Registry / Assignment Verifier / Anonymous Builder / Archive
Builder / Leakage Scanner。

用法::

    .venv/bin/python scripts/phase2_release_candidate.py \
        --workspace workspace/cohort_dryrun_200 \
        --assignment-run AR000001 \
        [--expect-assignment-sha256 <sha256>]

边界：不重新生成 Phase1 Submission、不重跑 solver、不写正式 distributions/、
不执行任何发放动作（终态 AWAITING_EXPLICIT_RELEASE）。

退出码：0 = PHASE1_FINAL_VALIDATED + RELEASE_CANDIDATE_VERIFIED；
1 = 任一 Gate FAIL（结构化）；2 = 内部/配置错误。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from codesetarena.release.contract import PhaseFError  # noqa: E402
from codesetarena.release.orchestrator import run_phase_f  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase F Release Gate orchestrator")
    parser.add_argument("--workspace", default="workspace/cohort_dryrun_200",
                        help="Phase E.1 工作区（含 submissions/ 与 repository/）")
    parser.add_argument("--assignment-run", default="AR000001")
    parser.add_argument("--expected-archives", type=int, default=200)
    parser.add_argument("--reviews-per-task", type=int, default=5)
    parser.add_argument("--expect-assignment-sha256", default=None,
                        help="冻结的 assignment.npy SHA256（§23）")
    parser.add_argument("--deterministic-sample", type=int, default=10)
    parser.add_argument("--spot-check", type=int, default=10)
    parser.add_argument("--json-output", default=None)
    args = parser.parse_args(argv)

    started = time.time()

    def log(message: str) -> None:
        print(f"[phase-f] {message}", flush=True)

    try:
        result = run_phase_f(
            args.workspace,
            assignment_run_id=args.assignment_run,
            expected_archive_count=args.expected_archives,
            reviews_per_task=args.reviews_per_task,
            expected_matrix_sha256=args.expect_assignment_sha256,
            deterministic_sample=args.deterministic_sample,
            spot_check_count=args.spot_check,
            log=log,
        )
    except PhaseFError as exc:
        print(f"[phase-f] GATE FAIL gate={exc.gate or '-'} {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — CLI 顶层兜底
        print(f"[phase-f] INTERNAL_ERROR {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    _print_summary(result, time.time() - started)

    if args.json_output:
        path = Path(args.json_output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return 0


def _print_summary(result: dict, elapsed: float) -> None:
    phase1 = result["phase1"]
    rc = result["phase2_rc"]
    line = "=" * 60
    print(f"\n{line}")
    print("CodeSetArena Phase F — Final Acceptance / Phase2 RC")
    print(line)
    print(f"PHASE1_ARCHIVES = {phase1['archives']}")
    print(f"PHASE1_SINGLE_PASS = {phase1['single_pass']} / {phase1['archives']}")
    print(f"PHASE1_BATCH_PASS = {phase1['batch_status']} "
          f"(conflicts={phase1['batch_conflicts']})")
    print(f"PHASE1_FINAL_ACCEPTED = {phase1['final_accepted']} / {phase1['archives']}")
    print(f"PHASE1_ARCHIVE_MUTATIONS = {phase1['archive_mutations']}")
    print(f"CANONICAL_PROVENANCE = {phase1['canonical_provenance']}")
    print(f"ASSIGNMENT_RUN = {rc['assignment_run_id']}")
    print(f"ASSIGNMENT_ARTIFACT_HASH = {rc['assignment_matrix_sha256']}")
    print("ASSIGNMENT_REVERIFY = PASS")
    print(f"REVIEWERS = {rc['reviewers']}")
    print(f"TASKS = {rc['tasks']}")
    print(f"R = {rc['r']}")
    print(f"RC_PACKAGES = {rc['rc_packages']}")
    print(f"TASKS_PER_PACKAGE_MIN = {rc['tasks_per_package_min']}")
    print(f"TASKS_PER_PACKAGE_MAX = {rc['tasks_per_package_max']}")
    print(f"TOTAL_TASK_EXPOSURES = {rc['total_task_exposures']}")
    print(f"TASK_EXPOSURE_MIN = {rc['task_exposure_min']}")
    print(f"TASK_EXPOSURE_MAX = {rc['task_exposure_max']}")
    print(f"SELF_REVIEW = {rc['self_review']}")
    print(f"DUPLICATE_ASSIGNMENTS = {rc['duplicate_assignments']}")
    print(f"REVERSE_RECONSTRUCTION = "
          f"{'PASS' if rc['reconstruction_diff'] == 0 else 'FAIL'}")
    print(f"RECONSTRUCTION_DIFF = {rc['reconstruction_diff']}")
    print(f"IDENTITY_LEAKS = {rc['identity_leaks']}")
    print(f"PRIVATE_CONTENT_LEAKS = {rc['private_content_leaks']}")
    print(f"SECRET_LEAKS = {rc['secret_leaks']}")
    print(f"LOCAL_PATH_LEAKS = {rc['local_path_leaks']}")
    print(f"PHASE1_SET_SHA256 = {rc['phase1_submission_set_sha256']}")
    print(f"ASSIGNMENT_SHA256 = {rc['assignment_matrix_sha256']}")
    print(f"PHASE2_RC_SET_SHA256 = {rc['phase2_release_candidate_set_sha256']}")
    print(f"FORMAL_DISTRIBUTION_CREATED = {rc['formal_distribution_created']}")
    print(f"PACKAGES_RELEASED = {rc['packages_released']}")
    print(f"PHASE1_STATUS = {phase1['phase1_status']}")
    print(f"PHASE2_RC_STATUS = {rc['phase2_rc_status']}")
    print(f"RELEASE_STATUS = {rc['next_action']}")
    print(f"ELAPSED_SECONDS = {elapsed:.1f}")
    print(line)


if __name__ == "__main__":
    raise SystemExit(main())
