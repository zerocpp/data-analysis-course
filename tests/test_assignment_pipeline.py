"""Assignment Pipeline 生产集成测试（synthetic，无真实学生数据）。

链条：Submission Archive -> batch-validate -> ingest（真实 ingest 路径）
   -> canonical repository -> assignment build -> verifier -> private mapping
   -> anonymous distribution -> distribution verify -> leakage scan -> READY。

覆盖 §67-§81：mini cohort、200 人全链、自评/重复/coverage 篡改、非法/缺失
category、匿名泄露、source/canonical ID 泄露、数量、一致性、own-task 缺席、
可复现、不同 seed、幂等、Reviewer Archive 导出校验。
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from codesetarena import constants as K
from codesetarena.assignment import input_builder as ib
from codesetarena.assignment import distribution as dist_mod
from codesetarena.assignment import run as run_mod
from codesetarena.assignment.verifier import verify_assignment
from codesetarena.submission import contract as C
from codesetarena.submission.ingest import ingest_submissions
from codesetarena.submission.registry import SubmissionRegistry

PHASE = "phase1"
N_MINI = 20
N_FULL = 200
R = 5
FMS = list(K.HYPOTHESIZED_FM_ORDER)


def _sid(i: int) -> str:
    return f"TEST_STUDENT_{i:03d}"


def _tid(i: int) -> str:
    return "T_" + f"{i:030d}"


def _make_tree(root: Path, sid: str, task_id: str, fm: str) -> Path:
    (root / "task/data").mkdir(parents=True)
    (root / "agent").mkdir(parents=True)
    (root / "manifest.json").write_text(
        json.dumps({"schema_version": C.SCHEMA_VERSION, "course": "DataC1004",
                    "submission_phase": PHASE, "student_id": sid, "task_id": task_id,
                    "build_status": "passed", "files": {}}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (root / "task/task.json").write_text(
        json.dumps({"task_id": task_id, "student_id": sid,
                    "hypothesized_failure_mode": fm,
                    "task_statement": "synthetic task statement（不含身份信息）"},
                   ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (root / "task/data/data.csv").write_text("region,amount\neast,10\nwest,20\n", encoding="utf-8")
    (root / "agent/agent.py").write_text(
        "def run(task, df, llm):\n    return str(int(df['amount'].sum()))\n", encoding="utf-8"
    )
    (root / "agent/agent_manifest.json").write_text(
        json.dumps({"schema_version": C.SCHEMA_VERSION, "student_id": sid,
                    "task_id": task_id, "entrypoint": "agent.py"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (root / "task/reference.py").write_text("def reference(df):\n    return str(int(df['amount'].sum()))\n",
                                            encoding="utf-8")
    (root / ".env.example").write_text("LLM_BASE_URL=\nLLM_API_KEY=\n", encoding="utf-8")
    return root


def build_synthetic_repo(tmp_path: Path, n: int) -> tuple[Path, dict]:
    """真实链路造 repo：workspace tree -> 正式 tar.gz -> batch-validate -> ingest。"""
    from codesetarena.submission.archive import create_submission_archive

    batch_in = tmp_path / f"batch_{n}"
    repo = tmp_path / f"repo_{n}"
    batch_in.mkdir(parents=True)
    for i in range(1, n + 1):
        sid = _sid(i)
        tid = _tid(i)
        tree = _make_tree(tmp_path / f"tree_{sid}", sid, tid, FMS[(i - 1) % 14])
        archive = batch_in / f"DataC1004_P1_{sid}.tar.gz"
        create_submission_archive(tree, archive)
    report = ingest_submissions(repo, batch_in)
    assert report["status"] == "PASS", report
    summary = report["summary"]
    assert summary["ingested"] == n, report
    return repo, report


@pytest.fixture(scope="session")
def repo_200(tmp_path_factory):
    repo, _ = build_synthetic_repo(tmp_path_factory.mktemp("assign200"), N_FULL)
    return repo


@pytest.fixture(scope="module")
def repo_20(tmp_path_factory):
    repo, _ = build_synthetic_repo(tmp_path_factory.mktemp("assign20"), N_MINI)
    return repo


@pytest.fixture(scope="module")
def run_200(repo_200):
    """200 人正式全链（build + distribute）一次，供多个测试复用。"""
    record = run_mod.build_assignment_run(repo_200, PHASE, seed=5)
    assert record["status"] == "VERIFIED", record
    done = run_mod.distribute_assignment_run(repo_200, record["assignment_run_id"])
    assert done["status"] == "READY", done.get("errors")
    return repo_200, record["assignment_run_id"]


def _run_verified(repo: Path, seed: int = 7) -> dict:
    record = run_mod.build_assignment_run(repo, PHASE, seed=seed)
    assert record["status"] == "VERIFIED", record
    return record


def _matrix(repo: Path, run_id: str):
    return np.load(repo / "assignments" / run_id / "assignment.npy").tolist()


def test_mini_cohort_20_end_to_end(repo_20):
    """§67：20 人最小 cohort 全链验证。"""
    record = _run_verified(repo_20, seed=1)
    run_id = record["assignment_run_id"]
    assert record["num_reviewers"] == 20
    assert record["total_assignments"] == 200
    assert record["verifier_status"] == "PASS"
    assert record["min_reviewer_load"] == record["max_reviewer_load"] == 10
    assert record["min_task_coverage"] == record["max_task_coverage"] == 10
    assert record["self_review_count"] == 0
    assert record["duplicate_pair_count"] == 0

    done = run_mod.distribute_assignment_run(repo_20, run_id)
    assert done["status"] == "READY", done.get("errors")
    assert done["identity_leaks"] == 0
    dist_root = repo_20 / "distributions" / run_id / "reviewers"
    packages = list(dist_root.iterdir())
    assert len(packages) == 20


def test_full_200_end_to_end(run_200):
    """§68/§77/§78/§79/§91：200 人正式 synthetic 全链。"""
    repo_200, run_id = run_200
    record = run_mod.load_run(repo_200, run_id)
    assert record["num_reviewers"] == 200
    assert record["num_tasks"] == 200
    assert record["r"] == 10
    assert record["total_assignments"] == 2000
    assert record["verifier_status"] == "PASS"
    assert record["min_reviewer_load"] == 10
    assert record["max_reviewer_load"] == 10
    assert record["min_task_coverage"] == 10
    assert record["max_task_coverage"] == 10
    assert record["self_review_count"] == 0
    assert record["duplicate_pair_count"] == 0
    assert record["status"] == "READY"
    assert record["identity_leaks"] == 0
    assert record["reviewer_packages"] == 200

    reg = SubmissionRegistry(repo_200)
    # 每 task 出现恰好 5 次；每 reviewer 恰好 5 个 task；无 own task
    rows = [r for r in reg.assignments if r.get("assignment_run_id") == run_id]
    assert len(rows) == 2000
    from collections import Counter

    counts = Counter(row["task_id"] for row in rows)
    assert set(counts.values()) == {10}
    per_reviewer = Counter(row["reviewer_student_id"] for row in rows)
    assert set(per_reviewer.values()) == {10}
    # reviewer 的 own task 不进自己的包
    snapshot = json.loads((repo_200 / "assignments" / run_id / "input_snapshot.json").read_text())
    task_to_owner = {t["task_id"]: t["owner_student_id"] for t in snapshot["tasks"]}
    for package in sorted((repo_200 / "distributions" / run_id / "reviewers").iterdir()):
        manifest = json.loads((package / "assignment.json").read_text())
        assert len(manifest["anonymous_tasks"]) == 10
    for row in rows:
        assert task_to_owner[row["task_id"]] != row["reviewer_student_id"]

    # §78：从最终包的匿名 ID 反向重建矩阵 == assignment.npy
    matrix = _matrix(repo_200, run_id)
    n = 200
    rebuilt = [[0] * n for _ in range(n)]
    task_index = {tid: j for j, tid in enumerate(snapshot["task_order"])}
    for package in sorted((repo_200 / "distributions" / run_id / "reviewers").iterdir()):
        idx = int(package.name[1:]) - 1
        ats = json.loads((package / "assignment.json").read_text())["anonymous_tasks"]
        for at in ats:
            canonical = reg.find_canonical_task(at)
            rebuilt[idx][task_index[canonical["task_id"]]] += 1
    assert rebuilt == matrix
    assert all(rebuilt[i][i] == 0 for i in range(n))


def test_tampered_self_review_fails(repo_20):
    """§69：篡改 self-review -> verifier FAIL。"""
    record = _run_verified(repo_20, seed=2)
    matrix = _matrix(repo_20, record["assignment_run_id"])
    matrix[3][3] = 1
    snapshot = json.loads(
        (repo_20 / "assignments" / record["assignment_run_id"] / "input_snapshot.json").read_text()
    )
    case = {"case_id": "x", "num_students": 20, "num_tasks": 20, "num_categories": 14,
            "category_vector": snapshot["category_vector"]}
    result = verify_assignment(case, matrix, 5)
    assert result["valid"] is False
    assert result["self_review_count"] == 1


def test_tampered_duplicate_pair_fails(repo_20):
    """§70：重复 reviewer-task 对（非 binary）-> verifier FAIL。"""
    record = _run_verified(repo_20, seed=2)
    matrix = _matrix(repo_20, record["assignment_run_id"])
    matrix[0][1] = 2
    snapshot = json.loads(
        (repo_20 / "assignments" / record["assignment_run_id"] / "input_snapshot.json").read_text()
    )
    case = {"case_id": "x", "num_students": 20, "num_tasks": 20, "num_categories": 14,
            "category_vector": snapshot["category_vector"]}
    result = verify_assignment(case, matrix, 5)
    assert result["valid"] is False
    assert any("NON_BINARY" in e for e in result["errors"])


def test_tampered_wrong_coverage_fails(repo_20):
    """§71：删一个 assignment -> coverage=4 -> verifier FAIL。"""
    record = _run_verified(repo_20, seed=2)
    matrix = _matrix(repo_20, record["assignment_run_id"])
    for i in range(20):
        matrix[i][7] = 0
    snapshot = json.loads(
        (repo_20 / "assignments" / record["assignment_run_id"] / "input_snapshot.json").read_text()
    )
    case = {"case_id": "x", "num_students": 20, "num_tasks": 20, "num_categories": 14,
            "category_vector": snapshot["category_vector"]}
    result = verify_assignment(case, matrix, 5)
    assert result["valid"] is False
    assert result["min_task_coverage"] < 5
    assert any("TASK_COVERAGE" in e for e in result["errors"])


def test_verification_failure_blocks_distribution(repo_20):
    """§48：Verifier FAIL 的 run 不允许分发。"""
    record = _run_verified(repo_20, seed=3)
    run_id = record["assignment_run_id"]
    matrix = _matrix(repo_20, run_id)
    matrix[0][0] = 1  # 篡改：self-review
    np.save(repo_20 / "assignments" / run_id / "assignment.npy", np.asarray(matrix, dtype=np.uint8))
    result = run_mod.distribute_assignment_run(repo_20, run_id)
    assert result["status"] == "VERIFICATION_FAILED"
    # 恢复：用同 seed 重新求解写回（保持 repo 干净）
    from codesetarena.assignment import solver

    snapshot = json.loads(
        (repo_20 / "assignments" / run_id / "input_snapshot.json").read_text(encoding="utf-8")
    )
    fresh = solver.solve_assignment(snapshot["category_vector"], 5, 3, {})
    np.save(repo_20 / "assignments" / run_id / "assignment.npy",
            np.asarray(fresh.matrix, dtype=np.uint8))


def test_invalid_category_fails_input(repo_20):
    """§72：canonical metadata 里非法 category -> input builder FAIL，solver 不启动。"""
    reg = SubmissionRegistry(repo_20)
    task_id = reg.rows[0]["task_id"]
    metadata_path = repo_20 / "tasks" / task_id / "metadata.json"
    original = metadata_path.read_text()
    metadata = json.loads(original)
    metadata["failure_mode"] = "FM99_BOGUS"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False) + "\n", encoding="utf-8")
    from codesetarena.assignment.contract import AssignmentError

    with pytest.raises(AssignmentError) as exc_info:
        ib.build_assignment_input(repo_20, PHASE)
    assert "ASSIGN_INPUT_INVALID_CATEGORY" in str(exc_info.value)
    metadata_path.write_text(original, encoding="utf-8")


def test_missing_category_fails_input(repo_20):
    """§73：canonical metadata 无 category -> FAIL，不猜不补。"""
    reg = SubmissionRegistry(repo_20)
    task_id = reg.rows[0]["task_id"]
    metadata_path = repo_20 / "tasks" / task_id / "metadata.json"
    original = metadata_path.read_text()
    metadata = json.loads(original)
    metadata["failure_mode"] = ""
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False) + "\n", encoding="utf-8")
    from codesetarena.assignment.contract import AssignmentError

    with pytest.raises(AssignmentError) as exc_info:
        ib.build_assignment_input(repo_20, PHASE)
    assert "ASSIGN_INPUT_MISSING_CATEGORY" in str(exc_info.value)
    metadata_path.write_text(original, encoding="utf-8")


def test_anonymous_leakage_zero_hits(run_200):
    """§74：全部 student id 在 distribution 中 0 命中。"""
    repo_200, run_id = run_200
    reg = SubmissionRegistry(repo_200)
    dist_root = repo_200 / "distributions" / run_id
    leaks = dist_mod.scan_distribution_leaks(
        dist_root, dist_mod.distribution_secret_tokens(repo_200, reg)
    )
    assert leaks == []


def test_source_and_canonical_id_leakage(run_200):
    """§75/§76：source_task_id 与 canonical task_id 不在匿名包中。"""
    repo_200, run_id = run_200
    reg = SubmissionRegistry(repo_200)
    dist_root = repo_200 / "distributions" / run_id
    tokens = dist_mod.distribution_secret_tokens(repo_200, reg)
    assert any(t.startswith("T_") for t in tokens)  # canonical task ids 被扫描
    assert dist_mod.scan_distribution_leaks(dist_root, tokens) == []
    target = dist_root / "reviewers" / "R000001" / "tasks"
    (target / "leak.txt").write_text("TEST_STUDENT_001_MY_TASK\n", encoding="utf-8")
    try:
        assert dist_mod.scan_distribution_leaks(dist_root, tokens)
    finally:
        (target / "leak.txt").unlink()


def test_private_content_leak_scan(run_200):
    """§40：gold / reference / private metadata 不出现在分布。"""
    repo_200, run_id = run_200
    dist_root = repo_200 / "distributions" / run_id
    hits = dist_mod.scan_private_content_leaks(dist_root)
    assert hits == []


def test_reproducibility_and_different_seed(repo_20):
    """§80/§81：同 seed 同矩阵；不同 seed 都合法（不要求不同）。"""
    a = _run_verified(repo_20, seed=11)
    b = _run_verified(repo_20, seed=11)
    assert a["assignment_run_id"] == b["assignment_run_id"]  # 幂等复用
    assert _matrix(repo_20, a["assignment_run_id"]) == _matrix(repo_20, b["assignment_run_id"])
    c = run_mod.build_assignment_run(repo_20, PHASE, seed=99)
    assert c["status"] == "VERIFIED"
    d = run_mod.build_assignment_run(repo_20, PHASE, seed=100)
    assert d["status"] == "VERIFIED"


def test_idempotency_marks_run_exists(repo_20):
    record = _run_verified(repo_20, seed=21)
    again = run_mod.build_assignment_run(repo_20, PHASE, seed=21)
    assert again["assignment_run_id"] == record["assignment_run_id"]
    assert again.get("reused") is True


def test_reviewer_archive_export_and_validation(repo_20):
    """§58/§59：per-reviewer tar.gz 结构 / 数量 / 无身份 token。"""
    record = _run_verified(repo_20, seed=31)
    run_id = record["assignment_run_id"]
    run_mod.distribute_assignment_run(repo_20, run_id)
    reg = SubmissionRegistry(repo_20)
    tokens = dist_mod.distribution_secret_tokens(repo_20, reg)
    exported = dist_mod.export_reviewer_archives(repo_20, run_id)
    assert len(exported) == 20
    for archive in exported:
        result = dist_mod.validate_reviewer_archive(Path(archive), 10, secret_tokens=tokens)
        assert result["valid"], result["errors"]
        assert result["task_count"] == 10


def test_assignment_pipeline_reads_only_canonical(repo_20):
    """Assignment 输入只来自 canonical repo + registry：篡改 submitted 原件不影响。"""
    before = run_mod.list_runs(repo_20)
    record = _run_verified(repo_20, seed=41)
    assert record["status"] == "VERIFIED"
    # pipeline 未触碰 received/ tasks/ agents/（artifact 只写入 assignments/）
    assert len(before) == len(run_mod.list_runs(repo_20)) - 1
    run_dir = repo_20 / "assignments" / record["assignment_run_id"]
    assert (run_dir / "assignment.npy").is_file()
    assert (run_dir / "input_snapshot.json").is_file()
    assert (run_dir / "category_vector.json").is_file()
    assert (run_dir / "verifier.json").is_file()
    assert (run_dir / "audit.json").is_file()