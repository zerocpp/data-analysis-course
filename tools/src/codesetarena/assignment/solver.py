"""统一 Assignment Solver Core（唯一实现 = Benchmark 与生产共用）。

Benchmark（benchmark/assignment_diversity_v1/methods/*）与本生产包都从这里
导入；不存在第二份 CATEGORY_AWARE_SWAP 实现。

统一协议：``solve_assignment(category_vector, r, seed, config)`` 返回
``AssignmentResult``（raw 200x200 binary matrix + provenance）。方法只生成
矩阵，不计算任何分数；评分由独立 Evaluator 完成。

方法集（Phase B 冻结）：

- RANDOM_FEASIBLE     可行性-only 随机 baseline（r-offset cycle 共轭置换）
- CATEGORY_AWARE_GREEDY  构造期类别启发式（未覆盖类别优先 -> 剩余容量 -> 随机 tie-break）
- RANDOM_SWAP         无目标 rewiring baseline（全部合法 swap 接受）
- CATEGORY_AWARE_SWAP 目标引导局部搜索（仅减少同类 collision 的 swap 接受；tie 由 seed 硬币）—— 生产默认
- OPTIMIZED_ASSIGNMENT  精确 Min-Cost Max-Flow 参考上界（确定型）

生产默认方法 = CATEGORY_AWARE_SWAP（policy/assignment_policy.json 指定）。
"""

from __future__ import annotations

import heapq
import random
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Optional

NUM_STUDENTS = 200
NUM_TASKS = 200
NUM_CATEGORIES = 14
DEFAULT_R = 5
SOLVER_VERSION = "assignment-solver-v1.0"


@dataclass
class AssignmentResult:
    """Raw solver output：200x200 binary matrix + provenance（不含任何分数）。"""

    method: str
    seed: Optional[int]
    r: int
    matrix: list  # list[list[int]] 200x200，值 ∈ {0, 1}
    runtime_seconds: float
    metadata: dict = field(default_factory=dict)
    deterministic: bool = False

    @property
    def method_name(self) -> str:
        return self.method


Solver = Callable[[list, int, int, dict], AssignmentResult]


class AssignmentMethod:
    """注册表条目：solver + 默认 config。"""

    def __init__(
        self,
        name: str,
        solver: Solver,
        deterministic: bool = False,
        default_config: Optional[dict] = None,
    ) -> None:
        self.name = name
        self.solver = solver
        self.deterministic = deterministic
        self.default_config = default_config or {}

    def solve(
        self,
        category_vector: list,
        r: int,
        seed: int,
        config: Optional[dict] = None,
    ) -> AssignmentResult:
        cfg = dict(self.default_config)
        if config:
            cfg.update(config)
        return self.solver(category_vector, r, seed, cfg)


# ---------------------------------------------------------------------------
# 矩阵 / 任务集转换
# ---------------------------------------------------------------------------


def matrix_from_task_sets(n: int, task_sets: list) -> list:
    matrix = [[0] * n for _ in range(n)]
    for i, tasks in enumerate(task_sets):
        for j in tasks:
            matrix[i][j] = 1
    return matrix


def task_sets_from_matrix(matrix: list) -> list:
    n = len(matrix)
    return [{j for j in range(n) if matrix[i][j]} for i in range(n)]


# ---------------------------------------------------------------------------
# 合法 swap 机制（M3/M4 共用；行和/列和/无自评由构造保持）
# ---------------------------------------------------------------------------


def draw_legal_swap(task_sets: list, rng, n: int, max_attempts: int = 64):
    for _ in range(max_attempts):
        a = rng.randrange(n)
        if not task_sets[a]:
            continue
        x = rng.choice(sorted(task_sets[a]))
        b = rng.randrange(n)
        if b == a:
            continue
        if not task_sets[b]:
            continue
        y = rng.choice(sorted(task_sets[b]))
        if x == y:
            continue
        if y in task_sets[a]:
            continue
        if x in task_sets[b]:
            continue
        if y == a or x == b:
            continue
        return a, x, b, y
    return None


def apply_swap(task_sets: list, a: int, x: int, b: int, y: int) -> None:
    task_sets[a].discard(x)
    task_sets[a].add(y)
    task_sets[b].discard(y)
    task_sets[b].add(x)


# ---------------------------------------------------------------------------
# M1 — RANDOM_FEASIBLE
# ---------------------------------------------------------------------------


