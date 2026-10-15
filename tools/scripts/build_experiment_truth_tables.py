#!/usr/bin/env python3
"""Build experiment truth tables for the 24 Task x 5 Agent = 120-run manual experiment.

Data flow (single source of truth):

    raw experiment results (workspace/manual/DAxx_Ty_Z/runs/*/result.json)
        -> run_truth_table.csv          (120 rows, per Task x Agent)
        -> truth_matrix.csv             (24 rows x 5 agent columns + per agent footer)
        -> agent_summary.csv            (5 rows)
        -> task_summary.csv             (24 rows)
        -> experiment_truth_table_report.md

The script is strictly READ-ONLY over original experiment data:
it reads task.json / data.csv / reference.py / runs/*/result.json,
and writes ONLY the five output files under --out-dir.

Verdict reuse: the official judge verdict saved in each run's result.json
(field ``correct``, produced by the platform runner/judge) is the single source
of truth for SUCCESS/FAIL. No answer is re-judged and no run is re-executed.

Run:
    .venv/bin/python scripts/build_experiment_truth_tables.py
    .venv/bin/python scripts/build_experiment_truth_tables.py --out-dir /tmp/out
"""

from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import importlib.util
import json
import os
import re
import sys

# --------------------------------------------------------------------------
# Constants (project-fixed registries; do not edit values)
# --------------------------------------------------------------------------

AGENT_REGISTRY = [
    ("A", "Direct"),
    ("B", "Thought-Trace"),
    ("C", "Stateful Pandas Workspace"),
    ("D", "Plan-Then-Code"),
    ("E", "Execute-Then-Verify"),
]

# Failure Mode 全 keys（FM05_JOIN 形式）来自正式 taxonomy 单一真相源：
# codesetarena.constants（随 FM10/FM13 扩展自动更新，禁止在此另建白名单）。
from codesetarena.constants import FAILURE_MODE_FULL_KEYS

VALID_FM = FAILURE_MODE_FULL_KEYS

STATUS_SUCCESS = "SUCCESS"
STATUS_FAIL = "FAIL"
STATUS_MISSING = "MISSING"

# 兼容 DAxx_Tn_<agent> 与 DAxx_FMnn_Tn_<agent> 两种命名（FM10/FM13 新任务）。
# group: 1=DA 编号, 2=FM 编号(可无), 3=T 编号, 4=Agent
DIR_RE = re.compile(r"^(DA\d+)_(?:FM(\d+)_)?(T\d+)_([A-E])$")


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def natural_task_key(variant: str) -> tuple:
    """Natural sort: DA01_T1 < DA01_T2 < DA01_FM10_T1 < DA02_T1 < ... < DA12_T2."""
    m = re.match(r"^DA(\d+)_(?:FM(\d+)_)?T(\d+)$", variant)
    return (int(m.group(1)), int(m.group(2) or 0), int(m.group(3)))


