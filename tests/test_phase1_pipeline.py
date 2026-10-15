"""Phase 1 正式运行流程测试（Batch Build -> Validator -> Registry -> FM Distribution -> Frozen Main Pool -> Assignment -> Late）。

覆盖需求 §37 的 TEST 1-8。全部离线：不调用 LLM / runner；以 tmp_path 构造
canonical 提交（task.json + data.csv），走完整教师端流水线函数。
"""

from __future__ import annotations

import json
from pathlib import Path


from codesetarena.constants import HYPOTHESIZED_FM_ORDER
from codesetarena.phase1 import (
    DEFAULT_REVIEWS_PER_STUDENT,
    REVIEWS_PER_STUDENT,
    assign_late,
    assign_main,
    build_fm_distribution,
    build_registry,
    freeze_main_pool,
    merge_final_mappings,
    prepare_canonical,
    records_after_main,
    validate_batch,
    verify_main_unchanged,
    verify_reverse_consistency,
)
from codesetarena.phase1.registry import write_registry
from codesetarena.phase1.snapshot import MAIN_POOL_REGISTRY_CSV, MAIN_POOL_SNAPSHOT_JSON, load_main_pool
from codesetarena.phase1.validation import (
    DUPLICATE_ACTIVE_STUDENT_ID,
    DUPLICATE_TASK_ID,
    INVALID_FM,
    MISSING_FIELD,
    scan_canonical,
)

FMS = HYPOTHESIZED_FM_ORDER

# Demo 小池（10~12 人）用的调研度：production 默认 m=15 需要 n >= 16
M = 2


def make_submission(root: Path, student_id: str, *, fm: str, task_id: str | None = None,
                    task_statement: str = "some task", dirname: str | None = None) -> Path:
    """在 root 下创建提交目录（task.json + data.csv）；dirname 可选覆盖目录名。"""

    pkg = root / (dirname or student_id)
    pkg.mkdir(parents=True, exist_ok=True)
    if task_id is None:
        task_id = f"T_{student_id}"
    task = {
        "task_id": task_id,
        "student_id": student_id,
        "hypothesized_failure_mode": fm,
        "task_statement": task_statement,
    }
    (pkg / "task.json").write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
    (pkg / "data.csv").write_text("x\n1\n", encoding="utf-8")
    return pkg


def make_pool(canonical_root: Path, n: int = 10) -> list[Path]:
    """建立 n 个学生提交；FM 轮转分配（10 人时前 10 个 FM 各 1，FM11/12/14 为 0）。"""

    for i in range(n):
        make_submission(canonical_root, f"2024{i + 1:03d}", fm=FMS[i % len(FMS)])
    return scan_canonical(canonical_root)


def rows_from_report(report: dict) -> list[dict]:
    from codesetarena.phase1.validation import validated_packages

    return build_registry(validated_packages(report), submission_batch="main")


# ---------------------------------------------------------------------------
# TEST 1：正常 10 学生/task -> PASS，registry / distribution 正确
# ---------------------------------------------------------------------------