def solve_random_feasible(category_vector: list, r: int, seed: int, config: dict) -> AssignmentResult:
    del config
    n = len(category_vector)
    start = time.perf_counter()
    perm = list(range(n))
    random.Random(seed).shuffle(perm)
    pos = [0] * n
    for idx, val in enumerate(perm):
        pos[val] = idx
    matrix = [[0] * n for _ in range(n)]
    for i in range(n):
        pi = perm[i]
        for k in range(1, r + 1):
            matrix[i][pos[(pi + k) % n]] = 1
    return AssignmentResult(
        method="RANDOM_FEASIBLE",
        seed=seed,
        r=r,
        matrix=matrix,
        runtime_seconds=time.perf_counter() - start,
        metadata={"construction": "r-offset cycle conjugated by seeded permutation", "n": n},
        deterministic=False,
    )


# ---------------------------------------------------------------------------
# M2 — CATEGORY_AWARE_GREEDY
# ---------------------------------------------------------------------------


def _greedy_construct(vector: list, r: int, rng: random.Random):
    n = len(vector)
    order = list(range(n))
    rng.shuffle(order)
    capacity = [r] * n
    task_sets = [set() for _ in range(n)]
    for i in order:
        seen = {vector[j] for j in task_sets[i]}
        priorities = list(range(n))
        rng.shuffle(priorities)
        candidates = [j for j in range(n) if j != i and capacity[j] > 0]
        if len(candidates) < r:
            return None
        candidates.sort(
            key=lambda j: (0 if vector[j] not in seen else 1, -capacity[j], priorities[j])
        )
        for j in candidates[:r]:
            task_sets[i].add(j)
            capacity[j] -= 1
    return task_sets


def _greedy_fallback_sets(vector: list, r: int, seed: int) -> list:
    n = len(vector)
    result = solve_random_feasible(vector, r, seed, {})
    return [{j for j in range(n) if result.matrix[i][j]} for i in range(n)]


def solve_category_greedy(category_vector: list, r: int, seed: int, config: dict) -> AssignmentResult:
    n = len(category_vector)
    max_restarts = int(config.get("max_restarts", 100))
    start = time.perf_counter()
    restarts = 0
    task_sets = None
    for restart in range(max_restarts + 1):
        rng = random.Random(seed + 1000 * restart)
        task_sets = _greedy_construct(category_vector, r, rng)
        if task_sets is not None:
            restarts = restart
            break
    fallback_used = task_sets is None
    if fallback_used:
        task_sets = _greedy_fallback_sets(category_vector, r, seed)
    matrix = matrix_from_task_sets(n, task_sets)
    return AssignmentResult(
        method="CATEGORY_AWARE_GREEDY",
        seed=seed,
        r=r,
        matrix=matrix,
        runtime_seconds=time.perf_counter() - start,
        metadata={
            "strategy": "unseen-category first, capacity-descending, random tie-break",
            "restarts": restarts,
            "fallback_used": fallback_used,
            "n": n,
        },
        deterministic=False,
    )


# ---------------------------------------------------------------------------
# M3 — RANDOM_SWAP
# ---------------------------------------------------------------------------


def solve_random_swap(category_vector: list, r: int, seed: int, config: dict) -> AssignmentResult:
    n = len(category_vector)
    max_iterations = int(config.get("max_iterations", 100000))
    start = time.perf_counter()
    rng = random.Random(seed)
    initial = solve_random_feasible(category_vector, r, seed, {})
    task_sets = [{j for j in range(n) if initial.matrix[i][j]} for i in range(n)]
    accepted = 0
    for _ in range(max_iterations):
        swap = draw_legal_swap(task_sets, rng, n)
        if swap is None:
            continue
        a, x, b, y = swap
        apply_swap(task_sets, a, x, b, y)
        accepted += 1
    matrix = matrix_from_task_sets(n, task_sets)
    return AssignmentResult(
        method="RANDOM_SWAP",
        seed=seed,
        r=r,
        matrix=matrix,
        runtime_seconds=time.perf_counter() - start,
        metadata={
            "strategy": "random feasible initial + all legal random swaps accepted",
            "max_iterations": max_iterations,
            "accepted_swaps": accepted,
            "n": n,
        },
        deterministic=False,
    )


# ---------------------------------------------------------------------------
# M4 — CATEGORY_AWARE_SWAP（生产默认方法）
# ---------------------------------------------------------------------------