def compute_reference_gold(task_dir: str):
    """Run reference.py over data.csv to obtain the canonical gold answer."""
    ref_path = os.path.join(task_dir, "reference.py")
    data_path = os.path.join(task_dir, "data.csv")
    spec = importlib.util.spec_from_file_location("_manual_ref", ref_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    import pandas as pd
    df = pd.read_csv(data_path)
    return mod.reference(df)


def str_value(v) -> str:
    """Stable string form of an answer value (None -> empty string)."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def failure_reason_from_run(r: dict) -> str:
    """Map the observed direct failure cause to the standard vocabulary.

    Vocabulary: wrong_answer / timeout / exception / parse_error /
    invalid_output / resource_limit / missing_output / incorrect_answer /
    llm_api_error / internal_error.
    """
    if r.get("correct") is False:
        # The run completed and was evaluated; the answer was wrong.
        return "wrong_answer"
    outcome = r.get("outcome_type")
    rerr = (r.get("error") or "").lower()
    category = (r.get("internal_error_category") or "").lower()

    if "timeout" in rerr or "timed out" in rerr:
        return "timeout"
    if "token_budget" in category or "token" in rerr:
        return "resource_limit"
    if "resource_limit" in category or "resource" in category:
        return "resource_limit"
    if "crash" in category or "exception" in rerr or "error" in rerr and "llm" not in rerr:
        return "exception"
    if "llm_api" in category or "llm" in rerr:
        return "llm_api_error"
    if outcome == "internal_error":
        return "internal_error"
    return "incorrect_answer"


# --------------------------------------------------------------------------
# Scan
# --------------------------------------------------------------------------

def scan(manual_root: str):
    """Return ordered task list, agent ids, and per-dir records."""
    dirs = []
    for name in sorted(os.listdir(manual_root)):
        m = DIR_RE.match(name)
        if m and os.path.isdir(os.path.join(manual_root, name)):
            fm = m.group(2)
            variant = m.group(1) + ("_FM" + fm if fm else "") + "_" + m.group(3)
            dirs.append((name, variant, m.group(4)))
    if not dirs:
        sys.exit(f"no DA*_T*_[A-E] workspaces found under {manual_root}")

    # task variants in stable natural order（含 FM10/FM13 新变体）
    variants = sorted({d[1] for d in dirs}, key=natural_task_key)
    agents = [a for a, _ in AGENT_REGISTRY]  # A, B, C, D, E
    agent_name = dict(AGENT_REGISTRY)

    task_meta = {}  # variant -> dict
    run_rows = []   # one row per dir/run
    problems = collections.defaultdict(list)

    for variant in variants:
        vdirs = [d for d in dirs if d[1] == variant]
        # task.json is identical across the 5 copies of a variant
        tj_path = os.path.join(manual_root, vdirs[0][0], "task.json")
        with open(tj_path, encoding="utf-8") as fh:
            tj = json.load(fh)
        task_id = tj["task_id"]
        student_id = tj["student_id"]
        hfm = tj.get("hypothesized_failure_mode")
        if hfm not in VALID_FM:
            problems["invalid_hfm"].append(f"{variant}: {hfm!r}")
        gold = compute_reference_gold(os.path.join(manual_root, vdirs[0][0]))
        task_meta[variant] = {
            "task_id": task_id, "student_id": student_id, "hfm": hfm, "gold": gold,
            "dirs": [d[0] for d in vdirs], "task_statement": tj["task_statement"],
        }

    # Collect exactly one final run record per workspace dir
    for dname in sorted(d[0] for d in dirs):
        dm = DIR_RE.match(dname)
        fm = dm.group(2)
        variant = dm.group(1) + ("_FM" + fm if fm else "") + "_" + dm.group(3)
        agent_id = dname[-1]
        runs_root = os.path.join(manual_root, dname, "runs")
        run_ids = []
        if os.path.isdir(runs_root):
            run_ids = [x for x in sorted(os.listdir(runs_root))
                       if os.path.isdir(os.path.join(runs_root, x))]
        if len(run_ids) > 1:
            problems["multiple_runs"].append(f"{dname}: {run_ids}")
        if not run_ids:
            run_rows.append({
                "dir": dname, "variant": variant, "agent_id": agent_id,
                "run_id": None, "result_path": "", "record": None, "missing": True,
            })
            continue
        rid = run_ids[-1]
        rp = os.path.join(runs_root, rid, "result.json")
        if not os.path.exists(rp):
            problems["missing_result"].append(f"{dname}/{rid}")
            run_rows.append({
                "dir": dname, "variant": variant, "agent_id": agent_id,
                "run_id": rid, "result_path": rp, "record": None, "missing": True,
            })
            continue
        with open(rp, encoding="utf-8") as fh:
            record = json.load(fh)
        run_rows.append({
            "dir": dname, "variant": variant, "agent_id": agent_id,
            "run_id": rid, "result_path": rp, "record": record, "missing": False,
        })

    return variants, agents, agent_name, task_meta, run_rows, problems


def build_truth_rows(run_rows, task_meta, agent_name):
    """Normalize every run into a single final truth-table row."""
    rows = []
    for rr in run_rows:
        variant = rr["variant"]
        meta = task_meta[variant]
        rec = rr["record"]
        name = agent_name[rr["agent_id"]]
        result_path = rr["result_path"]

        if rr["missing"] or rec is None:
            rows.append({
                "task_id": meta["task_id"], "task_sample": variant[-2:],
                "task_variant": variant, "student_id": meta["student_id"],
                "agent_id": rr["agent_id"], "agent_name": name,
                "hypothesized_failure_mode": meta["hfm"], "status": STATUS_MISSING,
                "ground_truth": str_value(meta["gold"]), "agent_answer": "",
                "correct": "", "failure_reason": "", "result_path": result_path or "(no run record)",
            })
            continue

        correct = rec.get("correct")
        res = rec.get("result") or {}
        agent_answer = res.get("result")
        declared_gold = res.get("declared_gold_answer")
        gold = declared_gold if declared_gold is not None else meta["gold"]

        if correct is True:
            status, reason = STATUS_SUCCESS, ""
        elif correct is False:
            status, reason = STATUS_FAIL, failure_reason_from_run(rec)
        elif rec.get("outcome_type") == "internal_error":
            status, reason = STATUS_FAIL, failure_reason_from_run(rec)
        elif agent_answer is None:
            # run exists but no usable answer and no verdict
            status, reason = STATUS_FAIL, "missing_output"
        else:
            status, reason = STATUS_FAIL, "incorrect_answer"

        rows.append({
            "task_id": meta["task_id"], "task_sample": variant[-2:],
            "task_variant": variant, "student_id": meta["student_id"],
            "agent_id": rr["agent_id"], "agent_name": name,
            "hypothesized_failure_mode": meta["hfm"], "status": status,
            "ground_truth": str_value(gold),
            "agent_answer": str_value(agent_answer),
            "correct": "" if correct is None else ("true" if correct else "false"),
            "failure_reason": reason, "result_path": result_path,
        })
    return rows


# --------------------------------------------------------------------------
# Aggregation + CSV output (CSVs derive ONLY from run truth rows)
# --------------------------------------------------------------------------

def key_success(failed_list):
    return "SUCCESS"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    here = os.path.dirname(os.path.abspath(__file__))
    ap.add_argument("--manual-root", default=os.path.normpath(os.path.join(here, "..", "workspace", "manual")))
    ap.add_argument("--out-dir", default=os.path.normpath(os.path.join(here, "..", "experiments", "manual_truth_tables")))
    args = ap.parse_args()

    variants, agents, agent_name, task_meta, run_rows, problems = scan(args.manual_root)
    rows = build_truth_rows(run_rows, task_meta, agent_name)

    # ---- consistency #12: exactly one final record per Task x Agent ----------
    pair_counts = collections.Counter((r["task_variant"], r["agent_id"]) for r in rows)
    dupes = {k: v for k, v in pair_counts.items() if v > 1}
    if dupes:
        problems["duplicate_pairs"].extend(f"{k}: {v}" for k, v in sorted(dupes.items()))

    os.makedirs(args.out_dir, exist_ok=True)
    agent_cols = [agent_name[a] for a in agents]  # fixed order A..E

    # ---- run_truth_table.csv -------------------------------------------------
    rt_path = os.path.join(args.out_dir, "run_truth_table.csv")
    with open(rt_path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["task_id", "task_sample", "task_variant", "student_id", "agent_id",
                    "agent_name", "hypothesized_failure_mode", "status", "ground_truth",
                    "agent_answer", "correct", "failure_reason", "result_path"])
        for r in rows:
            w.writerow([r["task_id"], r["task_sample"], r["task_variant"], r["student_id"],
                        r["agent_id"], r["agent_name"], r["hypothesized_failure_mode"],
                        r["status"], r["ground_truth"], r["agent_answer"], r["correct"],
                        r["failure_reason"], r["result_path"]])

    # ---- aggregate -----------------------------------------------------------
    st_by_pair = {(r["task_variant"], r["agent_id"]): r for r in rows}
    task_success = collections.Counter()
    task_failure = collections.Counter()
    task_missing = collections.Counter()
    agent_success = collections.Counter()
    agent_failure = collections.Counter()
    agent_missing = collections.Counter()
    agent_fail_fm = collections.defaultdict(list)  # agent -> [(variant, fm)]
    for r in rows:
        tv, ag = r["task_variant"], r["agent_id"]
        if r["status"] == STATUS_SUCCESS:
            task_success[tv] += 1; agent_success[ag] += 1
        elif r["status"] == STATUS_FAIL:
            task_failure[tv] += 1; agent_failure[ag] += 1
            agent_fail_fm[ag].append((tv, r["hypothesized_failure_mode"]))
        else:
            task_missing[tv] += 1; agent_missing[ag] += 1

    # ---- truth_matrix.csv ----------------------------------------------------
    tm_path = os.path.join(args.out_dir, "truth_matrix.csv")
    with open(tm_path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["task_id", "hypothesized_failure_mode"] + agent_cols +
                   ["task_success_count", "task_failure_count", "task_missing_count"])
        for tv in variants:
            cell = [st_by_pair[(tv, a)]["status"] for a in agents]
            w.writerow([tv, task_meta[tv]["hfm"]] + cell +
                       [task_success[tv], task_failure[tv], task_missing[tv]])
        w.writerow(["__AGENT_SUCCESS_COUNT__", ""] + [agent_success[a] for a in agents] + ["", "", ""])
        w.writerow(["__AGENT_FAILURE_COUNT__", ""] + [agent_failure[a] for a in agents] + ["", "", ""])
        w.writerow(["__AGENT_MISSING_COUNT__", ""] + [agent_missing[a] for a in agents] + ["", "", ""])

    # ---- agent_summary.csv ----------------------------------------------------
    def fmt_fail_fm(ag):
        cnt = collections.Counter(fm for _, fm in agent_fail_fm[ag])
        return ";".join(f"{fm}:{cnt[fm]}" for fm in sorted(cnt))

    as_path = os.path.join(args.out_dir, "agent_summary.csv")
    with open(as_path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["agent_id", "agent_name", "total_tasks", "success_count", "failure_count",
                    "missing_count", "success_rate", "failure_rate", "failed_hypothesized_failure_modes"])
        for a in agents:
            s, f, m = agent_success[a], agent_failure[a], agent_missing[a]
            w.writerow([a, agent_name[a], len(variants), s, f, m,
                        f"{s / len(variants):.6f}", f"{f / len(variants):.6f}",
                        fmt_fail_fm(a)])

    # ---- task_summary.csv ------------------------------------------------------
    ts_path = os.path.join(args.out_dir, "task_summary.csv")
    with open(ts_path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["task_id", "student_id", "hypothesized_failure_mode", "total_agents",
                    "success_count", "failure_count", "missing_count", "success_rate",
                    "failure_rate", "successful_agents", "failed_agents", "missing_agents",
                    "failed_failure_mode"])
        for tv in variants:
            succ = [agent_name[a] for a in agents if st_by_pair[(tv, a)]["status"] == STATUS_SUCCESS]
            fail = [agent_name[a] for a in agents if st_by_pair[(tv, a)]["status"] == STATUS_FAIL]
            miss = [agent_name[a] for a in agents if st_by_pair[(tv, a)]["status"] == STATUS_MISSING]
            s, f, m = task_success[tv], task_failure[tv], task_missing[tv]
            ffm = task_meta[tv]["hfm"] if f > 0 else ""
            w.writerow([tv, task_meta[tv]["student_id"], task_meta[tv]["hfm"], len(agents),
                        s, f, m, f"{s / len(agents):.6f}", f"{f / len(agents):.6f}",
                        ";".join(succ), ";".join(fail), ";".join(miss), ffm])

    # ---- report ----------------------------------------------------------------
    n_success = sum(1 for r in rows if r["status"] == STATUS_SUCCESS)
    n_fail = sum(1 for r in rows if r["status"] == STATUS_FAIL)
    n_missing = sum(1 for r in rows if r["status"] == STATUS_MISSING)
    report = build_report(variants, agents, agent_name, task_meta, rows,
                          n_success, n_fail, n_missing, problems)
    rp_path = os.path.join(args.out_dir, "experiment_truth_table_report.md")
    with open(rp_path, "w", encoding="utf-8", newline="") as fh:
        fh.write(report)

    print(json.dumps({
        "tasks": len(variants),
        "agents": len(agents),
        "expected_runs": len(variants) * len(agents),
        "found_runs": len(rows),
        "success": n_success, "fail": n_fail, "missing": n_missing,
        "problems": {k: v for k, v in problems.items()},
        "outputs": [rt_path, tm_path, as_path, ts_path, rp_path],
    }, ensure_ascii=False, indent=2))


def build_report(variants, agents, agent_name, task_meta, rows,
                 n_success, n_fail, n_missing, problems):
    L = ["# Experiment Truth Table Report", ""]
    L += ["## Dataset Overview", ""]
    L += [f"- Tasks: {len(variants)}", f"- Agents: {len(agents)}",
          f"- Expected runs: {len(variants) * len(agents)}",
          f"- Found runs: {len(rows)}",
          f"- SUCCESS: {n_success}",
          f"- FAIL: {n_fail}",
          f"- MISSING: {n_missing}", ""]
    L += ["## Agents", ""]
    L += [f"- {a}: {agent_name[a]} ({a})" for a in agents]
    L += ["", "## Tasks", ""]
    L += [f"- {tv}: {task_meta[tv]['task_id']} sample {tv[-1]} | {task_meta[tv]['hfm']} | student {task_meta[tv]['student_id']}" for tv in variants]
    L += ["", "## Agent Performance", ""]
    L.append("| Agent | Success | Fail | Missing | Success Rate |")
    L.append("|---|---|---|---|---|")
    for a in agents:
        s = sum(1 for r in rows if r["agent_id"] == a and r["status"] == STATUS_SUCCESS)
        f = sum(1 for r in rows if r["agent_id"] == a and r["status"] == STATUS_FAIL)
        m = sum(1 for r in rows if r["agent_id"] == a and r["status"] == STATUS_MISSING)
        L.append(f"| {agent_name[a]} ({a}) | {s} | {f} | {m} | {s / len(variants):.6f} |")
    L += ["", "## Task Performance", ""]
    L.append("| Task | Hypothesized Failure Mode | Success | Fail | Missing |")
    L.append("|---|---|---|---|---|")
    for tv in variants:
        s = sum(1 for r in rows if r["task_variant"] == tv and r["status"] == STATUS_SUCCESS)
        f = sum(1 for r in rows if r["task_variant"] == tv and r["status"] == STATUS_FAIL)
        m = sum(1 for r in rows if r["task_variant"] == tv and r["status"] == STATUS_MISSING)
        L.append(f"| {tv} | {task_meta[tv]['hfm']} | {s} | {f} | {m} |")
    L += ["", "## Failure Mode Summary", ""]
    L.append("| failure_mode | task_count | total_runs | success_runs | failure_runs | missing_runs | failure_rate |")
    L.append("|---|---|---|---|---|---|---|")
    fm_of = collections.defaultdict(list)
    for tv in variants:
        fm_of[task_meta[tv]["hfm"]].append(tv)
    sm = collections.Counter((r["task_variant"], r["hypothesized_failure_mode"], r["status"]) for r in rows)
    for fm in sorted(fm_of):
        tvs = fm_of[fm]
        tot = len(tvs) * len(agents)
        s = sum(1 for r in rows if r["hypothesized_failure_mode"] == fm and r["status"] == STATUS_SUCCESS)
        f = sum(1 for r in rows if r["hypothesized_failure_mode"] == fm and r["status"] == STATUS_FAIL)
        m = sum(1 for r in rows if r["hypothesized_failure_mode"] == fm and r["status"] == STATUS_MISSING)
        L.append(f"| {fm} | {len(tvs)} | {tot} | {s} | {f} | {m} | {f / tot:.6f} |")
    L += ["", "## Validation", ""]
    ok = []
    ok.append(("[PASS] 24 tasks" if len(variants) == 24 else f"[FAIL] tasks={len(variants)}"))
    ok.append(("[PASS] 5 agents" if len(agents) == 5 else f"[FAIL] agents={len(agents)}"))
    exp = len(variants) * len(agents)
    ok.append(("[PASS] 120 expected task-agent pairs" if exp == 120 else f"[FAIL] expected={exp}"))
    ok.append(("[PASS] SUCCESS + FAIL + MISSING = " + str(len(rows)) if n_success + n_fail + n_missing == len(rows) == exp else f"[FAIL] n={len(rows)}"))
    ok.append(("[PASS] matrix cells = 24 x 5 = 120" if len(variants) * len(agents) == 120 else "[FAIL] matrix cells"))
    # per task / per agent totals: for every unit, the three status counts sum to the expected size
    def _status_counts(key, value):
        return {s: sum(1 for r in rows if r[key] == value and r["status"] == s)
                for s in (STATUS_SUCCESS, STATUS_FAIL, STATUS_MISSING)}

    task_ok = all(sum(_status_counts("task_variant", tv).values()) == len(agents) for tv in variants)
    agent_ok = all(sum(_status_counts("agent_id", a).values()) == len(variants) for a in agents)
    ok.append(("[PASS] task totals consistent" if task_ok else "[FAIL] task totals"))
    ok.append(("[PASS] agent totals consistent" if agent_ok else "[FAIL] agent totals"))
    # cross equality
    ts = {STATUS_SUCCESS: 0, STATUS_FAIL: 0, STATUS_MISSING: 0}
    ag = {STATUS_SUCCESS: 0, STATUS_FAIL: 0, STATUS_MISSING: 0}
    for r in rows:
        ts[r["status"]] += 1 if r["status"] in ts else 0
    for a in agents:
        for st in (STATUS_SUCCESS, STATUS_FAIL, STATUS_MISSING):
            ag[st] += sum(1 for r in rows if r["agent_id"] == a and r["status"] == st)
    ok.append(("[PASS] sum(task) == sum(agent)" if ts == ag else f"[FAIL] {ts} vs {ag}"))
    ok.append(("[PASS] hypothesized_failure_mode in allowed enum" if all(
        task_meta[tv]["hfm"] in VALID_FM for tv in variants) else "[FAIL] hfm enum"))
    ok.append(("[PASS] at most one record per Task x Agent" if not problems.get("duplicate_pairs") else "[FAIL] duplicate pairs"))
    L += ok
    if problems:
        L += ["", "## Problems", ""]
        for k, v in problems.items():
            L.append(f"- {k} ({len(v)}): {v[:5]}")
    L.append("")
    return "\n".join(L)


if __name__ == "__main__":
    main()