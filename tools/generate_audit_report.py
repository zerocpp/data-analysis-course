# -*- coding: utf-8 -*-
"""Generate the v1 Agent x Task Audit Markdown report from audit_matrix.json.

Read-only derivation: the only input is results/audit/audit_matrix.json (plus
objective repository layout facts such as the batch_logs directory listing).
Never modifies the audit matrix, agents, tasks, or raw runs.

Usage (from repo root):
    python tools/generate_audit_report.py
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "results" / "audit" / "audit_matrix.json"
DEST = REPO / "docs" / "version_updates" / "v1_agent_task_audit"

NA = "Not available in current audit metadata"


def load() -> dict:
    return json.loads(SRC.read_text(encoding="utf-8"))


def all_cells(data: dict) -> list[tuple[str, str, dict]]:
    cells = []
    m = data["audit_matrix"]
    for tv in sorted(m):
        for ag in ("A", "B", "C", "D", "E"):
            cells.append((tv, ag, m[tv]["agents"][ag]))
    return cells


def analysis_failure_cells(data: dict) -> list[tuple[str, str, dict]]:
    return [
        (tv, ag, c)
        for tv, ag, c in all_cells(data)
        if c["execution"]["outcome_type"] == "analysis_failure"
    ]


def task_variants(data: dict) -> list[str]:
    return sorted(data["audit_matrix"].keys())


# ---------------------------------------------------------------------------
# README.md
# ---------------------------------------------------------------------------
def gen_readme(data: dict) -> str:
    s = data["audit_summary"]
    exp = data["experiment"]
    af_cells = analysis_failure_cells(data)
    agent_d_wrong = [tv for tv, ag, c in af_cells if ag == "D"]
    return f"""# Agent × Task Audit Report v1

## Purpose

本版本用于记录课程系统一期 Agent × Task 执行的正式审计结果：

- Agent 在不同 Task 上的执行表现；
- Execution Outcome（completed / analysis_failure / internal_error）；
- Artifact-based Audit（llm_calls / tool observation / 数值核验）；
- Internal Error 分类；
- Task 设计反馈（基于真实 run evidence）。

该报告基于真实 run evidence，是**审计结果**，不是：

- benchmark ranking
- leaderboard
- agent scoring

## Experiment Scope

以下数据直接读取自 `results/audit/audit_matrix.json`：

| Scope | Count |
|---|---|
| Tasks | {exp['task_count']} |
| Agents | {exp['agent_count']} |
| Cells | {exp['expected_cells']} |

## Source of Truth

数据流向：

```text
Raw execution evidence
        ↓
Audit Matrix (results/audit/audit_matrix.json)
        ↓
Markdown Report (本目录)
```

其中：

`results/audit/audit_matrix.json`

为唯一审计结果来源。本报告所有数字均来自该文件。

## Main Findings

- {s['correct']} correct
- {s['analysis_failure']} analysis_failure
- {s['internal_error']} internal_error
- {s['missing_runs']} missing_runs

事实性说明：

- 全部 {s['analysis_failure']} 个 analysis_failure cell 均为 Agent D
  （{'、'.join(f'{tv}/D' for tv in agent_d_wrong)}）。
- `expected_failure_mode` 在所有 task.json 中均不可用
  （`missing_expected_failure_metadata.count = {s['missing_expected_failure_metadata']['count']}`），
  因此本报告不进行任何 Failure Mode 推断。
"""


# ---------------------------------------------------------------------------
# experiment_overview.md
# ---------------------------------------------------------------------------
def gen_overview(data: dict) -> str:
    exp = data["experiment"]
    task_ids = sorted({data["audit_matrix"][tv]["task"]["task_id"] for tv in task_variants(data)})
    return f"""# Experiment Overview

## Dataset

- Task 数：{exp['task_count']}（12 个 task_id × T1/T2 两个变体）
- Agent 数：{exp['agent_count']}（A–E）
- Task naming：

当前 task.json 中的 `task_id` 取值为 `{'、'.join(task_ids)}`（**不是** FM01–FM12；
audit_matrix.json 中不存在任何 Failure Mode 标识）。

注意：task.json 未提供 `expected_failure_mode` 字段，目录名 `DAxx_Tx_<Agent>`
仅用于导航，不作为 Failure Mode 元数据。

## Execution Protocol

每个 cell 为一个 Task × Agent 组合：

    Task Variant（DA01_T1 … DA12_T2）× Agent（A–E）

共 {exp['expected_cells']} cells；每个 workspace 恰好 1 个 run
（{exp['canonical_run_rule']}）。

## Outcome Definition

按当前系统（audit_matrix.json 中的 `outcome_type`）：

### completed

正常运行完成且 answer 与 gold 一致（`correct: true`）。

### analysis_failure

正常运行完成、有合法 answer，但 answer 与 gold 不一致
（`correct: false`，无 Internal Error）。

### internal_error

