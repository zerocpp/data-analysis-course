"""``codesetarena assignment`` CLI（功能性 mode，与 submission CLI 风格一致）。

模式：
- build      从 canonical repository 生成验证过的 assignment run（VERIFIED）
- distribute 对 VERIFIED run 建立私有映射 + 匿名分发 + 校验 + 泄露扫描（READY）
- prepare    build + distribute 一键编排（底层功能独立可调，不耦合）
- verify     重放已有 run 的 verifier / distribution 校验（审计）
- report     生成管理端 assignment_report.xlsx

退出码：0 = 成功/READY；1 = assignment/distribution 失败（结构化错误）；
2 = 配置 / 内部错误。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from codesetarena.assignment import reporting as report_mod
from codesetarena.assignment import run as run_mod
from codesetarena.assignment.contract import RunStatus, AssignmentError

MODES = ("build", "distribute", "prepare", "verify", "report",
         "export-exposure")


def add_subparser(commands) -> None:
    parser = commands.add_parser(
        "assignment",
        help="Peer-Review Assignment Pipeline：canonical repository -> 匿名 reviewer 分发",
    )
    parser.add_argument(
        "--mode", required=True, choices=MODES,
        help="build: 生成验证过的 assignment；distribute: 私有映射+匿名分发；"
             "prepare: build+distribute；verify: 重放校验；report: 管理端 Excel",
    )
    parser.add_argument("--repository", required=True, help="course_repository 根目录")
    parser.add_argument("--phase", default="phase1", choices=["phase1", "phase2"])
    parser.add_argument("--policy", default=None, help="assignment_policy.json 路径")
    parser.add_argument("--seed", default=None, type=int, help="assignment seed（缺省由快照派生）")
    parser.add_argument("--assignment-run", default=None, help="AR######（distribute/verify/report）")
    parser.add_argument("--json-output", default=None, help="机器可读 JSON 报告输出路径")


def cmd_assignment(args) -> int:
    repo = Path(args.repository)
    try:
        if args.mode == "build":
            record = run_mod.build_assignment_run(
                repo, args.phase, policy=args.policy, seed=args.seed,
            )
        elif args.mode == "distribute":
            if not args.assignment_run:
                raise AssignmentError("ASSIGN_RUN_REQUIRED", "distribute 需要 --assignment-run")
            record = run_mod.distribute_assignment_run(
                repo, args.assignment_run, policy=args.policy,
            )
        elif args.mode == "prepare":
            record = run_mod.prepare_assignment_run(
                repo, args.phase, policy=args.policy, seed=args.seed,
            )
        elif args.mode == "verify":
            if not args.assignment_run:
                raise AssignmentError("ASSIGN_RUN_REQUIRED", "verify 需要 --assignment-run")
            record = _verify_existing(repo, args.assignment_run)
        elif args.mode == "export-exposure":
            if not args.assignment_run:
                raise AssignmentError("ASSIGN_RUN_REQUIRED",
                                      "export-exposure 需要 --assignment-run")
            from codesetarena.assignment import distribution as dist_mod

            exported = dist_mod.export_exposure_packages(
                repo, args.assignment_run,
                out_dir=Path(args.json_output) if args.json_output else None,
            )
            record = {"assignment_run_id": args.assignment_run,
                      "status": "EXPORTED", "packages": exported,
                      "package_count": len(exported)}
            print(f"[assignment] export-exposure packages={len(exported)}")
            for item in exported:
                print(f"[assignment]   {item}")
            if args.json_output and Path(args.json_output).suffix == ".json":
                out = Path(args.json_output)
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(
                    json.dumps(record, ensure_ascii=False, indent=2) + chr(10),
                    encoding="utf-8")
            _print_summary(record)
            return 0
        elif args.mode == "report":
            if not args.assignment_run:
                raise AssignmentError("ASSIGN_RUN_REQUIRED", "report 需要 --assignment-run")
            out = report_mod.write_assignment_excel(repo, args.assignment_run)
            record = {"assignment_run_id": args.assignment_run, "report_path": str(out),
                      "status": "REPORTED"}
        else:  # pragma: no cover
            raise AssignmentError("ASSIGN_CLI_MODE", f"未知 mode {args.mode}")

        _print_summary(record)

        if args.json_output:
            out = Path(args.json_output)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")

        status = record.get("status")
        if status in (RunStatus.READY, RunStatus.VERIFIED, "REPORTED"):
            return 0
        if status in RunStatus.FINAL_FAILURES or status in ("VERIFICATION_FAILED",):
            return 1
        return 2
    except AssignmentError as exc:
        print(f"[assignment] {exc}", file=sys.stderr)
        if exc.code.startswith(("ASSIGN_INPUT_", "DIST_", "ASSIGN_VERIFY_",
                                "ASSIGN_SOLVER_", "ASSIGN_MAPPING_")):
            return 1
        return 2
    except Exception as exc:  # noqa: BLE001 — CLI 顶层兜底
        print(f"[assignment] INTERNAL_ERROR: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 2


def _verify_existing(repo: Path, run_id: str) -> dict:
    """重放已有 run：verifier（矩阵） + distribution（若已构建）独立复核。"""
    from codesetarena.assignment.verifier import verify_assignment

    record = run_mod.load_run(repo, run_id)
    run_dir = repo / "assignments" / run_id
    snapshot = json.loads((run_dir / "input_snapshot.json").read_text(encoding="utf-8"))
    matrix = __import__("numpy").load(run_dir / "assignment.npy").tolist()
    case = {
        "case_id": run_id,
        "num_students": record["num_reviewers"],
        "num_tasks": record["num_tasks"],
        "num_categories": 14,
        "category_vector": snapshot["category_vector"],
    }
    verifier = verify_assignment(case, matrix, record["r"])
    result = {"assignment_run_id": run_id, "status": "VERIFIED" if verifier["valid"] else "VERIFICATION_FAILED",
              "verifier": verifier}
    dist_dir = repo / "distributions" / run_id / "reviewers"
    if dist_dir.is_dir():
        from codesetarena.assignment import distribution as dist_mod
        from codesetarena.submission.registry import SubmissionRegistry

        reg = SubmissionRegistry(repo)
        check = dist_mod.verify_distribution(repo, run_dir, record, reg)
        leaks = dist_mod.scan_distribution_leaks(
            repo / "distributions" / run_id,
            dist_mod.distribution_secret_tokens(repo, reg),
        )
        result["distribution_verification"] = check
        result["leaks"] = leaks
        # v10.1.11 Exposure Contract Fix #3：verify 重放必须包含 Exposure 语义扫描
        exposure_leaks = dist_mod.scan_exposure_contract_leaks(
            repo / "distributions" / run_id)
        result["exposure_contract_leaks"] = exposure_leaks
        if check["valid"] and not leaks and not exposure_leaks:
            result["status"] = "READY"
        else:
            result["status"] = "DISTRIBUTION_FAILED"
            # 重放否定 READY → 持久化失败状态（export 门读取 run.json）
            persisted = run_mod.load_run(repo, run_id)
            persisted["status"] = RunStatus.DISTRIBUTION_FAILED
            persisted["distribution_status"] = "DISTRIBUTION_FAILED"
            persisted["errors"] = [
                f"DIST_EXPOSURE_CONTRACT_LEAK: {h['path']}: {h['keys']}"
                for h in exposure_leaks
            ] or [f"DIST_IDENTITY_LEAK: {h['path']}: {h['token']}" for h in leaks][:10]
            run_mod._write_json(repo / "assignments" / run_id / "run.json", persisted)
    return result


def _print_summary(record: dict) -> None:
    status = record.get("status", "UNKNOWN")
    print(f"[assignment] run_id={record.get('assignment_run_id')} status={status}")
    if record.get("reused"):
        print("[assignment] RUN_EXISTS: 已存在相同 (snapshot, method, seed, policy) 的 run，复用")
    for key in ("method", "seed", "r", "num_reviewers", "num_tasks",
                "total_assignments", "verifier_status", "diversity_score",
                "exposure_mae", "distribution_status", "identity_leaks",
                "reviewer_packages", "report_path"):
        value = record.get(key)
        if value is not None:
            print(f"[assignment] {key}={value}")
    errors = record.get("errors") or []
    for error in errors[:10]:
        print(f"[assignment] ERROR {error}")
    if status == RunStatus.READY:
        print("[assignment] READY: distribution verified, identity leaks = 0")