def solve_category_swap(category_vector: list, r: int, seed: int, config: dict) -> AssignmentResult:
    n = len(category_vector)
    ncat = max(category_vector)
    max_iterations = int(config.get("max_iterations", 100000))
    tie_prob = float(config.get("tie_accept_probability", 0.5))
    start = time.perf_counter()
    rng = random.Random(seed)

    initial = solve_random_feasible(category_vector, r, seed, {})
    task_sets = [{j for j in range(n) if initial.matrix[i][j]} for i in range(n)]

    category_of = {j: category_vector[j] for j in range(n)}
    counts = [[0] * (ncat + 1) for _ in range(n)]
    for i in range(n):
        for j in task_sets[i]:
            counts[i][category_of[j]] += 1
    total_collisions = sum(c * (c - 1) // 2 for i in range(n) for c in counts[i])

    accepted = 0
    improving = 0
    ties = 0
    for _ in range(max_iterations):
        swap = draw_legal_swap(task_sets, rng, n)
        if swap is None:
            continue
        a, x, b, y = swap
        cx = category_of[x]
        cy = category_of[y]
        delta = (counts[a][cy] - (counts[a][cx] - 1)) + (counts[b][cx] - (counts[b][cy] - 1))
        if delta < 0:
            accept = True
            improving += 1
        elif delta > 0:
            accept = False
        else:
            accept = rng.random() < tie_prob
            ties += int(accept)
        if not accept:
            continue
        apply_swap(task_sets, a, x, b, y)
        counts[a][cx] -= 1
        counts[a][cy] += 1
        counts[b][cy] -= 1
        counts[b][cx] += 1
        total_collisions += delta
        accepted += 1

    matrix = matrix_from_task_sets(n, task_sets)
    return AssignmentResult(
        method="CATEGORY_AWARE_SWAP",
        seed=seed,
        r=r,
        matrix=matrix,
        runtime_seconds=time.perf_counter() - start,
        metadata={
            "strategy": "random feasible initial + swap accepted iff total collisions decrease (tie: seeded coin)",
            "max_iterations": max_iterations,
            "accepted_swaps": accepted,
            "improving_swaps": improving,
            "tie_accepted": ties,
            "final_total_collisions": int(total_collisions),
            "n": n,
        },
        deterministic=False,
    )


# ---------------------------------------------------------------------------
# M5 — OPTIMIZED_ASSIGNMENT（精确 Min-Cost Max-Flow）
# ---------------------------------------------------------------------------


class _MinCostFlow:
    """Primal-dual min-cost max-flow + Dinic-style blocking flow（level 图防环）。"""

    def __init__(self, n_nodes: int) -> None:
        self.n = n_nodes
        self.graph = [[] for _ in range(n_nodes)]

    def add_edge(self, u: int, v: int, cap: int, cost: int) -> None:
        self.graph[u].append([v, cap, cost, len(self.graph[v])])
        self.graph[v].append([u, 0, -cost, len(self.graph[u]) - 1])

    def _dijkstra(self, s: int, potentials: list) -> list:
        n = self.n
        inf = float("inf")
        dist = [inf] * n
        dist[s] = 0
        pq = [(0, s)]
        graph = self.graph
        while pq:
            d, u = heapq.heappop(pq)
            if d != dist[u]:
                continue
            for v, cap, cost, _rev in graph[u]:
                if cap <= 0:
                    continue
                nd = d + cost + potentials[u] - potentials[v]
                if nd < dist[v]:
                    dist[v] = nd
                    heapq.heappush(pq, (nd, v))
        return dist

    def _blocking_push(self, s: int, t: int, limit: int, potentials: list) -> int:
        n = self.n
        level = [-1] * n
        level[s] = 0
        dq = deque([s])
        graph = self.graph
        while dq:
            u = dq.popleft()
            for v, cap, cost, _rev in graph[u]:
                if cap > 0 and level[v] < 0 and potentials[v] == potentials[u] + cost:
                    level[v] = level[u] + 1
                    dq.append(v)
        if level[t] < 0:
            return 0
        self.level = level
        self.arc = [0] * n
        total = 0
        while total < limit:
            pushed = self._dfs_level(s, t, limit - total, potentials)
            if pushed == 0:
                break
            total += pushed
        return total

    def _dfs_level(self, u: int, t: int, f: int, potentials: list) -> int:
        if u == t:
            return f
        graph = self.graph
        while self.arc[u] < len(graph[u]):
            e = graph[u][self.arc[u]]
            v, cap, cost, rev = e
            if (
                cap > 0
                and self.level[v] == self.level[u] + 1
                and potentials[v] == potentials[u] + cost
            ):
                pushed = self._dfs_level(v, t, min(f, cap), potentials)
                if pushed:
                    e[1] -= pushed
                    graph[v][rev][1] += pushed
                    return pushed
            self.arc[u] += 1
        return 0

    def min_cost_max_flow(self, s: int, t: int, required: int):
        n = self.n
        inf = float("inf")
        potentials = [0] * n
        flow = 0
        total_cost = 0
        while flow < required:
            dist = self._dijkstra(s, potentials)
            if dist[t] == inf:
                break
            for v in range(n):
                if dist[v] < inf:
                    potentials[v] += dist[v]
            while flow < required:
                pushed = self._blocking_push(s, t, required - flow, potentials)
                if pushed == 0:
                    break
                flow += pushed
                total_cost += pushed * (potentials[t] - potentials[s])
        return flow, total_cost


def _build_and_solve(vector: list, r: int):
    n = len(vector)
    task_categories = {j: vector[j] for j in range(n)}
    nonempty = sorted({c for c in vector})

    s = 0
    reviewer = {i: 1 + i for i in range(n)}
    task = {j: 1 + n + j for j in range(n)}
    mid = {}
    next_id = 1 + 2 * n
    for i in range(n):
        for c in nonempty:
            mid[(i, c)] = next_id
            next_id += 1
    t = next_id
    next_id += 1

    mcf = _MinCostFlow(next_id)
    for i in range(n):
        mcf.add_edge(s, reviewer[i], r, 0)
    for i in range(n):
        for c in nonempty:
            m = mid[(i, c)]
            for k in range(r):
                mcf.add_edge(reviewer[i], m, 1, k)
            for j in range(n):
                if j != i and task_categories[j] == c:
                    mcf.add_edge(m, task[j], 1, 0)
    for j in range(n):
        mcf.add_edge(task[j], t, r, 0)

    flow, cost = mcf.min_cost_max_flow(s, t, n * r)
    if flow != n * r:
        raise RuntimeError(f"min-cost flow failed: pushed {flow}/{n * r}")

    task_sets = [[] for _ in range(n)]
    for i in range(n):
        for c in nonempty:
            m = mid[(i, c)]
            for v, cap, _cost, _rev in mcf.graph[m]:
                if v in task.values() and cap == 0:
                    j = v - (1 + n)
                    task_sets[i].append(j)
    matrix = [[0] * n for _ in range(n)]
    for i in range(n):
        for j in task_sets[i]:
            matrix[i][j] = 1
    return matrix, cost


def solve_optimized(category_vector: list, r: int, seed: int, config: dict) -> AssignmentResult:
    del config, seed
    n = len(category_vector)
    start = time.perf_counter()
    matrix, collisions = _build_and_solve(category_vector, r)
    return AssignmentResult(
        method="OPTIMIZED_ASSIGNMENT",
        seed=None,
        r=r,
        matrix=matrix,
        runtime_seconds=time.perf_counter() - start,
        metadata={
            "strategy": "exact min-cost max-flow; minimizes total same-category collisions",
            "solver": "primal-dual with potentials + blocking flow",
            "optimal": True,
            "global_optimum_total_collisions": int(collisions),
            "n": n,
        },
        deterministic=True,
    )


# ---------------------------------------------------------------------------
# 注册表（Benchmark 与生产共用的唯一来源）
# ---------------------------------------------------------------------------


METHODS = {
    "RANDOM_FEASIBLE": AssignmentMethod(
        "RANDOM_FEASIBLE", solve_random_feasible, deterministic=False,
    ),
    "CATEGORY_AWARE_GREEDY": AssignmentMethod(
        "CATEGORY_AWARE_GREEDY", solve_category_greedy, deterministic=False,
        default_config={"max_restarts": 100},
    ),
    "RANDOM_SWAP": AssignmentMethod(
        "RANDOM_SWAP", solve_random_swap, deterministic=False,
        default_config={"max_iterations": 100000},
    ),
    "CATEGORY_AWARE_SWAP": AssignmentMethod(
        "CATEGORY_AWARE_SWAP", solve_category_swap, deterministic=False,
        default_config={"max_iterations": 100000, "tie_accept_probability": 0.5},
    ),
    "OPTIMIZED_ASSIGNMENT": AssignmentMethod(
        "OPTIMIZED_ASSIGNMENT", solve_optimized, deterministic=True,
    ),
}

DEFAULT_METHOD = "CATEGORY_AWARE_SWAP"


def solve_assignment(category_vector: list, r: int, seed: int,
                     config: Optional[dict] = None,
                     method: str = DEFAULT_METHOD) -> AssignmentResult:
    """生产 / 研究统一入口：method 默认 CATEGORY_AWARE_SWAP（policy 可覆盖）。"""
    return METHODS[method].solve(category_vector, r, seed, config)