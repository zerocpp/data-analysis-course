# -*- coding: utf-8 -*-
"""Phase 2 匿名化随机均衡分配(10 学生 × 2 陌生题,每道题 2 位审者)。

算法:随机置换 π → 学生 i 获得 T(π[i]+1)、T(π[i]+2)(模 10 轮转)。
性质:无自分配(偏移 ≥1)、每人恰好 2 题、每道题恰好 2 人、可复现(seed)。
"""
import json
import random
from pathlib import Path

ROOT = Path(__file__).parent.parent
RUNS = ROOT / "runs"
SEED = 42

STUDENTS = [f"A{i:02d}" for i in range(1, 11)]
TASKS = [f"T{i:02d}" for i in range(1, 11)]


def assign(seed=SEED):
    rng = random.Random(seed)
    pi = list(range(10))
    rng.shuffle(pi)
    students, per_task = {}, {}
    for i, s in enumerate(STUDENTS):
        t1, t2 = TASKS[(pi[i] + 1) % 10], TASKS[(pi[i] + 2) % 10]
        students[s] = [t1, t2]
        per_task.setdefault(t1, []).append(s)
        per_task.setdefault(t2, []).append(s)
    # 均衡性断言
    assert all(len(v) == 2 for v in students.values()), "每人应恰好 2 题"
    assert all(len(v) == 2 for v in per_task.values()), "每道题应恰好 2 人"
    for s, ts in students.items():
        owner = TASKS[STUDENTS.index(s)]
        assert owner not in ts, f"{s} 不能看到自己的题 {owner}"
    assignment = {
        "seed": seed,
        "students": students,
        "per_task_reviewers": per_task,
        "balanced": True,
        "anonymized": True,
    }
    RUNS.mkdir(parents=True, exist_ok=True)
    (RUNS / "assignment.json").write_text(json.dumps(assignment, ensure_ascii=False, indent=2), encoding="utf-8")
    return assignment


def roster():
    """教师侧保密映射:学生 ↔ 自己设计的题(OWN)。"""
    r = {s: TASKS[STUDENTS.index(s)] for s in STUDENTS}
    (RUNS / "roster.json").write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    return r


if __name__ == "__main__":
    a = assign()
    print("分配方案(匿名):")
    for s, ts in a["students"].items():
        print(f"  {s}: {ts}")
    print("\n每道题审稿人:")
    for t, rs in a["per_task_reviewers"].items():
        print(f"  {t}: {rs}")