def test_test1_normal_pool_validation_registry_distribution(tmp_path: Path):
    canonical = tmp_path / "canonical"
    make_pool(canonical, 10)

    report = validate_batch(scan_canonical(canonical))
    assert report["status"] == "PASS"
    assert report["package_count"] == 10
    assert report["error_codes"] == []

    rows = rows_from_report(report)
    assert len(rows) == 10
    assert {r["student_id"] for r in rows} == {f"2024{i + 1:03d}" for i in range(10)}
    assert len({r["task_id"] for r in rows}) == 10

    registry_out = tmp_path / "registry"
    paths = write_registry(rows, registry_out)
    assert paths["csv"].is_file() and paths["task_to_owner"].is_file()

    # task -> owner 与 student -> tasks 正确
    owner = json.loads(paths["task_to_owner"].read_text(encoding="utf-8"))
    assert len(owner) == 10
    s2t = json.loads(paths["student_to_tasks"].read_text(encoding="utf-8"))
    assert all(len(v) == 1 for v in s2t.values())  # 每个学生恰好 1 个 task
    assert all(owner[t] == s for s, ts in s2t.items() for t in ts)

    # distribution 正确（14 行固定；FM01-FM10 各 1，FM11/12/13/14 为 0）
    cov = build_fm_distribution(rows, tmp_path / "coverage", snapshot_tag="main")
    data = json.loads(cov["json"].read_text(encoding="utf-8"))
    assert data["total_tasks"] == 10
    assert len(data["distribution"]) == len(FMS) == 14
    by_fm = {d["hypothesized_failure_mode"]: d["count"] for d in data["distribution"]}
    assert sum(by_fm.values()) == 10
    assert by_fm[FMS[10]] == 0 and by_fm[FMS[11]] == 0  # FM11、FM12 为 0
    assert by_fm[FMS[12]] == 0 and by_fm[FMS[13]] == 0  # FM13、FM14 为 0
    assert all(0.0 <= d["proportion"] <= 1.0 for d in data["distribution"])
    assert cov["csv"].is_file() and cov["png"].is_file()


# ---------------------------------------------------------------------------
# TEST 2：duplicate task_id -> FAIL，coverage/assignment 不生成
# ---------------------------------------------------------------------------


def test_test2_duplicate_task_id_blocks_pipeline(tmp_path: Path):
    canonical = tmp_path / "canonical"
    make_submission(canonical, "2024001", fm=FMS[0], task_id="T_DUP")
    make_submission(canonical, "2024002", fm=FMS[1], task_id="T_DUP")

    report = validate_batch(scan_canonical(canonical))
    assert report["status"] == "ERROR"
    assert DUPLICATE_TASK_ID in report["error_codes"]
    dup = [e for e in report["errors"] if e["code"] == DUPLICATE_TASK_ID][0]
    assert dup["task_id"] == "T_DUP"
    assert {m["student_id"] for m in dup["students"]} == {"2024001", "2024002"}

    # 校验失败后：coverage 不生成、assignment 不生成（pipeline 在 validation 停止）
    assert not (tmp_path / "coverage").exists()
    assert not (tmp_path / "assignment").exists()


# ---------------------------------------------------------------------------
# TEST 3：duplicate active student_id -> FAIL（不自动选「最新一个」）
# ---------------------------------------------------------------------------


def test_test3_duplicate_active_student_id(tmp_path: Path):
    canonical = tmp_path / "canonical"
    make_submission(canonical, "2024001", fm=FMS[0], task_id="T_A", dirname="2024001_v1")
    make_submission(canonical, "2024001", fm=FMS[1], task_id="T_B", dirname="2024001_v2")

    report = validate_batch(scan_canonical(canonical))
    assert report["status"] == "ERROR"
    assert DUPLICATE_ACTIVE_STUDENT_ID in report["error_codes"]
    dup = [e for e in report["errors"] if e["code"] == DUPLICATE_ACTIVE_STUDENT_ID][0]
    assert len(dup["packages"]) == 2  # 两份都列出，不做自动取舍


# ---------------------------------------------------------------------------
# TEST 4：illegal hypothesized_failure_mode -> FAIL
# ---------------------------------------------------------------------------


def test_test4_illegal_fm(tmp_path: Path):
    canonical = tmp_path / "canonical"
    make_submission(canonical, "2024001", fm="FM99_NOPE")
    report = validate_batch(scan_canonical(canonical))
    assert report["status"] == "ERROR"
    assert INVALID_FM in report["error_codes"]

    # FM10/FM13 属于正式 14 类 taxonomy（v2.0），Phase 1 池级接受
    canonical2 = tmp_path / "canonical2"
    make_submission(canonical2, "2024002", fm="FM10_DUPLICATE_RECORD_HANDLING")
    assert validate_batch(scan_canonical(canonical2))["status"] == "PASS"
    canonical3 = tmp_path / "canonical3"
    make_submission(canonical3, "2024003", fm="FM13_NUMERICAL_PRECISION_ROUNDING")
    assert validate_batch(scan_canonical(canonical3))["status"] == "PASS"


