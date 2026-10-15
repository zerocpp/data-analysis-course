"""Demo Runtime —— 教师演示专用：Runtime Resource Limit 四案例（独立隔离）。

原则：

- **复用真实 Runtime 内核**：每个案例都经 ``main.budget.run_with_budget``
  （与正式 run/exposure 同一条 Supervisor + 子进程执行链）真实执行，
  禁止任何“sleep 后打印假结果”的伪造；
- **独立 override**：每案例的 timeout 通过本模块构造的**案例级 Budget**
  实现（仅 demo 命令使用的 runtime override），绝不修改正式
  ``policy/runtime_policy.json``，也不影响 ``run --phase``；
- **隔离落位**：全部文件写入 ``<workspace>/demos/runtime_limits/<case>/``，
  运行证据写 ``<workspace>/demos/runtime_limits/runs/<case>/``，
  绝不触碰 ``phase1/``、``phase2/``；
- **无 LLM**：示例 agent 不调用 llm，不消耗 Token、不依赖网络。

案例矩阵（同一题：value 列求和 = 60；同一坏 agent；同一好 agent）::

    too-loose          坏 agent（死循环）   timeout=10s  → TIMEOUT
    too-strict         好 agent（sleep 3s） timeout=2s   → TIMEOUT（误杀）
    normal             好 agent（sleep 3s） timeout=10s  → PASS answer=60
    timeout-isolation  坏 agent（死循环）   timeout=3s   → TIMEOUT + 健康检查 PASS
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TASK = {
    "description": "计算 value 列的总和。",
    "task_statement": "data.csv 包含 value 列。请计算 value 列所有记录的总和，并只输出整数。",
    "answer": 60,
}
DATA_CSV = "value\n10\n20\n30\n"
REFERENCE_PY = (
    '"""Demo reference：唯一输入 df，唯一输出答案。"""\n\n\n'
    "def reference(df):\n    return int(df['value'].sum())\n"
)
GOOD_AGENT_PY = (
    "import time\n\n\n"
    "def run(task, df, llm):\n"
    "    time.sleep(3)\n"
    "    return int(df['value'].sum())\n"
)
GOOD_FAST_AGENT_PY = (
    "def run(task, df, llm):\n"
    "    return int(df['value'].sum())\n"
)
BAD_AGENT_PY = (
    "def run(task, df, llm):\n"
    "    while True:\n"
    "        pass\n"
)

DEMO_SUBDIR = "demos"
DEMO_NAME = "runtime_limits"
RUNS_SUBDIR = "runs"
EXPECTED_ANSWER = 60

CASES: dict[str, dict[str, Any]] = {
    "too-loose": {
        "seq": 1,
        "dir": "01_too_loose_bad_agent",
        "title": "TOO LOOSE",
        "agent": BAD_AGENT_PY,
        "agent_desc": "infinite loop（坏 Agent，长期占用）",
        "timeout": 10,
        "expected": "TIMEOUT",
    },
    "too-strict": {
        "seq": 2,
        "dir": "02_too_strict_good_agent",
        "title": "TOO STRICT",
        "agent": GOOD_AGENT_PY,
        "agent_desc": "correct, expected runtime ≈ 3s（好 Agent，被误杀）",
        "timeout": 2,
        "expected": "TIMEOUT",
    },
    "normal": {
        "seq": 3,
        "dir": "03_reasonable_good_agent",
        "title": "REASONABLE LIMIT",
        "agent": GOOD_AGENT_PY,
        "agent_desc": "与 Demo 02 完全相同的好 Agent",
        "timeout": 10,
        "expected": "PASS",
    },
    "timeout-isolation": {
        "seq": 4,
        "dir": "04_reasonable_bad_agent",
        "title": "TIMEOUT ISOLATION",
        "agent": BAD_AGENT_PY,
        "agent_desc": "与 Demo 01 完全相同的坏 Agent",
        "timeout": 3,
        "expected": "TIMEOUT",
    },
}
CASE_ORDER = ("too-loose", "too-strict", "normal", "timeout-isolation")


class DemoRuntimeError(RuntimeError):
    """Demo 配置/执行错误。"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha_tree(root: Path) -> dict[str, str]:
    """目录内全部文件的 sha256（相对路径排序）；目录不存在返回空。"""

    files: dict[str, str] = {}
    if not root.is_dir():
        return files
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        files[path.relative_to(root).as_posix()] = hashlib.sha256(
            path.read_bytes()).hexdigest()
    return files


