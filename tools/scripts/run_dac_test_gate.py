#!/usr/bin/env python3
"""DAC Test Gate —— CodeSetArena 统一回归门禁入口。

开发人员只需要记这一个命令：

    .venv/bin/python scripts/run_dac_test_gate.py --verify
    .venv/bin/python scripts/run_dac_test_gate.py --smoke
    .venv/bin/python scripts/run_dac_test_gate.py --full
    .venv/bin/python scripts/run_dac_test_gate.py --pre-change      # 改代码之前
    .venv/bin/python scripts/run_dac_test_gate.py --post-change     # 改代码之后（必须全绿）
    .venv/bin/python scripts/run_dac_test_gate.py --release         # 发布
    .venv/bin/python scripts/run_dac_test_gate.py --lock-current    # 显式维护 Baseline Lock

原则：

- 任何系统模块修改都必须通过 Post-change Gate 才能算 DONE；
- Baseline Lock：仓库内 lock == 外部 Corpus identity，不一致 FAIL，禁止自动更新；
- Fixture READ ONLY：Gate 绝不重建 / 修改 / 编译 fixture；
- 报告写入 test_reports/dac/<时间戳>_<MODE>.{json,md}。

退出码：0 = 全绿通过；1 = Gate FAIL；2 = 用法/基础设施错误。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (str(ROOT), str(ROOT / "src")):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from tests.integration.dac_baseline import (  # noqa: E402
    LOCK_FILENAME,
    build_lock_content,
    compute_corpus_identity,
    load_lock,
    verify_lock,
)
from tests.integration.fixture_runner import default_fixture_root  # noqa: E402

REPORT_DIR = ROOT / "test_reports" / "dac"
GATE_STAGES: list[str] = []


def _git_state() -> dict:
    """仓库非 git repo 时如实记录 no-git，不伪造 commit。"""

    try:
        out = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=20)
        if out.returncode == 0:
            return {"system_git_commit": out.stdout.strip(),
                    "git_dirty": "unknown(no-git-worktree-check)"}
    except Exception:  # noqa: BLE001
        pass
    return {"system_git_commit": "no-git", "git_dirty": "n/a"}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _run(cmd: list[str], verbose: bool = True) -> subprocess.CompletedProcess:
    """流式执行：实时透传 stdout/stderr（进度可见），同时保留全文供报告解析。"""

    proc = subprocess.Popen(
        cmd, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    chunks: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        chunks.append(line)
        if verbose:
            print(line, end="", flush=True)
    ret = proc.wait()
    return subprocess.CompletedProcess(cmd, ret, stdout="".join(chunks), stderr="")


def _parse_failed_cases(stdout: str) -> list[str]:
    """从 run_integration_cases 输出解析 FAILED CASES 列表。"""

    block = stdout.split("FAILED CASES:")[-1] if "FAILED CASES:" in stdout else ""
    cases: list[str] = []
    for line in block.splitlines():
        m = re.match(r"^\s{2}(\S+)\s*$", line)
        if m:
            cases.append(m.group(1))
    return cases


def _stage(name: str, ok: bool, detail: str = "") -> dict:
    GATE_STAGES.append(name)
    return {"stage": name, "pass": ok, "detail": detail[:400]}


def _write_report(mode: str, payload: dict, *, exit_code: int) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    payload.update({
        "report_id": f"{ts}_{mode}",
        "mode": mode,
        "finished_at": _now(),
        "elapsed_seconds": round(payload.get("_elapsed", 0.0), 1),
    })
    payload.pop("_elapsed", None)
    jpath = REPORT_DIR / f"{ts}_{mode}.json"
    jpath.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md = [f"# DAC Test Gate Report [{mode}]", "",
          f"- report_id: `{payload['report_id']}`", 
          f"- baseline: v{payload.get('baseline_version')} ({payload.get('corpus_version')})",
          f"- cases/executions/smoke: {payload.get('case_count')} / "
          f"{payload.get('execution_count')} / {payload.get('smoke_execution_count')}",
          f"- result: {payload.get('result')}",
          f"- gate_exit_code: {exit_code}"]
    mpath = REPORT_DIR / f"{ts}_{mode}.md"
    mpath.write_text("\n".join(md) + "\n", encoding="utf-8")
    return jpath


def gate_verify(fixture_root: Path, lock_path: Path) -> tuple[bool, int, dict]:
    ok, mismatches = verify_lock(lock_path, fixture_root)
    actual = compute_corpus_identity(fixture_root)
    stages = [
        _stage("baseline_lock_match", ok,
               ";\n".join(mismatches) if mismatches else "Expected==Actual"),
        _stage("dynamic_cases_zero", actual["dynamic_cases"] == 0,
               f"REMAINING_DYNAMIC_CASES={actual['dynamic_cases']}"),
        _stage("missing_fixtures_zero", actual["missing_fixtures"] == 0,
               f"MISSING_FIXTURES={actual['missing_fixtures']}"),
        _stage("hash_mismatches_zero", actual["hash_mismatches"] == 0,
               f"HASH_MISMATCHES={actual['hash_mismatches']}"),
    ]
    passed = all(s["pass"] for s in stages)
    payload = {"result": "PASS" if passed else "FAIL", "stages": stages, **_git_state(),
               "baseline_version": load_lock(lock_path).get("baseline_version"),
               "corpus_version": actual["corpus_version"],
               "case_count": actual["case_count"],
               "execution_count": actual["execution_count"],
               "smoke_execution_count": actual["smoke_execution_count"],
               "corpus_identity_sha256": actual["corpus_identity_sha256"],
               "fixture_schema_version": actual["fixture_schema_version"]}
    return passed, (0 if passed else 1), payload


def _require_verify(fixture_root: Path, lock_path: Path) -> tuple[bool, int, dict]:
    ok, code, payload = gate_verify(fixture_root, lock_path)
    if not ok:
        print("BASELINE LOCK VERIFY FAILED（禁止继续运行 regression）", file=sys.stderr)
        for s in payload["stages"]:
            if not s["pass"]:
                print(f"  FAIL {s['stage']}: {s['detail']}", file=sys.stderr)
        print("如需维护（显式）： scripts/run_dac_test_gate.py --lock-current", file=sys.stderr)
    return ok, code, payload


def _run_dac_suite(suite: str, fixture_root: Path,
                   include_verify: bool = True,
                   resume_from: str | None = None) -> tuple[bool, int, dict, str]:
    stages: list[dict] = []
    ok = True
    if include_verify:
        v_ok, _, vpayload = _require_verify(fixture_root, ROOT / "tests/integration" / LOCK_FILENAME)
        stages.extend(vpayload["stages"])
        ok = v_ok
    # 运行前后各做一次 fixture identity（运行期不得污染 fixture）
    before = compute_corpus_identity(fixture_root)
    runner_cmd = [sys.executable, str(ROOT / "scripts" / "run_integration_cases.py"),
                  "--suite", suite, "--fixture-root", str(fixture_root)]
    if resume_from:
        runner_cmd += ["--resume-from", resume_from]
    proc = _run(runner_cmd)
    after = compute_corpus_identity(fixture_root)
    stage_ok = proc.returncode == 0
    stage = _stage(f"dac_{suite}", stage_ok,
                   f"exit={proc.returncode}")
    stages.append(stage)
    fixed_ok = (before["corpus_identity_sha256"] == after["corpus_identity_sha256"]
                and before["missing_fixtures"] == after["missing_fixtures"]
                and before["hash_mismatches"] == after["hash_mismatches"])
    stages.append(_stage("fixture_identity_unchanged", fixed_ok,
                         f"before={before['corpus_identity_sha256'][:16]}… "
                         f"after={after['corpus_identity_sha256'][:16]}…"))
    ok = ok and stage_ok and fixed_ok
    summary = proc.stdout
    m = re.search(r"Total\s+(\d+) / (\d+)", summary)
    payload = {
        "result": "PASS" if ok else "FAIL",
        "stages": stages,
        f"{suite}_exit_code": proc.returncode,
        f"{suite}_passed": int(m.group(2)) if m else None,
        f"{suite}_total": int(m.group(1)) if m else None,
        "failed_cases": _parse_failed_cases(summary),
        **_git_state(),
        "baseline_version": load_lock(ROOT / "tests/integration" / LOCK_FILENAME).get("baseline_version"),
        "case_count": before["case_count"],
        "execution_count": before["execution_count"],
        "smoke_execution_count": before["smoke_execution_count"],
        "corpus_identity_sha256": before["corpus_identity_sha256"],
        "fixture_schema_version": before["fixture_schema_version"],
    }
    return ok, (0 if ok else 1), payload, summary


def _run_unit() -> tuple[bool, int, dict]:
    proc = _run([sys.executable, "-m", "pytest", "tests/", "-q",
                 "--ignore=tests/test_benchmark_matrix.py"],
                verbose=False)
    ok = proc.returncode == 0
    return ok, (0 if ok else 1), {
        "result": "PASS" if ok else "FAIL",
        "unit_exit_code": proc.returncode,
        "unit_summary": (proc.stdout or "").splitlines()[-2:] + (proc.stderr or "").splitlines()[:2],
        "stages": [_stage("unit_pytest", ok, f"exit={proc.returncode}")],
    }


def _latest_report(mode: str) -> dict | None:
    files = sorted(REPORT_DIR.glob(f"*_{mode}.json")) if REPORT_DIR.is_dir() else []
    if not files:
        return None
    return json.loads(files[-1].read_text(encoding="utf-8"))


def _compare_pre_post(pre: dict | None, post: dict) -> list[str]:
    if not pre:
        return ["(无 pre-change 报告可比较; 建议先运行 --pre-change)"]
    issues: list[str] = []
    pre_total = pre.get("full_total") or pre.get("execution_count") or 0
    post_total = post.get("full_total") or post.get("execution_count") or 0
    post_passed = post.get("full_passed")
    if post_passed is not None and post_passed < pre_total:
        issues.append(f"REGRESSION INTRODUCED: pre={pre.get('full_passed')}/{pre.get('full_total')} "
                      f"post={post_passed}/{post_total}")
        issues += [f"  NEW FAILURE: {c}" for c in (post.get("failed_cases") or [])]
    else:
        issues.append(f"NO NEW REGRESSION: pre full={pre.get('full_passed')}/{pre.get('full_total')} "
                      f"(或对照 pre 全绿) post={post_passed}/{post_total}")
    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_dac_test_gate")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--pre-change", action="store_true")
    parser.add_argument("--post-change", action="store_true")
    parser.add_argument("--release", action="store_true")
    parser.add_argument("--lock-current", action="store_true",
                        help="显式维护 Baseline Lock（写当前 identity 入 lock，人工确认用）")
    parser.add_argument("--baseline-version", default="1.0.0",
                        help="--lock-current 时写入的 baseline 版本")
    parser.add_argument("--unit", action="store_true", help="post-change/release 追加单元测试")
    parser.add_argument("--resume-from", default=None,
                        help="断点续跑：full/smoke 从该 case_id 开始（透传给 run_integration_cases）")
    parser.add_argument("--fixture-root", default=None)
    args = parser.parse_args(argv)

    fixture_root = Path(args.fixture_root).resolve() if args.fixture_root else default_fixture_root()
    lock_path = ROOT / "tests/integration" / LOCK_FILENAME

    if args.lock_current:
        base = load_lock(lock_path)
        actual = compute_corpus_identity(fixture_root)
        lock = build_lock_content(actual, baseline_version=args.baseline_version)
        lock["corpus_version"] = base.get("corpus_version") or actual["corpus_version"]
        lock_path.write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        print(f"baseline lock updated -> {lock_path}")
        return 0

    modes = [m for m, flag in (
        ("verify", args.verify), ("smoke", args.smoke), ("full", args.full),
        ("pre_change", args.pre_change), ("post_change", args.post_change),
        ("release", args.release)) if flag]
    if not modes:
        parser.print_help()
        return 2
    if len(modes) > 1:
        print("一次只允许一个 mode", file=sys.stderr)
        return 2
    mode = modes[0]
    start = time.monotonic()

    payload: dict = {"stages": []}
    exit_code = 1
    if mode == "verify":
        ok, exit_code, payload = gate_verify(fixture_root, lock_path)
        print(f"result={payload['result']} "
              f"(cases={payload['case_count']} exec={payload['execution_count']} "
              f"smoke={payload['smoke_execution_count']} "
              f"dynamic={payload['stages'][1]['detail']})")
    elif mode == "smoke":
        ok, exit_code, payload, _ = _run_dac_suite("smoke", fixture_root, resume_from=args.resume_from)
        print(f"result={payload['result']} smoke={payload.get('smoke_passed')}/{payload.get('smoke_total')}")
    elif mode == "full":
        ok, exit_code, payload, _ = _run_dac_suite("full", fixture_root, resume_from=args.resume_from)
        print(f"result={payload['result']} full={payload.get('full_passed')}/{payload.get('full_total')}")
    elif mode == "pre_change":
        v_ok, v_code, vpayload = _require_verify(fixture_root, lock_path)
        payload.update(vpayload)
        ok, code, fpayload, _ = _run_dac_suite("full", fixture_root, resume_from=args.resume_from)
        payload.update(fpayload)
        ok = v_ok and ok
        payload["result"] = "PASS" if ok else "FAIL"
        payload["report_kind"] = "PRE_CHANGE"
        exit_code = 0 if ok else 1
        print(f"PRE-CHANGE result={payload['result']} full={payload.get('full_passed')}/{payload.get('full_total')}")
    elif mode == "post_change":
        ok = True
        v_ok, _, vpayload = _require_verify(fixture_root, lock_path)
        payload.update(vpayload)
        ok = v_ok
        if args.unit:
            u_ok, _, upayload = _run_unit()
            payload.update(upayload)
            ok = ok and u_ok
        s_ok, _, spayload, _ = _run_dac_suite("smoke", fixture_root, resume_from=args.resume_from)
        payload.update(spayload)
        ok = ok and s_ok
        f_ok, _, fpayload, _ = _run_dac_suite("full", fixture_root, resume_from=args.resume_from)
        payload.update(fpayload)
        ok = ok and f_ok
        # 运行后再复核 identity（含 fixture 不可变）
        final = compute_corpus_identity(fixture_root)
        verify2 = (final["missing_fixtures"] == 0 and final["hash_mismatches"] == 0
                   and final["corpus_identity_sha256"] == payload.get("corpus_identity_sha256"))
        payload["stages"].append(_stage("post_run_fixture_verify", verify2,
                                        f"identity={final['corpus_identity_sha256'][:16]}…"))
        ok = ok and verify2
        issues = _compare_pre_post(_latest_report("pre_change"), payload)
        payload["pre_post_comparison"] = issues
        payload["report_kind"] = "POST_CHANGE"
        payload["result"] = "PASS" if ok else "FAIL"
        exit_code = 0 if ok else 1
        print(f"POST-CHANGE result={payload['result']} full={payload.get('full_passed')}/{payload.get('full_total')}")
        for line in issues:
            print("  " + line)
    elif mode == "release":
        ok = True
        v_ok, _, vpayload = _require_verify(fixture_root, lock_path)
        payload.update(vpayload)
        ok = v_ok
        u_ok, _, upayload = _run_unit()
        payload.update(upayload)
        ok = ok and u_ok
        s_ok, _, spayload, _ = _run_dac_suite("smoke", fixture_root, resume_from=args.resume_from)
        payload.update(spayload)
        ok = ok and s_ok
        f_ok, _, fpayload, _ = _run_dac_suite("full", fixture_root, resume_from=args.resume_from)
        payload.update(fpayload)
        ok = ok and f_ok
        payload["report_kind"] = "RELEASE"
        payload["result"] = "PASS" if ok else "FAIL"
        exit_code = 0 if ok else 1
        print(f"RELEASE result={payload['result']} unit={payload.get('unit_exit_code')} "
              f"full={payload.get('full_passed')}/{payload.get('full_total')}")

    payload["_elapsed"] = time.monotonic() - start
    report_path = _write_report(mode, payload, exit_code=exit_code)
    print(f"report -> {report_path}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())