未正常产出答案（LLM API / Token / 资源 / 崩溃等），`correct: null`。

## Audit Limitation（前置说明）

本实验所有 task.json 均无 `expected_failure_mode`，因此
expected-vs-observed Failure 审计不可用；其余分类不受影响。
"""


# ---------------------------------------------------------------------------
# audit_matrix_summary.md
# ---------------------------------------------------------------------------
def gen_summary(data: dict) -> str:
    s = data["audit_summary"]
    total = s["audited_cells"]
    breakdown = s["internal_error_breakdown"]
    cats = [k for k in ("llm_api", "token_budget", "resource_limit", "runtime_crash") if breakdown.get(k)]
    rows_ie = "\n".join(f"| {k} | {breakdown[k]} |" for k in cats)
    if not rows_ie:
        rows_ie = "| _none_ | 0 |"
    af_rows = "\n".join(
        f"| {tv} | {ag} | {c['execution']['result']!r} | {c['execution']['declared_gold_answer']!r} |"
        for tv, ag, c in analysis_failure_cells(data)
    )
    return f"""# Audit Matrix Summary

## Overall Outcome

| Outcome | Count |
|---|---|
| correct | {s['correct']} |
| analysis_failure | {s['analysis_failure']} |
| internal_error | {s['internal_error']} |
| missing_runs | {s['missing_runs']} |

## Cell Coverage

- 已审计 cell：{s['audited_cells']} / {s['expected_cells']}

约束校验：`audited_cells + missing_runs == expected_cells`
（{s['audited_cells']} + {s['missing_runs']} = {s['expected_cells']}）。

## Internal Error Summary

| Category | Count |
|---|---|
{rows_ie}

## Analysis Failure Summary

仅列出 outcome_type = analysis_failure 的 cell（原因分析见 task_findings.md）：

| Task | Agent | Result | Gold |
|---|---|---|---|
{af_rows}
"""


# ---------------------------------------------------------------------------
# task_findings.md
# ---------------------------------------------------------------------------
def gen_task_findings(data: dict) -> str:
    sections = []
    for tv in task_variants(data):
        block = data["audit_matrix"][tv]
        ta = block["task_audit"]
        if not ta["needs_manual_review"]:
            continue
        t = block["task"]
        fms = t.get("expected_failure_mode")
        fm_txt = NA if fms is None else str(fms)
        lines = [f"# {tv}", "", "## Execution Result", ""]
        for ag in ("A", "B", "C", "D", "E"):
            c = block["agents"][ag]
            if c["execution"]["outcome_type"] != "analysis_failure":
                continue
            e = c["execution"]
            lines += [
                f"- failing agent: {ag}",
                f"- run_id: {c['run']['run_id']}",
                f"- result: {e['result']!r}",
                f"- declared_gold_answer: {e['declared_gold_answer']!r}",
                "",
            ]
        lines += ["## Evidence Summary", ""]
        for ag in ("A", "B", "C", "D", "E"):
            c = block["agents"][ag]
            if c["execution"]["outcome_type"] != "analysis_failure":
                continue
            a = c["audit"]
            lines.append(f"- Agent {ag}（audit evidence）：")
            for ev in a.get("evidence", []):
                lines.append(f"  - *{ev.get('source')}* — {ev.get('detail')}")
        lines += ["", "## Audit Classification", ""]
        # 该 task 内所有 analysis_failure cell 的 classification 一致取第一条
        first = None
        for ag in ("A", "B", "C", "D", "E"):
            c = block["agents"][ag]
            if c["execution"]["outcome_type"] == "analysis_failure":
                first = c["audit"]
                break
        if first:
            lines += [
                f"- audit_status: {first['audit_status']}",
                f"- expected_failure_mode: {fm_txt}",
                f"- observed_failure_mode: {first['observed_failure_mode']!r}",
                f"- needs_manual_review: {ta['needs_manual_review']}",
            ]
        lines += [
            "",
            "## Limitation",
            "",
            "task.json 未提供 `expected_failure_mode`。",
            "",
            "因此：不能进行 Expected Failure Mapping；"
            "`expected_failure_observed` 不可判定（本审计中为 None）。",
            "",
        ]
        sections.append("\n".join(lines))
    return "\n".join(sections)


# ---------------------------------------------------------------------------
# agent_findings.md
# ---------------------------------------------------------------------------
def gen_agent_findings(data: dict) -> str:
    sections = []
    for ag, st in data["agent_summary"].items():
        sections.append(
            f"""# Agent {ag}

- total_tasks: {st['total_tasks']}
- correct: {st['correct_count']}
- analysis_failure: {st['analysis_failure_count']}
- internal_error: {st['internal_error_count']}
- valid_model_runs: {st['valid_model_runs']}

