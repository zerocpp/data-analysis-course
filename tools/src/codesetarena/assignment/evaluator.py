"""Diversity Evaluator（生产 Audit 指标；Benchmark 亦从其导入）。

生产角色：Assignment 通过 Hard Gate 之后，记录当前 cohort 实际 assignment 的
CATEGORY_PAIR_DIVERSITY 与 exposure MAE 作为审计指标（非 Acceptance Gate）。
Benchmark 角色：同样的函数作为冻结主评分（evaluation_spec.json 公式）。

公式（冻结）：
    a_ic       category-c task 数（reviewer i）
    collision_i = sum_c C(a_ic, 2)
    D_i        = 1 - collision_i / C(r, 2)
    D_case     = mean_i D_i
    score      = 100 * D_case
诊断（不进分）：
    exposure MAE = mean_{i,c} |a_ic - r * (M_c - I[c_i=c]) / (N-1)|
"""

from __future__ import annotations

import math
import statistics
from collections import Counter

CaseInput = dict


def _percentiles(sorted_vals: list, ps: tuple) -> dict:
    n = len(sorted_vals)
    out = {}
    for p in ps:
        if n == 1:
            out[f"P{p}"] = float(sorted_vals[0])
        else:
            pos = (n - 1) * p / 100.0
            lo = int(pos)
            frac = pos - lo
            out[f"P{p}"] = sorted_vals[lo] * (1 - frac) + sorted_vals[lo + 1] * frac
    return out


def evaluate_assignment(case_input: CaseInput, assignment, r: int) -> dict:
    vector = case_input["category_vector"]
    n = len(vector)
    ncat = int(case_input.get("num_categories", 14))

    tasks_per_category = Counter(vector)
    count_rows = [[0] * ncat for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if assignment[i][j]:
                count_rows[i][vector[j] - 1] += 1

    collisions_i = [sum(math.comb(a, 2) for a in count_rows[i]) for i in range(n)]
    pair_total = math.comb(r, 2)
    d_i = [1.0 - collisions_i[i] / pair_total if pair_total else 1.0 for i in range(n)]
    d_case = sum(d_i) / n
    case_score = 100.0 * d_case

    mae_accum = 0.0
    for i in range(n):
        c_i = vector[i]
        for c in range(1, ncat + 1):
            m_c = tasks_per_category.get(c, 0)
            p_ic = (m_c - (1 if c_i == c else 0)) / (n - 1)
            mu_ic = r * p_ic
            mae_accum += abs(count_rows[i][c - 1] - mu_ic)
    exposure_mae = mae_accum / (n * ncat)

    sorted_d = sorted(d_i)
    diversity = {
        "mean": float(sum(d_i) / n),
        "std": float(statistics.pstdev(d_i)),
        "min": float(min(d_i)),
        "max": float(max(d_i)),
        "median": float(statistics.median(d_i)),
    }
    diversity.update(_percentiles(sorted_d, (10, 25, 75, 90)))

    return {
        "case_id": case_input.get("case_id"),
        "score": case_score,
        "d_case": d_case,
        "collisions_total": sum(collisions_i),
        "exposure_mae": exposure_mae,
        "reviewer_diversity": diversity,
        "d_i": [float(v) for v in d_i],
    }