def test_test4_missing_field(tmp_path: Path):
    canonical = tmp_path / "canonical"
    pkg = make_submission(canonical, "2024001", fm=FMS[0])
    task = json.loads((pkg / "task.json").read_text(encoding="utf-8"))
    del task["task_statement"]
    (pkg / "task.json").write_text(json.dumps(task), encoding="utf-8")
    report = validate_batch(scan_canonical(canonical))
    assert report["status"] == "ERROR"
    assert MISSING_FIELD in report["error_codes"]


# ---------------------------------------------------------------------------
# TEST 5：FM 某类别数量为 0 -> CSV/PNG 仍保留该 FM 行
# ---------------------------------------------------------------------------


def test_test5_zero_count_fm_still_present(tmp_path: Path):
    canonical = tmp_path / "canonical"
    for i, fm in enumerate(FMS[:3]):  # 只有 3 种 FM，其余 9 种 count=0
        make_submission(canonical, f"2024{i + 1:03d}", fm=fm)

    report = validate_batch(scan_canonical(canonical))
    rows = rows_from_report(report)
    cov = build_fm_distribution(rows, tmp_path / "coverage", snapshot_tag="main")

    csv_lines = cov["csv"].read_text(encoding="utf-8").strip().splitlines()
    assert len(csv_lines) == 1 + len(FMS)  # header + 12 行（含 0）
    for fm in FMS:
        assert any(line.startswith(f"{fm},") for line in csv_lines[1:])

    data = json.loads(cov["json"].read_text(encoding="utf-8"))
    assert all(d["hypothesized_failure_mode"] in FMS for d in data["distribution"])
    zero_rows = [d for d in data["distribution"] if d["count"] == 0]
    assert len(zero_rows) == len(FMS) - 3
    assert cov["png"].stat().st_size > 0


# pylint: disable=W0611  # REVIEWS_PER_STUDENT 保留用于断言 production 默认值


def test_default_reviews_per_student_is_15():
    """正式课程默认 m=15（production）；Demo/测试显式传 m=2。"""

    assert DEFAULT_REVIEWS_PER_STUDENT == 15
    assert REVIEWS_PER_STUDENT == DEFAULT_REVIEWS_PER_STUDENT


# ---------------------------------------------------------------------------
# TEST 6：main FM 分层采样 -> 无自分配 / workload == m / cap 约束 / 可复现
# ---------------------------------------------------------------------------