def _policy_paths() -> list[Path]:
    """正式 policy 的候选位置（只读完整性检查用，demo 绝不写它们）。"""

    import os

    candidates = []
    csa_root = os.environ.get("CSA_ROOT")
    if csa_root:
        candidates.append(Path(csa_root) / "policy" / "runtime_policy.json")
    candidates.append(Path("/opt/codesetarena/policy/runtime_policy.json"))
    try:
        from ..policy_loader import project_root

        candidates.append(Path(project_root()) / "policy" / "runtime_policy.json")
    except Exception:  # noqa: BLE001 - 宿主机无 policy 时跳过
        pass
    seen: list[Path] = []
    for candidate in candidates:
        if candidate.is_file() and candidate not in seen:
            seen.append(candidate)
    return seen


def _integrity_snapshot(workspace: Path) -> dict[str, Any]:
    """phase1/phase2/正式 policy 的只读完整性快照（§26/§27）。"""

    workspace = Path(workspace)
    return {
        "phase1": _sha_tree(workspace / "phase1"),
        "phase2": _sha_tree(workspace / "phase2"),
        "policy": {str(p): hashlib_sha(p) for p in _policy_paths()},
    }


def hashlib_sha(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def demo_root(workspace: str | Path) -> Path:
    return Path(workspace) / DEMO_SUBDIR / DEMO_NAME


def demo_runs_root(workspace: str | Path) -> Path:
    return demo_root(workspace) / RUNS_SUBDIR


def run_case(case: str, workspace: str | Path, *, quiet: bool = False) -> dict[str, Any]:
    """执行单个 Demo 案例（真实 Runtime 内核；评估用真实 reference）。"""

    import pandas as pd

    from ..main.budget import Budget, run_with_budget
    from ..main.outcome import classify_budget_outcome
    from ..main.runner import _answers_equal, _run_reference

    if case not in CASES:
        raise DemoRuntimeError(f"未知 demo case: {case!r}（可选 "
                               + "/".join(CASE_ORDER) + "）")
    spec = CASES[case]
    workspace = Path(workspace)
    case_dir = demo_root(workspace) / spec["dir"]
    case_dir.mkdir(parents=True, exist_ok=True)
    runs_dir = demo_runs_root(workspace) / f"demo_{spec['seq']:02d}"
    runs_dir.mkdir(parents=True, exist_ok=True)

    # 案例内容：同一道题 / 同一 reference；只换 agent 与 timeout
    (case_dir / "task.json").write_text(
        json.dumps(TASK, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (case_dir / "data.csv").write_text(DATA_CSV, encoding="utf-8")
    (case_dir / "reference.py").write_text(REFERENCE_PY, encoding="utf-8")
    agent_path = case_dir / "agent.py"
    agent_path.write_text(spec["agent"], encoding="utf-8")

    df = pd.read_csv(case_dir / "data.csv")
    started = time.monotonic()
    started_iso = _now_iso()
    scratch = Path(tempfile.mkdtemp(prefix=f"csa_demo_{case}_"))
    budget = Budget(
        timeout_seconds=float(spec["timeout"]),
        max_memory_mb=None,
        max_disk_mb=None,
        max_tokens=None,
    )
    llm_spec = {
        "base_url": "http://localhost:9",
        "api_key": "DEMO-NO-LLM",
        "model": "demo-no-llm",
        "timeout": 30.0,
    }
    input_data = {
        "task": dict(TASK),
        "data_csv": str(case_dir / "data.csv"),
        "scratch_dir": str(scratch),
    }
    outcome = run_with_budget(str(agent_path), input_data, budget, llm_spec)
    elapsed = round(time.monotonic() - started, 2)
    shutil.rmtree(scratch, ignore_errors=True)

    agent_answer = outcome.get("result")
    timed_out = (not outcome.get("ok")) and outcome.get("stop_reason") in (
        "time_budget_exceeded",)
    correct = None
    reference_answer = None
    if outcome.get("ok"):
        try:
            reference_answer = _run_reference(case_dir / "reference.py", df.copy(deep=True))
            correct = bool(_answers_equal(agent_answer, reference_answer))
        except Exception as exc:  # noqa: BLE001 - reference 失败也要如实呈现
            reference_answer = f"reference_error: {exc}"

    classification = classify_budget_outcome(outcome)
    expected = spec["expected"]
    if expected == "TIMEOUT":
        status = "PASS" if timed_out else "FAIL"
    else:
        status = "PASS" if (outcome.get("ok") and correct) else "FAIL"

    record = {
        "schema_version": "codesetarena.demo.runtime.v1",
        "case": case,
        "title": spec["title"],
        "agent_desc": spec["agent_desc"],
        "timeout_seconds": spec["timeout"],
        "expected": expected,
        "status": status,
        "timed_out": timed_out,
        "stop_reason": outcome.get("stop_reason"),
        "classification": classification,
        "answer": agent_answer if outcome.get("ok") else None,
        "answer_preview": (agent_answer if isinstance(agent_answer, str)
                           else json.dumps(agent_answer, ensure_ascii=False)
                           )[:500] if outcome.get("ok") else None,
        "reference_answer": reference_answer,
        "correct": correct,
        "started_at": started_iso,
        "finished_at": _now_iso(),
        "elapsed_seconds": elapsed,
        "agent_path": str(agent_path),
    }
    (runs_dir / "result.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if outcome.get("calls_log"):
        (runs_dir / "llm_calls.json").write_text(
            json.dumps(outcome["calls_log"], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
    if not quiet:
        print(f"[demo] {case:<18} status={status} expected={expected} "
              f"elapsed={elapsed}s stop={outcome.get('stop_reason')} "
              f"answer={record['answer']!r} correct={correct}")
    return record


def health_check(workspace: str | Path, *, quiet: bool = False) -> dict[str, Any]:
    """Runtime 健康检查：超限终止后立即运行一个快速正常 Agent。"""

    from ..main.budget import Budget, run_with_budget
    from ..main.runner import _answers_equal, _run_reference
    import pandas as pd

    workspace = Path(workspace)
    health_dir = demo_root(workspace) / "health_check"
    health_dir.mkdir(parents=True, exist_ok=True)
    (health_dir / "task.json").write_text(
        json.dumps(TASK, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (health_dir / "data.csv").write_text(DATA_CSV, encoding="utf-8")
    (health_dir / "reference.py").write_text(REFERENCE_PY, encoding="utf-8")
    (health_dir / "agent.py").write_text(GOOD_FAST_AGENT_PY, encoding="utf-8")
    df = pd.read_csv(health_dir / "data.csv")
    started = time.monotonic()
    outcome = run_with_budget(
        str(health_dir / "agent.py"),
        {"task": dict(TASK), "data_csv": str(health_dir / "data.csv")},
        Budget(timeout_seconds=10, max_memory_mb=None, max_disk_mb=None, max_tokens=None),
        {"base_url": "http://localhost:9", "api_key": "DEMO-NO-LLM",
         "model": "demo-no-llm", "timeout": 30.0},
    )
    elapsed = round(time.monotonic() - started, 2)
    reference = _run_reference(health_dir / "reference.py", df.copy(deep=True))
    correct = bool(_answers_equal(outcome.get("result"), reference)) if outcome.get("ok") else False
    record = {
        "status": "PASS" if correct else "FAIL",
        "answer": outcome.get("result"),
        "correct": correct,
        "elapsed_seconds": elapsed,
        "recorded_at": _now_iso(),
    }
    runs_dir = demo_runs_root(workspace) / "health_check"
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / "result.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not quiet:
        print(f"[demo] health_check       status={record['status']} "
              f"answer={record['answer']!r} elapsed={elapsed}s")
    return record


def run_all(workspace: str | Path, *, quiet: bool = False) -> dict[str, Any]:
    """一键四案例（含 Demo04 后的健康检查 + 完整性核查）。"""

    workspace = Path(workspace)
    before = _integrity_snapshot(workspace)
    records = {case: run_case(case, workspace, quiet=quiet) for case in CASE_ORDER}
    health = health_check(workspace, quiet=quiet)
    after = _integrity_snapshot(workspace)

    def _changed(part: str) -> bool:
        return before[part] != after[part]

    policy_changed = any(before["policy"][k] != after["policy"][k]
                         for k in before["policy"])
    expected_map = {case: CASES[case]["expected"] for case in CASE_ORDER}
    statuses = {}
    for case in CASE_ORDER:
        record = records[case]
        if expected_map[case] == "TIMEOUT":
            statuses[case] = "PASS" if record["timed_out"] else "FAIL"
        else:
            statuses[case] = record["status"]
    statuses["health"] = health["status"]
    integrity = {
        "phase1_modified": _changed("phase1"),
        "phase2_modified": _changed("phase2"),
        "policy_modified": policy_changed,
        "before": before,
        "after": after,
    }
    all_pass = all(v == "PASS" for v in statuses.values()) and not any(
        (integrity["phase1_modified"], integrity["phase2_modified"],
         integrity["policy_modified"]))
    summary = {
        "schema_version": "codesetarena.demo.runtime.v1",
        "cases": records,
        "health_check": health,
        "integrity": {k: v for k, v in integrity.items()
                      if k in ("phase1_modified", "phase2_modified",
                               "policy_modified")},
        "real_llm_called": any(
            (r.get("budget") or {}).get("calls") for r in records.values()),
        "statuses": statuses,
        "demo_status": "PASS" if all_pass else "BLOCKED",
        "recorded_at": _now_iso(),
    }
    runs_dir = demo_runs_root(workspace)
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / "demo_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not quiet:
        _print_summary(summary, workspace)
    return summary


def _print_summary(summary: dict[str, Any], workspace: Path) -> None:
    line = "=" * 60
    print(line)
    print("BigData Runtime Resource Control Demo")
    print(line)
    for case in CASE_ORDER:
        spec = CASES[case]
        r = summary["cases"][case]
        print(f"\n[{spec['seq']}/4] Demo {spec['seq']:02d} — {spec['title']}")
        print(f"Agent:\n  {spec['agent_desc']}")
        print(f"Timeout:\n  {spec['timeout']} seconds")
        print("Running...")
        print("RESULT:")
        if r["timed_out"]:
            print("  TIMEOUT")
            print(f"  category={r['classification'].get('internal_error_category')}")
            print(f"  kind={r['classification'].get('resource_kind')}")
            print(f"  elapsed={r['elapsed_seconds']} sec")
        else:
            print(f"  {'PASS' if r['status'] == 'PASS' else 'FAIL'}")
            print(f"  answer={r['answer']!r}")
            print(f"  correct={r['correct']}")
            print(f"  elapsed={r['elapsed_seconds']} sec")
        print("-" * 60)
    print("\nRuntime health check...")
    print(f"{'PASS' if summary['health_check']['status'] == 'PASS' else 'FAIL'}")
    print(line)
    print("SUMMARY")
    print(line)
    print("Too loose: bad agent occupies resources until timeout")
    print("Too strict: good agent is incorrectly terminated")
    print("Reasonable + good agent: PASS")
    print("Reasonable + bad agent: terminated quickly, runtime stays healthy")
    integrity = summary["integrity"]
    print(f"\nPHASE1 MODIFIED: {'YES' if integrity['phase1_modified'] else 'NO'}")
    print(f"PHASE2 MODIFIED: {'YES' if integrity['phase2_modified'] else 'NO'}")
    print(f"FORMAL COURSE POLICY MODIFIED: "
          f"{'YES' if integrity['policy_modified'] else 'NO'}")
    print(f"REAL LLM CALLED: {'YES' if summary['real_llm_called'] else 'NO'}")
    print(f"DEMO ROOT: {demo_root(workspace)}")
    print(f"DEMO STATUS:\n{summary['demo_status']}")
    print(line)


__all__ = [
    "CASE_ORDER",
    "CASES",
    "DemoRuntimeError",
    "health_check",
    "run_all",
    "run_case",
]
