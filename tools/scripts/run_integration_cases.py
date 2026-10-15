#!/usr/bin/env python3
"""Integration Test 自动化入口（含进度条 + 断点续跑）。

用法::

    .venv/bin/python scripts/run_integration_cases.py --suite full
    .venv/bin/python scripts/run_integration_cases.py --suite smoke
    .venv/bin/python scripts/run_integration_cases.py --case COMMON_INVALID_FM
    .venv/bin/python scripts/run_integration_cases.py --category teacher_pool_state
    .venv/bin/python scripts/run_integration_cases.py --suite full --resume-from COMMON_INVALID_FM
    .venv/bin/python scripts/run_integration_cases.py --list

环境变量：

- CODESETARENA_FIXTURE_ROOT：永久 Fixture Root（默认 ActualExp/DAC_TestCases）
- CODESETARENA_IT_TMP：临时执行根（默认 /tmp/codesetarena-integration）

进度：TTY 下默认显示进度条（--progress/--no-progress 可强制）；非 TTY（管道/tee）
自动改为逐行进度输出。

断点续跑：``--resume-from <case_id>`` 从指定 case 开始（跳过之前所有 case）；
中断（Ctrl-C）时会打印续跑提示，退出码 130。

退出码：全 PASS -> 0；任一 FAIL -> 1；中断 -> 130；基础设施错误 -> 2。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SRC = ROOT / "src"
for path in (_SRC, str(ROOT)):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from tests.integration.fixture_runner import (  # noqa: E402
    CaseError,
    default_fixture_root,
    default_tmp_root,
    load_case,
    run_integration_case,
)

CATEGORY_LABELS = {
    "common": "Common Base Contract",
    "server": "Server Trust Boundary",
    "phase2_extension": "Phase2 Extension",
    "workflow": "Workflow",
    "local": "Local Build",
    "workspace": "Workspace",
    "cohort": "Cohort / Sampling",
    "student_package": "Student Package (gaps)",
    "run_workspace": "Run Workspace",
    "teacher_pool_state": "Teacher Pool State",
    "server_pool_state": "Server Pool State",
}


def _load_index(fixture_root: Path) -> list[dict]:
    index_path = fixture_root / "case_index.json"
    if not index_path.is_file():
        print(f"缺少 case_index.json: {index_path}（请先运行作者工具生成 corpus）",
              file=sys.stderr)
        raise SystemExit(2)
    return json.loads(index_path.read_text(encoding="utf-8")).get("cases") or []


def _show_filters(args) -> str:
    """把当前过滤参数还原为命令行片段（用于中断后的续跑提示）。"""

    parts = []
    if args.suite:
        parts.append(f"--suite {args.suite}")
    if args.category:
        parts.append(f"--category {args.category}")
    if args.phase and args.phase != "all":
        parts.append(f"--phase {args.phase}")
    if args.profile and args.profile != "all":
        parts.append(f"--profile {args.profile}")
    if args.case:
        parts.append(f"--case {args.case}")
    return " ".join(parts)


def _render_progress(done: int, total: int, current: str, *, elapsed: float) -> None:
    """TTY 进度条（原地刷新，不换行）。"""

    bar_width = 30
    filled = int(bar_width * done / total) if total else 0
    bar = "#" * filled + "-" * (bar_width - filled)
    percent = 100.0 * done / total if total else 0.0
    line = (f"[{done:>3d}/{total}] [{bar}] {percent:5.1f}%  "
            f"elapsed {int(elapsed)}s  current={current}")
    print(line[:140] + " " * max(0, 140 - len(line)), end="", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_integration_cases")
    parser.add_argument("--case", default=None, help="只运行单个 case_id")
    parser.add_argument("--profile", default=None,
                        choices=["local", "server", "batch", "workspace", "all"],
                        help="按 execution profile 过滤")
    parser.add_argument("--phase", default=None, choices=["phase1", "phase2", "all"])
    parser.add_argument("--category", default=None, help="按 category 过滤")
    parser.add_argument("--list", action="store_true", help="只列出 case 不执行")
    parser.add_argument("--suite", default=None, choices=["smoke", "full"],
                        help="smoke 快捷子集 / full 全量")
    parser.add_argument("--fixture-root", default=None,
                        help="覆盖 CODESETARENA_FIXTURE_ROOT")
    parser.add_argument("--tmp-root", default=None, help="覆盖 CODESETARENA_IT_TMP")
    parser.add_argument("--keep-workspace-on-failure", action="store_true",
                        help="Case FAIL 时保留临时工作区并打印路径")
    parser.add_argument("--resume-from", default=None,
                        help="断点续跑：从该 case_id 开始（跳过其之前的所有 case）")
    parser.add_argument("--progress", dest="progress", action="store_true", default=None,
                        help="强制显示进度条")
    parser.add_argument("--no-progress", dest="progress", action="store_false",
                        help="关闭进度显示")
    args = parser.parse_args(argv)

    fixture_root = Path(args.fixture_root) if args.fixture_root else default_fixture_root()
    tmp_root = Path(args.tmp_root) if args.tmp_root else default_tmp_root()

    cases = _load_index(fixture_root)
    if args.case:
        cases = [c for c in cases if c["case_id"] == args.case]
        if not cases:
            print(f"未找到 case: {args.case}", file=sys.stderr)
            return 2
    if args.category:
        cases = [c for c in cases if c.get("category") == args.category]
    if args.suite:
        cases = [c for c in cases if args.suite in c.get("suite", [])]
    if args.profile and args.profile != "all":
        filtered: list[dict] = []
        for c in cases:
            case = load_case(fixture_root / c["dir"])
            matching = [e for e in case["executions"]
                        if e.get("profile") == args.profile]
            if matching:
                case = dict(case)
                case["executions"] = matching
                filtered.append(case)
        cases = filtered
    if args.phase and args.phase != "all":
        filtered = []
        for c in cases:
            case = load_case(fixture_root / c["dir"])
            matching = [e for e in case["executions"]
                        if e.get("phase") in (args.phase, "all")]
            if matching:
                case = dict(case)
                case["executions"] = matching
                filtered.append(case)
        cases = filtered

    if not cases:
        print("没有匹配的 case")
        return 2

    if args.list:
        for c in cases:
            print(f"{c['case_id']:<40} {c.get('category', ''):<18} "
                  f"{c.get('asset_type', '-')}")
        print(f"total {len(cases)}")
        return 0

    start_index = 0
    if args.resume_from:
        ids = [c["case_id"] for c in cases]
        if args.resume_from not in ids:
            print(f"resume-from 指定的 case 不在本次选择中: {args.resume_from}",
                  file=sys.stderr)
            return 2
        start_index = ids.index(args.resume_from)
        print(f"resume-from : {args.resume_from}（跳过前 {start_index} 个 case）")

    print("=" * 50)
    print("DAC Integration Test")
    print("=" * 50)
    print(f"fixture root : {fixture_root}")
    print(f"tmp root     : {tmp_root}")
    print(f"cases        : {len(cases)}")

    results: list[dict] = []
    infra_failures = 0
    total_cases = len(cases)
    pending = total_cases - start_index
    use_tty = sys.stdout.isatty() if args.progress is None else args.progress
    t_start = time.monotonic()
    exec_done = 0
    exec_ok = 0
    try:
        for idx in range(start_index, total_cases):
            entry = cases[idx]
            if use_tty:
                _render_progress(idx - start_index, pending, entry["case_id"],
                                 elapsed=time.monotonic() - t_start)
            t_case = time.monotonic()
            case_dir = fixture_root / entry["dir"]
            try:
                case = load_case(case_dir)
                result = run_integration_case(
                    case, case_dir, tmp_root=tmp_root,
                    keep_on_failure=args.keep_workspace_on_failure,
                )
            except CaseError as exc:
                result = {"case_id": entry["case_id"], "category": entry["category"],
                          "status": "FAIL", "failures": [f"infra: {exc}"],
                          "executions": []}
                infra_failures += 1
            dt = time.monotonic() - t_case
            results.append(result)
            exec_done += len(result.get("executions", []))
            exec_ok += sum(1 for e in result.get("executions", []) if e["status"] == "PASS")
            line = (f"[{idx + 1:>3d}/{total_cases}] {entry['case_id']:<38} "
                    f"{result['status']:<4} (exec {exec_ok}/{exec_done} cumulative, "
                    f"{dt:6.1f}s case)")
            if use_tty:
                print(" " * 140, end="", flush=True)
                print("\r" + line, flush=True)
            else:
                print(line, flush=True)
            _print_case(result)
        if start_index > 0:
            print(f"\n[resume] 本次从 {cases[start_index]['case_id']} 续跑，"
                  f"共完成 {pending} 个 case（前 {start_index} 个已跳过）")
    except KeyboardInterrupt:
        print("\n[interrupt] 收到 Ctrl-C，停止批量执行。", file=sys.stderr)
        if results:
            nxt = cases[min(start_index + len(results), total_cases - 1)]["case_id"]
            print("[interrupt] 断点续跑命令：", file=sys.stderr)
            print(f"  .venv/bin/python scripts/run_integration_cases.py "
                  f"{_show_filters(args)} --resume-from {nxt}", file=sys.stderr)
        else:
            print(f"[interrupt] 尚未完成任何 case；续跑："
                  f"  .venv/bin/python scripts/run_integration_cases.py "
                  f"{_show_filters(args)} --resume-from {cases[start_index]['case_id']}",
                  file=sys.stderr)
        return 130

    failed = [r for r in results if r["status"] != "PASS"]
    total_exec = sum(len(r.get("executions", [])) for r in results)
    passed_exec = sum(
        1 for r in results for e in r.get("executions", []) if e["status"] == "PASS"
    )

    print("-" * 50)
    by_category: dict[str, tuple[int, int]] = {}
    for r in results:
        label = CATEGORY_LABELS.get(r["category"], r["category"])
        total, passed = by_category.get(label, (0, 0))
        by_category[label] = (
            total + len(r.get("executions", [])),
            passed + sum(1 for e in r.get("executions", []) if e["status"] == "PASS"),
        )
    for label, (total, passed) in sorted(by_category.items()):
        mark = "PASS" if passed == total else "FAIL"
        print(f"{label:<24} {passed:>3} / {total:>3} {mark}")
    print("-" * 50)
    print(f"{'Total':<24} {passed_exec:>3} / {total_exec:>3} "
          f"{'PASS' if not failed and infra_failures == 0 else 'FAIL'}")

    if failed or infra_failures:
        print()
        print("FAILED CASES:")
        for r in failed:
            print(f"  {r['case_id']}")
            for failure in r.get("failures") or []:
                print(f"    - {failure}")
            for e in r.get("executions", []):
                if e["status"] != "PASS":
                    print(f"    * {e['id']} ({e['profile']})")
                    for failure in e.get("failures") or []:
                        print(f"      - {failure}")
        return 1
    return 0


def _print_case(result: dict) -> None:
    print()
    print("Case:")
    print(result["case_id"])
    print()
    for e in result.get("executions", []):
        print(f"{e['profile'].upper():<10} {e['id']:<28} {e['status']}")
    fixture_ok = all(f["ok"] for f in result.get("fixture_before", [])
                     ) and all(f["ok"] for f in result.get("fixture_after", []))
    print(f"{'fixture integrity':<40} {'PASS' if fixture_ok else 'FAIL'}")
    cleanup = result.get("workspace_cleanup", True)
    print(f"{'temp workspace cleanup':<40} {'PASS' if cleanup else 'RETAINED'}")


if __name__ == "__main__":
    raise SystemExit(main())