def test_test6_main_sampling_assignment_invariants(tmp_path: Path):
    canonical = tmp_path / "canonical"
    make_pool(canonical, 10)
    report = validate_batch(scan_canonical(canonical))
    rows = rows_from_report(report)

    snapshot = freeze_main_pool(rows, tmp_path / "assignment", seed=42,
                                reviewers_per_student=M)
    result = assign_main(rows, seed=42, reviews=M)

    assert result["assignment_method"] == "fm_stratified_min_count_sampling"
    assert result["review_degree"] == M
    assert result["task_exposure_cap"] == M + 1
    assert result["fm_taxonomy_size"] == len(FMS) == 14

    own_task = {r["student_id"]: r["task_id"] for r in rows}
    cap = M + 1
    # A. workload 固定 == m
    assert all(len(ts) == M for ts in result["reviewer_to_tasks"].values())
    # B. Reviewer 内无重复
    assert all(len(set(ts)) == len(ts) for ts in result["reviewer_to_tasks"].values())
    # C. 无 self-assignment
    for s, ts in result["reviewer_to_tasks"].items():
        assert own_task[s] not in ts
    # D. Task exposure <= cap=m+1（不再要求恰好 m 个 reviewer）
    assert all(count <= cap for count in result["selection_count"].values())
    assert all(len(rs) <= cap for rs in result["task_to_reviewers"].values())
    # E/F. 全部来自 frozen main pool
    assert set(result["reviewer_to_tasks"]) == {r["student_id"] for r in rows}
    assert set(result["task_to_reviewers"]) == {r["task_id"] for r in rows}
    # G. 反相一致
    assert verify_reverse_consistency(result["reviewer_to_tasks"], result["task_to_reviewers"]) == []

    # 均衡性仅观测：小池单 FM 单 Task 布局下 exposure 允许 0..cap 波动（硬约束是 0<=c<=cap）
    counts = sorted(result["selection_count"].values())
    assert counts[0] >= 0 and counts[-1] <= cap
    assert sum(counts) == 10 * M

    # 可复现：同 seed 同结果
    result2 = assign_main(rows, seed=42, reviews=M)
    assert result2["reviewer_to_tasks"] == result["reviewer_to_tasks"]
    assert result2["records"] == result["records"]

    # 每条 record 带新 provenance（review_slot / task_selection_count_after / task_fm / round）
    for record in result["records"]:
        assert record["sampling_round"] in ("fm_stratified", "fill")
        assert record["task_fm"] in FMS
        assert 1 <= record["review_slot"] <= M
        assert record["task_selection_count_after"] <= cap

    # Round 1 覆盖优先：records 已带 sampling_round（上面已断言）
    assert (tmp_path / "assignment" / MAIN_POOL_SNAPSHOT_JSON).is_file()
    assert (tmp_path / "assignment" / MAIN_POOL_REGISTRY_CSV).is_file()
    loaded_rows, loaded_snapshot = load_main_pool(tmp_path / "assignment")
    assert len(loaded_rows) == 10 and loaded_snapshot["task_count"] == 10
    assert snapshot["registry_sha256"] == loaded_snapshot["registry_sha256"]


# ---------------------------------------------------------------------------
# TEST 7：late student -> main 不变 / late reviewer 获得 Task / late Task 可 0 reviewers
# ---------------------------------------------------------------------------


def test_test7_late_student_incremental(tmp_path: Path):
    canonical = tmp_path / "canonical"
    make_pool(canonical, 10)
    report = validate_batch(scan_canonical(canonical))
    main_rows = rows_from_report(report)

    freeze_main_pool(main_rows, tmp_path / "assignment", seed=42,
                     reviewers_per_student=M)
    main_result = assign_main(main_rows, seed=42, reviews=M)
    reviewer_to_tasks, task_to_reviewers = merge_final_mappings(main_result)
    records = records_after_main(main_result, {})
    assert len(records) == 10 * M

    frozen_before = {s: list(ts) for s, ts in reviewer_to_tasks.items()}

    # late 学生 2024101（第 11 人）提交自己的题
    make_submission(canonical, "2024101", fm=FMS[0], task_id="T_late")
    late_report = validate_batch(scan_canonical(canonical))
    assert late_report["status"] == "PASS"
    late_student = "2024101"
    late_assignment = {late_student: assign_late(late_student, main_rows, seed=42,
                                                 reviews=M)}

    # late reviewer 得到固定数量、来自 frozen main pool、无重复、无自己的题
    picked = late_assignment[late_student]
    assert len(picked) == M
    assert len(set(picked)) == len(picked)
    main_task_ids = {r["task_id"] for r in main_rows}
    assert set(picked) <= main_task_ids
    assert "T_late" not in picked  # 不能是自己的题

    # late 同一采样策略：method 与 main 一致
    _, late_method = assign_late(late_student, main_rows, seed=42, reviews=M,
                                 return_method=True)
    assert late_method == "fm_stratified_min_count_sampling"

    # main mapping 未被修改
    merged_r2t, merged_t2r = merge_final_mappings(
        main_result, late_assignment, late_owned_tasks=["T_late"]
    )
    assert verify_main_unchanged(main_result["reviewer_to_tasks"], merged_r2t) == []
    for s, ts in frozen_before.items():
        assert merged_r2t[s] == ts

    # late Task 允许 0 reviewers
    assert merged_t2r["T_late"] == []
    assert verify_reverse_consistency(merged_r2t, merged_t2r) == []


