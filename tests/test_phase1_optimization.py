"""FM-Stratified Sequential Min-Count Sampling 测试（取代旧 cyclic-shift / local search 测试）。

覆盖：14 类 FM 正式参与、硬约束 A-G、最低 exposure 优先、FM 分层 Round 1、
fill Round 2、seeded 可复现、reviewer 顺序随机化、INSUFFICIENT_MAIN_POOL guard、
CAPACITY_EXHAUSTED、late 同一策略 + 不修改 main（含 snapshot hash 不变）、
pipeline 新 artifact 与 provenance、多池 property。全离线。
"""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

from codesetarena.constants import HYPOTHESIZED_FM_CODES, HYPOTHESIZED_FM_ORDER
from codesetarena.phase1 import (
    AssignmentCapacityError,
    InsufficientMainPoolError,
    assign_late,
    assign_main,
    check_main_constraints,
    compute_reviewer_diversity,
    merge_final_mappings,
    run_main_assignment_pipeline,
    verify_main_unchanged,
    verify_reverse_consistency,
)
from codesetarena.phase1.assignment import _sample_single_reviewer
from codesetarena.phase1.optimize import (
    assert_hard_invariants,
    write_diversity_audit,
)

F = HYPOTHESIZED_FM_ORDER  # 14 个 canonical full keys（唯一枚举源）

# 8 人：4 FM × 2 Task（min-count-first 下能形成均衡 exposure 的池）
MIXED_LAYOUT = ["FM01_REQUIREMENT", "FM01_REQUIREMENT", "FM02_SCHEMA", "FM02_SCHEMA",
                "FM03_DATA_TYPE", "FM03_DATA_TYPE", "FM06_AGGREGATION", "FM06_AGGREGATION"]


def make_rows(layout: list[str]) -> list[dict]:
    return [
        {"task_id": f"T{i}", "student_id": f"S{i}", "hypothesized_failure_mode": fm}
        for i, fm in enumerate(layout)
    ]


def build_fm_struct(rows: list[dict]):
    from codesetarena.constants import HYPOTHESIZED_FM_ORDER as _ORDER

    fm_to_tasks = {fm: [] for fm in _ORDER}
    for r in rows:
        fm_to_tasks.setdefault(r["hypothesized_failure_mode"], []).append(r["task_id"])
    task_fm = {r["task_id"]: r["hypothesized_failure_mode"] for r in rows}
    owner = {r["task_id"]: r["student_id"] for r in rows}
    return fm_to_tasks, task_fm, owner


def rerun_order(students: list[str], seed: int) -> list[str]:
    rng = random.Random(seed)
    order = list(students)
    rng.shuffle(order)
    return order


# ---------------------------------------------------------------------------
# TEST A — 14 类 FM 正式参与（FM10/FM13 同等处理）
# ---------------------------------------------------------------------------