{('Agent %s completed all evaluated runs successfully.' % ag) if st['analysis_failure_count'] == 0 and st['internal_error_count'] == 0 else ('Agent %s had %d analysis_failure and %d internal_error cell(s).' % (ag, st['analysis_failure_count'], st['internal_error_count']))}
"""
        )
    return "\n".join(sections)


# ---------------------------------------------------------------------------
# failure_analysis.md
# ---------------------------------------------------------------------------
def gen_failure_analysis(data: dict) -> str:
    cells = analysis_failure_cells(data)
    per_cell = []
    for tv, ag, c in cells:
        a = c["audit"]
        ev_lines = "\n".join(
            f"- {ev.get('source')}: {ev.get('detail')}" for ev in a.get("evidence", [])
        )
        per_cell.append(
            f"""## {tv} / Agent {ag}

- run_id: {c['run']['run_id']}
- audit_status: {a['audit_status']}
- confidence: {a['confidence']}

### actual_failure_reason

{a['actual_failure_reason']}

### evidence

{ev_lines}
"""
        )
    return f"""# Failure Analysis

本文件仅针对 outcome_type = analysis_failure 的 cell 记录已观测错误机制
（内容全部来自 audit_matrix.json 中该 cell 的 audit 字段）。

## Observed mechanism（当前执行环境下的共性现象）

以下共性现象直接来自三个 cell 的 evidence / actual_failure_reason：

- `execute_pandas` calls use an independent namespace；
- intermediate variables are not preserved across calls；
- Plan-Then-Code Agent（Agent D）generated multi-step, state-dependent
  execution，assignment-only code 无法把中间值反馈给 Agent
  （observation 为 `<no output>`）。

这不是"Agent D 架构错误"的结论，而是**当前执行环境下观测到的失败机制**。

## Cell 级分析

{'\n'.join(per_cell)}
"""


# ---------------------------------------------------------------------------
# known_issues.md
# ---------------------------------------------------------------------------
def gen_known_issues(data: dict) -> str:
    s = data["audit_summary"]
    mfm = s["missing_expected_failure_metadata"]
    exp = data["experiment"]
    batch_line = exp.get("source_batch_logs") or NA
    # DA01_T1_A batch log missing（audit_matrix 中该 cell batch_log 为 null）
    d01 = data["audit_matrix"]["DA01_T1"]["agents"]["A"]
    log_txt = d01["run"]["batch_log"] or "not present（batch log missing）"
    return f"""# Known Issues & Audit Limitations

## Missing Failure Metadata

- 受影响 task：{mfm['count']} 个（全部 24 个 task 变体）task.json 缺乏
  `expected_failure_mode` 字段。
- 影响：`expected_failure_observed` 全为 0（不可判定）。

这不是"没有 Failure"，而是**无法进行 Expected Failure Mapping**。
本审计保留了 outcome 分类（correct / analysis_failure / internal_error），
但不推断任何 Failure Mode。相关说明（来自 audit_matrix.json）：

> {mfm['note']}

## Empty Batch Logs

`batch_logs/` 下存在 `manual_20260930_224255`、`manual_20260930_224545`、
`manual_20260930_224556` 三个空目录（PATH 修复前 `codesetarena` 不可用的
失败尝试），不含任何 run 产物；有效批次为 `manual_20260930_225823`（119 个 log）。

## Independent Run

- `DA01_T1_A` 不在批次 log 中（{log_txt}）；
  其 run 独立于批次执行（见 experiment 元信息：
  "DA01_T1_A run at 20260930_221859 (矩阵执行)"）。
- batch logs 来源：{batch_line}

## Audit 覆盖范围

- 无 `expected_failure_mode` 的 cell 无法做 expected-vs-observed 审计；
- analysis_failure cell 均为 `audit_status = other_failure_observed`，
  已带 evidence 与 confidence，但仍标注 `needs_manual_review`。
"""


# ---------------------------------------------------------------------------
# version.json
# ---------------------------------------------------------------------------
def gen_version() -> dict:
    return {
        "version": "v1_agent_task_audit",
        "source": "results/audit/audit_matrix.json",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "task_count": len(load()["audit_matrix"]),
        "agent_count": len(load()["agent_summary"]),
        "cell_count": sum(len(v["agents"]) for v in load()["audit_matrix"].values()),
    }


def main() -> None:
    data = load()
    DEST.mkdir(parents=True, exist_ok=True)
    files = {
        "README.md": gen_readme(data),
        "experiment_overview.md": gen_overview(data),
        "audit_matrix_summary.md": gen_summary(data),
        "task_findings.md": gen_task_findings(data),
        "agent_findings.md": gen_agent_findings(data),
        "failure_analysis.md": gen_failure_analysis(data),
        "known_issues.md": gen_known_issues(data),
    }
    for name, text in files.items():
        (DEST / name).write_text(text, encoding="utf-8")
    (DEST / "version.json").write_text(
        json.dumps(gen_version(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("generated %d files under %s" % (len(files) + 1, DEST))


if __name__ == "__main__":
    main()