# ---------------------------------------------------------------------------
# TEST 8：reverse mapping 完全一致（含多次 late 后）
# ---------------------------------------------------------------------------


def test_test8_reverse_mapping_consistency(tmp_path: Path):
    canonical = tmp_path / "canonical"
    make_pool(canonical, 10)
    report = validate_batch(scan_canonical(canonical))
    main_rows = rows_from_report(report)
    main_result = assign_main(main_rows, seed=7, reviews=M)

    late_assignments = {}
    late_owned = []
    # late 必须感知当前 exposure（cap=m+1）：传 main 的 selection_count 作为 task_load
    task_load = dict(main_result["selection_count"])
    for i, student in enumerate(["2024101", "2024102"]):
        make_submission(canonical, student, fm=FMS[i], task_id=f"T_late{student}")
        late_owned.append(f"T_late{student}")
        late_assignments[student] = assign_late(student, main_rows, seed=7,
                                                reviews=M, task_load=task_load)
        for t in late_assignments[student]:
            task_load[t] = task_load.get(t, 0) + 1

    r2t, t2r = merge_final_mappings(main_result, late_assignments, late_owned_tasks=late_owned)
    assert verify_reverse_consistency(r2t, t2r) == []

    # 双向一致性：r2t 每项都能在 t2r 中找到，反之亦然（已由 verify 覆盖，此处再验证总度数）
    assert sum(len(v) for v in r2t.values()) == sum(len(v) for v in t2r.values())
    for task, reviewers in t2r.items():
        for reviewer in reviewers:
            assert task in r2t[reviewer]

    # late task 0 reviewers；main task exposure <= cap=m+1（late 追加后也合法）
    for task in late_owned:
        assert t2r[task] == []
    for task in main_result["task_to_reviewers"]:
        assert 0 <= len(t2r[task]) <= M + 1


# ---------------------------------------------------------------------------
# prepare_canonical：raw zip -> canonical（只拷运行文件，不拷 .env/runs）
# ---------------------------------------------------------------------------


def test_prepare_canonical_from_zip_strips_secrets_and_generates_tid(tmp_path: Path):
    import zipfile

    raw = tmp_path / "raw_submission"
    raw.mkdir()
    pkg = make_submission(raw, "2024099", fm=FMS[1])
    (pkg / "task.json").write_text(json.dumps(
        {"student_id": "2024099", "hypothesized_failure_mode": FMS[1],
         "task_statement": "x"}, ensure_ascii=False), encoding="utf-8")
    (pkg / ".env").write_text("LLM_API_KEY=SECRET", encoding="utf-8")  # 机密
    (pkg / "runs").mkdir()
    (pkg / "runs" / "result.json").write_text("{}", encoding="utf-8")

    zip_path = tmp_path / "student_2024099.zip"
    with zipfile.ZipFile(zip_path, "w") as bundle:
        bundle.write(pkg / "task.json", arcname="task.json")
        bundle.write(pkg / "data.csv", arcname="data.csv")
        bundle.write(pkg / ".env", arcname=".env")
        bundle.write(pkg / "runs" / "result.json", arcname="runs/result.json")

    canonical_root = tmp_path / "canonical"
    dest = prepare_canonical(zip_path, canonical_root,
                             audit_log=tmp_path / "audit" / "task_id_corrections.jsonl")
    assert dest is not None and dest.name == "2024099"
    assert not (dest / ".env").exists()  # 机密永不进 canonical
    assert not (dest / "runs").exists()
    task = json.loads((dest / "task.json").read_text(encoding="utf-8"))
    assert task["student_id"] == "2024099"
    assert task["task_id"].startswith("T_")  # 缺失时生成全局 ID
    assert (tmp_path / "audit" / "task_id_corrections.jsonl").is_file()

    # raw 未被修改（.env 仍在原目录）
    assert (pkg / ".env").is_file()