def test_a_fm10_fm13_participate_in_registry_assignment_audit(tmp_path: Path):
    from codesetarena.phase1 import build_registry, validate_batch, write_registry

    layout = [
        "FM02_SCHEMA", "FM10_DUPLICATE_RECORD_HANDLING", "FM13_NUMERICAL_PRECISION_ROUNDING",
        "FM06_AGGREGATION", "FM04_FILTER_SORT", "FM05_JOIN", "FM07_TIME_TIMEZONE",
        "FM08_MISSING_VALUE", "FM09_OUTLIER_BOUNDARY", "FM11_PLANNING_DECOMPOSITION",
    ]
    rows = make_rows(layout)
    from codesetarena.phase1.validation import scan_canonical

    canonical = tmp_path / "canonical"
    for r in rows:
        pkg = canonical / r["student_id"]
        pkg.mkdir(parents=True, exist_ok=True)
        task = dict(r)
        task["task_statement"] = "some task"  # Batch Validator 必填字段
        (pkg / "task.json").write_text(
            json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    report = validate_batch(scan_canonical(canonical))
    assert report["status"] == "PASS"

    reg_rows = build_registry(report["packages"], submission_batch="main")
    paths = write_registry(reg_rows, tmp_path / "registry")
    registry = json.loads(paths["json"].read_text(encoding="utf-8"))
    fms = {r["hypothesized_failure_mode"] for r in registry["rows"]}
    assert "FM10_DUPLICATE_RECORD_HANDLING" in fms
    assert "FM13_NUMERICAL_PRECISION_ROUNDING" in fms

    result = assign_main(reg_rows, seed=42, reviews=2)
    assert check_main_constraints(result) == []
    assert_hard_invariants(result["reviewer_to_tasks"], result["task_to_reviewers"], reg_rows, 2)

    audit = write_diversity_audit(
        tmp_path / "assignment_diversity.csv", reg_rows, result["reviewer_to_tasks"],
        {r["student_id"] for r in reg_rows},
    )
    text = audit.read_text(encoding="utf-8")
    assert "FM10_DUPLICATE_RECORD_HANDLING" in text  # 进入 diversity 统计
    assert "FM13_NUMERICAL_PRECISION_ROUNDING" in text


# ---------------------------------------------------------------------------
# TEST B — 新 sampling 硬约束 A-G
# ---------------------------------------------------------------------------


def test_b_sampling_preserves_hard_constraints():
    rows = make_rows(MIXED_LAYOUT)
    result = assign_main(rows, seed=0, reviews=2)
    r2t, t2r = result["reviewer_to_tasks"], result["task_to_reviewers"]
    own_task = {r["student_id"]: r["task_id"] for r in rows}
    assert_hard_invariants(r2t, t2r, rows, 2)
    assert check_main_constraints(result) == []
    assert result["assignment_method"] == "fm_stratified_min_count_sampling"
    assert all(len(v) == 2 for v in r2t.values())
    assert all(len(v) <= 3 for v in t2r.values())  # cap = m+1 = 3
    assert verify_reverse_consistency(r2t, t2r) == []
    for s, ts in r2t.items():
        assert own_task[s] not in ts  # 无 self-assignment
        assert len(set(ts)) == len(ts)  # reviewer 内无重复 Task


# ---------------------------------------------------------------------------
# TEST C — 确定性 + reviewer 顺序随机化
# ---------------------------------------------------------------------------


def test_c_determinism_and_reviewer_order_randomization():
    rows = make_rows(MIXED_LAYOUT)
    result1 = assign_main(rows, seed=0, reviews=2)
    result2 = assign_main(rows, seed=0, reviews=2)
    assert result1["reviewer_to_tasks"] == result2["reviewer_to_tasks"]
    assert result1["records"] == result2["records"]

    # reviewer 处理顺序 = seeded shuffle（禁止固定 student_id 顺序）
    students = sorted({r["student_id"] for r in rows})
    assert list(result1["reviewer_to_tasks"].keys()) == rerun_order(students, seed=0)
    assert list(result1["reviewer_to_tasks"].keys()) != students  # 已打乱
    # 不同 seed → 顺序去相关（确定性断言，避免 flaky）
    assert rerun_order(students, seed=0) == ["S4", "S1", "S5", "S2", "S0", "S3", "S7", "S6"]
    assert rerun_order(students, seed=1) == ["S3", "S6", "S1", "S5", "S7", "S0", "S4", "S2"]


# ---------------------------------------------------------------------------
# TEST D — Round 1 同一 FM 内「最低 exposure count 优先」
# ---------------------------------------------------------------------------


def test_d_round1_min_count_first():
    rows = make_rows(["FM02_SCHEMA", "FM02_SCHEMA", "FM02_SCHEMA", "FM02_SCHEMA",
                      "FM06_AGGREGATION"])
    fm_to_tasks, task_fm, owner = build_fm_struct(rows)
    main_tasks = sorted(owner)
    # 构造 selection_count：FM02 桶内 T1=5 / T2=4 / T3=4 / T4=7，FM06 桶 T5=1
    # （单元测试直接驱动 helper，cap 用 8 以便保留多级 count 对比）
    selection_count = {"T0": 5, "T1": 4, "T2": 4, "T3": 7, "T4": 1}
    rng = random.Random(42)
    picked, edges = _sample_single_reviewer(
        "S5", main_tasks, fm_to_tasks, task_fm, owner, selection_count,
        m=2, cap=8, rng=rng, batch="main", method="fm_stratified_min_count_sampling", seed=42,
    )
    # Round 1（fm_stratified）：T0..T3 属于 FM02，min_count=4，只能从 T1/T2 随机选
    # Round 1（FM06）：T4 count=1 → 必选
    assert len(picked) == 2
    round1 = [e for e in edges if e["sampling_round"] == "fm_stratified"]
    assert len(round1) == 2
    by_fm = {e["task_fm"]: e["task_id"] for e in round1}
    assert by_fm["FM02_SCHEMA"] in {"T1", "T2"}  # 不可能是 count=5 的 T0 / count=7 的 T3
    assert by_fm["FM06_AGGREGATION"] == "T4"
    # 选中后立即 +1
    fin = {"T0": 5, "T1": 4, "T2": 4, "T3": 7, "T4": 1}
    fin[by_fm["FM02_SCHEMA"]] += 1
    fin["T4"] += 1
    assert selection_count == fin


# ---------------------------------------------------------------------------
# TEST E — Round 1 FM 分层覆盖 + Round 2 fill（14 FM × 2 池，m=15）
# ---------------------------------------------------------------------------


def test_e_round1_fm_stratified_round2_fill():
    layout = [fm for fm in F for _ in range(2)]  # 28 rows：每 FM 2 题
    rows = make_rows(layout)
    result = assign_main(rows, seed=42, reviews=15)
    assert check_main_constraints(result) == []
    per_reviewer_rounds: dict[str, dict] = {}
    for record in result["records"]:
        per_reviewer_rounds.setdefault(record["reviewer_id"], {"fm_stratified": 0, "fill": 0})
        per_reviewer_rounds[record["reviewer_id"]][record["sampling_round"]] += 1
    for reviewer, rounds in per_reviewer_rounds.items():
        # Round 1 拿到 14 个不同 FM；fill 只补 1 个 slot
        assert rounds["fm_stratified"] == 14
        assert rounds["fill"] == 1
        fms = {record["task_fm"] for record in result["records"]
               if record["reviewer_id"] == reviewer}
        assert len(fms) == 14  # 14 个不同 FM 全覆盖
    # 全部 Task exposure 均衡（28 task × 15 reviewer = 420 edges，理论均值 15）
    counts = sorted(result["selection_count"].values())
    assert counts[0] >= 13  # 理论上全部接近 15，允许轻微偏差
    assert counts[-1] - counts[0] <= 2


# ---------------------------------------------------------------------------
# TEST F — 某 FM 只有 Reviewer 自己是 owner -> 跳过该 FM，fill 补足
# ---------------------------------------------------------------------------


def test_f_skip_self_owned_fm_then_fill():
    # S0 是 FM01 桶唯一任务 owner：S0 的 FM01 候选为空；FM02 桶 3 题 -> Round 1 拿 1 个，
    # Round 2 fill 补第 2 个
    layout = ["FM01_REQUIREMENT", "FM02_SCHEMA", "FM02_SCHEMA", "FM02_SCHEMA"]
    rows = make_rows(layout)
    result = assign_main(rows, seed=3, reviews=2)
    assert check_main_constraints(result) == []
    assert "T0" not in result["reviewer_to_tasks"]["S0"]  # 永不 self-review
    s0_edges = [r for r in result["records"] if r["reviewer_id"] == "S0"]
    s0_rounds = sorted(r["sampling_round"] for r in s0_edges)
    assert s0_rounds == ["fill", "fm_stratified"]  # FM01 跳过 -> Round1 1 个 + fill 1 个
    # 无重复 / 无自审
    assert len(set(result["reviewer_to_tasks"]["S0"])) == 2


# ---------------------------------------------------------------------------
# TEST G — INSUFFICIENT_MAIN_POOL guard（production m=15，pool 不足 n>=16）
# ---------------------------------------------------------------------------


def test_g_insufficient_main_pool_guard():
    rows = make_rows(F[:11])  # 11 人（当前 Demo 规模）
    try:
        assign_main(rows, seed=42, reviews=15)
    except InsufficientMainPoolError as exc:
        assert "INSUFFICIENT_MAIN_POOL" in str(exc)
        assert "15" in str(exc)
    else:
        raise AssertionError("11 人 + m=15 必须报 INSUFFICIENT_MAIN_POOL")
    # 同池 m=2（Demo 显式覆盖）必须成功
    result = assign_main(rows, seed=42, reviews=2)
    assert check_main_constraints(result) == []

    # compatibility：DEFAULT 15 对 16 人可行
    rows16 = make_rows((F * 2)[:16])
    result16 = assign_main(rows16, seed=1, reviews=15)
    assert check_main_constraints(result16) == []
    assert all(len(v) == 15 for v in result16["reviewer_to_tasks"].values())


# ---------------------------------------------------------------------------
# TEST H — CAPACITY_EXHAUSTED：Fill Round 无合法候选 -> AssignmentCapacityError
# ---------------------------------------------------------------------------


def test_h_capacity_exhausted_raises():
    rows = make_rows(["FM02_SCHEMA", "FM02_SCHEMA", "FM02_SCHEMA", "FM02_SCHEMA"])
    fm_to_tasks, task_fm, owner = build_fm_struct(rows)
    main_tasks = sorted(owner)
    cap = 3
    # 所有非自有 Task 都已到 cap → S7 没有任何合法候选
    selection_count = {"T0": cap, "T1": cap, "T2": cap, "T3": cap}
    rng = random.Random(1)
    try:
        _sample_single_reviewer(
            "S7", main_tasks, fm_to_tasks, task_fm, owner, selection_count,
            m=2, cap=cap, rng=rng, batch="main", method="fm_stratified_min_count_sampling",
            seed=1,
        )
    except AssignmentCapacityError as exc:
        message = str(exc)
        assert "CAPACITY_EXHAUSTED" in message
        assert "S7" in message
        assert "selected_count" in message and "required_count" in message
        assert "eligible_remaining=0" in message
        assert f"cap={cap}" in message
    else:
        raise AssertionError("cap 全部占满时必须抛 AssignmentCapacityError")


# ---------------------------------------------------------------------------
# TEST I — Late 复用同一采样策略：cap / 无自审 / main 不变
# ---------------------------------------------------------------------------


def test_i_late_same_policy_respects_cap():
    rows = make_rows(MIXED_LAYOUT)
    main_result = assign_main(rows, seed=0, reviews=2)
    main_mapping = main_result["reviewer_to_tasks"]
    cap = main_result["task_exposure_cap"]

    late_picked, method, edges = assign_late(
        "S100", rows, seed=5, reviews=2, return_method=True, return_edges=True,
    )
    assert method == "fm_stratified_min_count_sampling"  # 同一策略
    assert len(late_picked) == 2
    assert len(set(late_picked)) == 2
    assert all(edge["sampling_round"] in ("fm_stratified", "fill") for edge in edges)

    merged_r2t, merged_t2r = merge_final_mappings(
        main_result, {"S100": late_picked}, late_owned_tasks=["T_late"]
    )
    assert verify_main_unchanged(main_mapping, merged_r2t) == []
    assert verify_reverse_consistency(merged_r2t, merged_t2r) == []
    # late 追加后 exposure 仍 <= cap
    assert all(len(rs) <= cap for rs in merged_t2r.values())

    # task_load（已有 exposure）参与采样：近 cap 的 Task 不会被继续抽中
    load = {t: cap - 1 for t in [r["task_id"] for r in rows]}
    late2 = assign_late("S101", rows, seed=6, reviews=2, task_load=load)
    assert all(load[t] + 1 <= cap for t in late2)


# ---------------------------------------------------------------------------
# TEST J — Late snapshot 不变：main_assignment.json hash before == hash after
# ---------------------------------------------------------------------------


def test_j_main_snapshot_unchanged_by_late(tmp_path: Path):
    from codesetarena.phase1.snapshot import freeze_main_pool, load_main_pool

    rows = make_rows(MIXED_LAYOUT)
    freeze_main_pool(rows, tmp_path / "assignment", seed=0, reviewers_per_student=2)
    frozen_rows, snapshot = load_main_pool(tmp_path / "assignment")
    run_main_assignment_pipeline(
        frozen_rows, snapshot, tmp_path / "assignment", seed=0, reviews=2,
    )
    main_path = tmp_path / "assignment" / "main_assignment.json"
    assert main_path.is_file()

    def sha(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    before = sha(main_path)
    loaded_main = json.loads(main_path.read_text(encoding="utf-8"))
    main_mapping = loaded_main["main_reviewer_to_tasks"]

    late_picked = assign_late("S100", rows, seed=7, reviews=2)
    main_proxy = {
        "seed": loaded_main.get("seed", 0),
        "assignment_batch": "main",
        "assignment_method": loaded_main["assignment_method"],
        "reviews_per_student": loaded_main["reviews_per_student"],
        "student_count": len(main_mapping),
        "task_count": len(main_mapping),
        "reviewer_to_tasks": main_mapping,
        "task_to_reviewers": _t2r(main_mapping),
        "records": [
            json.loads(line) for line in (tmp_path / "assignment" / "assignment_records.jsonl")
            .read_text(encoding="utf-8").splitlines()
            if line.strip() and json.loads(line).get("assignment_batch") == "main"
        ],
    }
    from codesetarena.phase1 import records_after_main, write_assignment_outputs

    merged_r2t, merged_t2r = merge_final_mappings(
        main_proxy, {"S100": late_picked}, late_owned_tasks=["T_late"]
    )
    records = records_after_main(main_proxy, {"S100": late_picked}, seed=7)
    base_keys = {"seed", "assignment_batch", "assignment_method", "reviews_per_student",
                 "student_count", "task_count", "anonymized", "frozen_snapshot",
                 "main_reviewer_to_tasks"}
    extra_meta = {k: v for k, v in loaded_main.items() if k not in base_keys}
    write_assignment_outputs(
        tmp_path / "assignment",
        main_result=main_proxy,
        reviewer_to_tasks=merged_r2t,
        task_to_reviewers=merged_t2r,
        records=records,
        snapshot=snapshot,
        main_mapping=main_mapping,
        extra_meta=extra_meta,
    )
    after = sha(main_path)
    assert before == after  # §32：main snapshot 一旦写出后不可被 late 修改


def _t2r(main_mapping: dict[str, list[str]]) -> dict[str, list[str]]:
    t2r: dict[str, list[str]] = {}
    for reviewer, tasks in main_mapping.items():
        for task in tasks:
            t2r.setdefault(task, []).append(reviewer)
    return t2r


# ---------------------------------------------------------------------------
# TEST K — pipeline 新 artifact + provenance（绝无 cyclic 字样）
# ---------------------------------------------------------------------------


def test_k_pipeline_artifacts_and_provenance(tmp_path: Path):
    from codesetarena.phase1.snapshot import freeze_main_pool, load_main_pool

    rows = make_rows((F * 2)[:16])
    freeze_main_pool(rows, tmp_path / "assignment", seed=42, reviewers_per_student=15)
    frozen_rows, snapshot = load_main_pool(tmp_path / "assignment")
    meta = run_main_assignment_pipeline(
        frozen_rows, snapshot, tmp_path / "assignment", seed=42, reviews=15,
    )
    assert meta["method"] == "fm_stratified_min_count_sampling"

    assignment_dir = tmp_path / "assignment"
    assert (assignment_dir / "task_exposure.csv").is_file()
    assert (assignment_dir / "assignment_summary.json").is_file()
    assert (assignment_dir / "reviewer_to_tasks.json").is_file()
    assert (assignment_dir / "task_to_reviewers.json").is_file()

    main_assignment = json.loads(
        (assignment_dir / "main_assignment.json").read_text(encoding="utf-8")
    )
    raw = (assignment_dir / "main_assignment.json").read_text(encoding="utf-8")
    assert "balanced_cyclic_shift" not in raw
    assert "cyclic_shift_local_search" not in raw
    assert main_assignment["assignment_method"] == "fm_stratified_min_count_sampling"
    assert main_assignment["sampling_mode"] == "sequential"
    assert main_assignment["replacement_across_reviewers"] is True
    assert main_assignment["replacement_within_reviewer"] is False
    assert main_assignment["review_degree"] == 15
    assert main_assignment["task_exposure_cap"] == 16
    assert main_assignment["seed"] == 42
    assert main_assignment["fm_taxonomy_size"] == 14
    assert len(main_assignment["main_reviewer_to_tasks"]) == 16

    # task_exposure.csv schema
    exposure_lines = (assignment_dir / "task_exposure.csv").read_text(
        encoding="utf-8"
    ).strip().splitlines()
    assert exposure_lines[0] == "task_id,owner_id,fm,reviewer_count,exposure_cap,at_cap"
    assert len(exposure_lines) == 1 + 16
    assert all(line.count(",") == 5 for line in exposure_lines[1:])

    summary = json.loads(
        (assignment_dir / "assignment_summary.json").read_text(encoding="utf-8")
    )
    assert summary["method"] == "fm_stratified_min_count_sampling"
    assert summary["main_pool_size"] == 16
    assert summary["review_degree"] == 15
    assert summary["task_exposure_cap"] == 16
    assert summary["total_assignment_edges"] == 16 * 15
    for key in ("min", "mean", "median", "max"):
        assert key in summary["task_exposure"]
        assert key in summary["reviewer_fm_diversity"]

    # records 每条带 sampling_round / review_slot / task_selection_count_after
    records = [
        json.loads(line)
        for line in (assignment_dir / "assignment_records.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    assert len(records) == 16 * 15
    for record in records:
        assert record["assignment_method"] == "fm_stratified_min_count_sampling"
        assert record["sampling_round"] in ("fm_stratified", "fill")
        assert record["task_fm"] in F

    # diversity audit 含新列
    audit = (assignment_dir / "assignment_diversity.csv").read_text(encoding="utf-8")
    assert "review_count" in audit and "expected_review_count" in audit
    assert "fm_coverage_round_count" in audit and "fill_round_count" in audit


# ---------------------------------------------------------------------------
# TEST L — Property：多个 (n, m) 池全部完成且满足硬约束
# ---------------------------------------------------------------------------


def test_l_property_across_pool_sizes():
    scenarios = [
        (12, 2),
        (16, 2),   # n = m + 1 边界
        (16, 5),
        (16, 15),  # n = m + 1 边界（FM 分层保证完成）
        (20, 5),
        (20, 10),
        (30, 15),  # 接近生产形态
    ]
    for n, m in scenarios:
        layout = [F[i % len(F)] for i in range(n)]
        rows = make_rows(layout)
        result = assign_main(rows, seed=hash((n, m)) % 1000, reviews=m)
        assert check_main_constraints(result) == [], f"n={n}, m={m}"
        assert_hard_invariants(result["reviewer_to_tasks"], result["task_to_reviewers"], rows, m)


def test_l2_capacity_exhausted_main_no_partial_write(tmp_path: Path):
    """§19：capacity 不足时显式抛错且不写 partial mappings（n=20/m=15 边界池）。"""
    from codesetarena.phase1.snapshot import freeze_main_pool, load_main_pool

    rows = make_rows([F[i % len(F)] for i in range(20)])
    freeze_main_pool(rows, tmp_path / "assignment", seed=42, reviewers_per_student=15)
    frozen_rows, snapshot = load_main_pool(tmp_path / "assignment")
    try:
        run_main_assignment_pipeline(
            frozen_rows, snapshot, tmp_path / "assignment", seed=42, reviews=15,
        )
    except AssignmentCapacityError as exc:
        assert "CAPACITY_EXHAUSTED" in str(exc)
    else:
        raise AssertionError("20 人 + 单/双 Task FM + m=15 边界池应显式失败")
    # 失败后不得写 partial final mappings
    assert not (tmp_path / "assignment" / "reviewer_to_tasks.json").exists()
    assert not (tmp_path / "assignment" / "task_to_reviewers.json").exists()
    assert not (tmp_path / "assignment" / "main_assignment.json").exists()


# ---------------------------------------------------------------------------
# TEST M — Main 主路径不再调用 legacy optimizer（monkeypatch 崩溃也不影响）
# ---------------------------------------------------------------------------


def test_m_main_path_never_calls_legacy_optimizer(tmp_path: Path, monkeypatch):
    from codesetarena.phase1 import optimize as optimize_module
    from codesetarena.phase1.snapshot import freeze_main_pool, load_main_pool

    def boom(*args, **kwargs):
        raise RuntimeError("legacy optimizer must not be called")

    monkeypatch.setattr(optimize_module, "optimize_main_assignment", boom)

    rows = make_rows(MIXED_LAYOUT)
    freeze_main_pool(rows, tmp_path / "assignment", seed=0, reviewers_per_student=2)
    frozen_rows, snapshot = load_main_pool(tmp_path / "assignment")
    meta = run_main_assignment_pipeline(
        frozen_rows, snapshot, tmp_path / "assignment", seed=0, reviews=2,
    )
    assert meta["method"] == "fm_stratified_min_count_sampling"
    final = json.loads(
        (tmp_path / "assignment" / "reviewer_to_tasks.json").read_text(encoding="utf-8")
    )
    assert set(final) == {r["student_id"] for r in rows}


# ---------------------------------------------------------------------------
# 辅助：diversity 语义与 canonical FM 唯一源
# ---------------------------------------------------------------------------


def test_diversity_semantics_and_canonical_fm():
    task_fm = {
        "T1": "FM10_DUPLICATE_RECORD_HANDLING",
        "T2": "FM13_NUMERICAL_PRECISION_ROUNDING",
    }
    assert compute_reviewer_diversity("r", ["T1", "T2"], task_fm, own_fm="FM02_SCHEMA") == 2
    assert compute_reviewer_diversity("r", ["T1", "T1"], task_fm, own_fm="FM02_SCHEMA") == 1
    task_fm2 = {"T3": "FM02_SCHEMA", "T4": "FM13_NUMERICAL_PRECISION_ROUNDING"}
    assert compute_reviewer_diversity("r", ["T3", "T4"], task_fm2, own_fm="FM02_SCHEMA") == 1
    assert HYPOTHESIZED_FM_CODES == {*HYPOTHESIZED_FM_ORDER}  # canonical 唯一源
    assert len(HYPOTHESIZED_FM_ORDER) == 14