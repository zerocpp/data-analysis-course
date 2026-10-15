"""Phase F —— Phase1 Final Acceptance + Phase2 Release Candidate 测试（§76–§79）。

- mini cohort（20 人，synthetic）走**真实**链路：archive -> batch -> ingest ->
  canonical -> assignment build（冻结）-> run_phase_f；
- Gate A/B 全部断言 + §78 失败注入（删任务 / own task / 匿名映射篡改 /
  canonical id 注入 / student id 注入 / .env / reference.py / exposure 破坏）；
- 真·200 人工作区由 ``scripts/phase2_release_candidate.py`` 驱动真实执行；
  pytest 层通过环境变量 ``CSA_PHASE_F_REAL_WORKSPACE`` 可选开启。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tarfile
import tempfile
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import pytest

sys_path = __import__("sys").path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys_path:
    sys_path.insert(0, str(ROOT))

from codesetarena.assignment import distribution as dist_mod  # noqa: E402
from codesetarena.assignment import run as assign_run  # noqa: E402
from codesetarena.release import (  # noqa: E402
    assignment_gate,
    candidate as rc_mod,
    final_validation,
    inventory as inv_mod,
    leakage,
    lineage,
    orchestrator,
)
from codesetarena.release.contract import (  # noqa: E402
    ASSIGNMENT_ARTIFACT_DRIFT,
    ASSIGNMENT_INPUT_DRIFT,
    CANONICAL_DRIFT,
    PhaseFError,
)
from codesetarena.submission.archive import create_submission_archive  # noqa: E402
from codesetarena.submission.ingest import ingest_submissions  # noqa: E402
from codesetarena.submission.registry import SubmissionRegistry  # noqa: E402

N = 20
R = 10
PHASE = "phase1"
FMS = [
    "FM01_REQUIREMENT", "FM02_SCHEMA", "FM03_DATA_TYPE", "FM04_FILTER_SORT",
    "FM05_JOIN", "FM06_AGGREGATION", "FM07_TIME_TIMEZONE", "FM08_MISSING_VALUE",
    "FM09_OUTLIER_BOUNDARY", "FM10_DUPLICATE_RECORD_HANDLING",
    "FM11_PLANNING_DECOMPOSITION", "FM12_VERIFICATION",
    "FM13_NUMERICAL_PRECISION_ROUNDING", "FM14_PROMPT_INJECTION",
]


def _sid(i: int) -> str:
    return f"99010{i:03d}"


def _tid(i: int) -> str:
    return "T_" + f"{i:030d}"


def _make_tree(root: Path, sid: str, task_id: str, fm: str) -> Path:
    (root / "task/data").mkdir(parents=True)
    (root / "agent").mkdir(parents=True)
    (root / "manifest.json").write_text(
        json.dumps({"schema_version": "codesetarena.submission.v1", "course": "DataC1004",
                    "submission_phase": PHASE, "student_id": sid, "task_id": task_id,
                    "build_status": "passed", "files": {}}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (root / "task/task.json").write_text(
        json.dumps({"task_id": task_id, "student_id": sid,
                    "hypothesized_failure_mode": fm,
                    "task_statement": "synthetic statement"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (root / "task/data/data.csv").write_text("region,amount\neast,10\nwest,20\n",
                                             encoding="utf-8")
    (root / "agent/agent.py").write_text(
        "def run(task, df, llm):\n    return str(int(df['amount'].sum()))\n",
        encoding="utf-8",
    )
    (root / "agent/agent_manifest.json").write_text(
        json.dumps({"schema_version": "codesetarena.submission.v1", "student_id": sid,
                    "task_id": task_id, "entrypoint": "agent.py"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (root / "task/reference.py").write_text("def reference(df):\n    return '1'\n",
                                            encoding="utf-8")
    return root


def _extract_archive(archive: Path, target: Path) -> Path:
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(target, filter="data")
    return target


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture(scope="module")
def cohort(tmp_path_factory):
    """20 人 Phase E.1 形态工作区：submissions/ + repository/ + 冻结 assignment。"""
    workspace = tmp_path_factory.mktemp("phase_f_ws") / "cohort"
    submissions = workspace / "submissions"
    submissions.mkdir(parents=True)
    build_root = workspace / "_build"
    build_root.mkdir()
    for i in range(1, N + 1):
        tree = _make_tree(build_root / f"tree_{i}", _sid(i), _tid(i), FMS[(i - 1) % 14])
        create_submission_archive(tree, submissions / f"DataC1004_P1_{_sid(i)}.tar.gz")
    repo = workspace / "repository"
    report = ingest_submissions(repo, submissions)
    assert report["status"] == "PASS" and report["summary"]["ingested"] == N
    record = assign_run.build_assignment_run(repo, PHASE, seed=7)
    assert record["status"] == "VERIFIED", record
    return {
        "workspace": workspace,
        "submissions": submissions,
        "repo": repo,
        "run_id": record["assignment_run_id"],
        "matrix_sha256": assignment_gate.sha256_file(
            repo / "assignments" / record["assignment_run_id"] / "assignment.npy"),
    }


@pytest.fixture(scope="module")
def phase_f(cohort):
    """完整 Phase F 一次（20 人），供多个断言复用。"""
    result = orchestrator.run_phase_f(
        cohort["workspace"],
        assignment_run_id=cohort["run_id"],
        expected_archive_count=N,
        reviews_per_task=R,
        expected_matrix_sha256=cohort["matrix_sha256"],
        deterministic_sample=4,
        spot_check_count=6,
    )
    return cohort, result


def _rc_dir(cohort) -> Path:
    return cohort["workspace"] / "release_candidates" / cohort["run_id"]


def _rc_archives(cohort) -> list[Path]:
    return sorted((_rc_dir(cohort) / "packages").glob("*.tar.gz"))


def _snapshot_of(cohort) -> dict:
    return json.loads(
        (cohort["repo"] / "assignments" / cohort["run_id"] / "input_snapshot.json")
        .read_text(encoding="utf-8")
    )


def _private_mapping_for(cohort, archives) -> dict:
    snapshot = _snapshot_of(cohort)
    return {"reviewers": [{
        "reviewer_package_id": rc_mod._package_id_of(a.name),
        "reviewer_student_id": snapshot["reviewer_order"][
            int(rc_mod._package_id_of(a.name)[1:]) - 1],
        "anonymous_task_ids": [],
    } for a in archives]}


def _fill_assigned_tasks(cohort, private_mapping: dict) -> None:
    for reviewer in private_mapping["reviewers"]:
        archive = (_rc_dir(cohort) / "packages" /
                   dist_mod.reviewer_archive_name(reviewer["reviewer_package_id"]))
        with tarfile.open(archive, "r:gz") as tar:
            manifest = json.loads(
                tar.extractfile("assignment.json").read().decode("utf-8"))
        reviewer["anonymous_task_ids"] = manifest["anonymous_tasks"]


# ---------------------------------------------------------------------------
# §77：Gate A
# ---------------------------------------------------------------------------


def test_final_phase1_archive_count_200(cohort):
    rows = inv_mod.scan_archive_inventory(cohort["submissions"])
    assert len(rows) == N
    inv_mod.require_archive_count(rows, N)
    with pytest.raises(PhaseFError) as exc:
        inv_mod.require_archive_count(rows, N + 1)
    assert exc.value.gate == "G01"


def test_final_phase1_all_validate(phase_f):
    _, result = phase_f
    assert result["phase1"]["single_pass"] == N
    assert result["phase1"]["single_fail"] == 0


def test_final_phase1_batch_pass(phase_f):
    _, result = phase_f
    assert result["phase1"]["batch_status"] == "PASS"
    assert result["phase1"]["batch_conflicts"] == 0
    assert result["phase1"]["final_accepted"] == N


def test_phase1_archive_hash_immutable(cohort, phase_f):
    """§4：Phase F 前后 SHA256 完全一致；任何改动可被检出（篡改只在副本上做）。"""
    _, result = phase_f
    assert result["phase1"]["archive_mutations"] == 0
    rows = inv_mod.scan_archive_inventory(cohort["submissions"])
    frozen_file = (cohort["workspace"] / "final_acceptance" /
                   "phase1_archive_inventory.jsonl")
    frozen = {row["archive_filename"]: row["sha256"]
              for row in _load_jsonl(frozen_file)}
    assert frozen == inv_mod.freeze_hashes(rows)
    before = inv_mod.freeze_hashes(rows)
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / rows[0]["archive_filename"]
        shutil.copyfile(rows[0]["path"], copy)
        copy.write_bytes(b"tampered")
        after = dict(before)
        after[rows[0]["archive_filename"]] = _sha256(copy)
        assert inv_mod.detect_mutations(before, after) == [copy.name]


def test_canonical_provenance_matches(cohort, phase_f):
    """§15/§16/§17：对账 PASS；canonical 缺失 -> CANONICAL_DRIFT（不自动修复）。"""
    _, result = phase_f
    assert result["phase1"]["canonical_provenance"] == "PASS"
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "repo"
        shutil.copytree(cohort["repo"], copy)
        shutil.rmtree(sorted((copy / "tasks").iterdir())[0])
        registry = SubmissionRegistry(copy)
        accepted = [{
            "archive_filename": "x.tar.gz",
            "archive_sha256": registry.rows[0]["archive_sha256"],
            "student_id": registry.rows[0]["student_id"],
        }]
        with pytest.raises(PhaseFError) as exc:
            final_validation.canonical_provenance(copy, accepted)
        assert exc.value.code == CANONICAL_DRIFT


# ---------------------------------------------------------------------------
# §77：Assignment Gate
# ---------------------------------------------------------------------------


def test_assignment_reverify_pass(cohort):
    result = assignment_gate.reverify_assignment_run(
        cohort["repo"], cohort["run_id"],
        expected_matrix_sha256=cohort["matrix_sha256"],
    )
    assert result["status"] == "PASS"
    verifier = result["verifier"]
    assert verifier["matrix_shape"] == [N, N]
    assert verifier["total_assignments"] == N * R
    assert verifier["min_reviewer_load"] == verifier["max_reviewer_load"] == R
    assert verifier["min_task_coverage"] == verifier["max_task_coverage"] == R
    assert verifier["self_review_count"] == 0
    assert verifier["duplicate_pair_count"] == 0


def test_assignment_artifact_hash_stable(cohort):
    with pytest.raises(PhaseFError) as exc:
        assignment_gate.reverify_assignment_run(
            cohort["repo"], cohort["run_id"], expected_matrix_sha256="0" * 64
        )
    assert exc.value.code == ASSIGNMENT_ARTIFACT_DRIFT


def test_assignment_snapshot_consistency_drift(cohort):
    """§21：canonical FM 漂移（metadata + task.json 同步改）-> ASSIGNMENT_INPUT_DRIFT。"""
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "repo"
        shutil.copytree(cohort["repo"], copy)
        victim = sorted((copy / "tasks").iterdir())[0]
        metadata = json.loads((victim / "metadata.json").read_text(encoding="utf-8"))
        new_fm = FMS[(FMS.index(metadata["failure_mode"]) + 1) % 14]
        metadata["failure_mode"] = new_fm
        (victim / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        task_json_path = victim / "task" / "task.json"
        task_json = json.loads(task_json_path.read_text(encoding="utf-8"))
        task_json["hypothesized_failure_mode"] = new_fm
        task_json_path.write_text(
            json.dumps(task_json, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        with pytest.raises(PhaseFError) as exc:
            assignment_gate.check_input_snapshot_consistency(copy, cohort["run_id"])
        assert exc.value.code == ASSIGNMENT_INPUT_DRIFT


# ---------------------------------------------------------------------------
# §77：Gate B —— RC 构建 / 校验 / No-Self / Exposure / 重建
# ---------------------------------------------------------------------------


def test_build_200_rc_packages(cohort, phase_f):
    _, result = phase_f
    assert len(_rc_archives(cohort)) == N
    assert result["phase2_rc"]["rc_packages"] == N


def test_each_rc_has_5_tasks(phase_f):
    """§31/§44/§46：每包 5 个唯一 AT，5 个唯一 canonical。"""
    _, result = phase_f
    assert result["phase2_rc"]["tasks_per_package_min"] == R
    assert result["phase2_rc"]["tasks_per_package_max"] == R
    assert result["phase2_rc"]["duplicate_assignments"] == 0


def test_no_self_after_anonymous_mapping(phase_f):
    """§45：Registry 权威 owner 逐包复查。"""
    _, result = phase_f
    assert result["phase2_rc"]["self_review"] == 0


def test_each_task_exposure_5(phase_f):
    """§47：每个 canonical task 恰好出现在 R 个包。"""
    _, result = phase_f
    assert result["phase2_rc"]["task_exposure_min"] == R
    assert result["phase2_rc"]["task_exposure_max"] == R
    assert result["phase2_rc"]["total_task_exposures"] == N * R


def test_reverse_reconstruction_exact(cohort, phase_f):
    """§48/§49：从最终包重建 == assignment.npy（bit-exact）。"""
    _, result = phase_f
    assert result["phase2_rc"]["reconstruction_diff"] == 0
    reconstruction = json.loads(
        (_rc_dir(cohort) / "reports" / "phase2_rc_reconstruction_report.json")
        .read_text(encoding="utf-8"))
    assert reconstruction["bit_exact"] is True
    assert reconstruction["difference_count"] == 0
    assert reconstruction["packages_checked"] == N


# ---------------------------------------------------------------------------
# §78：失败注入
# ---------------------------------------------------------------------------


def test_injection_missing_task_breaks_reconstruction(cohort):
    """§78：删掉一个 RC Task -> reconstruction FAIL（漏发必被检出）。"""
    archive = _rc_archives(cohort)[0]
    registry = SubmissionRegistry(cohort["repo"])
    run_dir = cohort["repo"] / "assignments" / cohort["run_id"]
    snapshot = _snapshot_of(cohort)
    package_id = rc_mod._package_id_of(archive.name)
    private_mapping = _private_mapping_for(cohort, _rc_archives(cohort))
    _fill_assigned_tasks(cohort, private_mapping)
    with tempfile.TemporaryDirectory() as tmp:
        pkg = _extract_archive(archive, Path(tmp) / package_id)
        victim = sorted((pkg / "tasks").iterdir())[0]
        shutil.rmtree(victim)
        manifest_path = pkg / "assignment.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["anonymous_tasks"] = [a for a in manifest["anonymous_tasks"]
                                       if a != victim.name]
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for reviewer in private_mapping["reviewers"]:
            if reviewer["reviewer_package_id"] == package_id:
                reviewer["anonymous_task_ids"] = manifest["anonymous_tasks"]
        broken = dist_mod.build_reviewer_archive(
            pkg, Path(tmp) / dist_mod.reviewer_archive_name(package_id))
        others = [a for a in _rc_archives(cohort) if a.name != archive.name]
        rebuilt = rc_mod.reconstruct_matrix_from_archives(
            [*others, broken], private_mapping=private_mapping, registry=registry,
            snapshot=snapshot, r=R,
        )
        assert rebuilt["errors"] == []
        comparison = rc_mod.compare_matrix(rebuilt["matrix"], run_dir / "assignment.npy")
        assert comparison["bit_exact"] is False
        assert comparison["difference_count"] == 1


def test_injection_own_task_breaks_no_self(cohort):
    """§78：把 reviewer own task 放进包 -> no-self FAIL。"""
    registry = SubmissionRegistry(cohort["repo"])
    snapshot = _snapshot_of(cohort)
    student = snapshot["reviewer_order"][0]
    own_task = next(t["task_id"] for t in snapshot["tasks"]
                    if t["owner_student_id"] == student)
    at_of_own = registry.find_anonymous_task(own_task)["anonymous_task_id"]
    packages = [{
        "reviewer_package_id": "R000001",
        "reviewer_student_id": student,
        "canonical_task_ids": [own_task],
        "anonymous_task_ids": [at_of_own],
    }]
    rows, problems = rc_mod.package_no_self(packages, registry, snapshot)
    assert rows[0]["self_review"] is True
    assert problems


def test_injection_mapping_swap_breaks_reconstruction(cohort):
    """§78：篡改 anonymous mapping -> reconstruction FAIL。"""
    registry = SubmissionRegistry(cohort["repo"])
    run_dir = cohort["repo"] / "assignments" / cohort["run_id"]
    snapshot = _snapshot_of(cohort)
    archives = _rc_archives(cohort)
    private_mapping = _private_mapping_for(cohort, archives)
    _fill_assigned_tasks(cohort, private_mapping)
    matrix = np.load(run_dir / "assignment.npy")
    column_a, column_b = next(
        (j1, j2)
        for j1 in range(len(snapshot["task_order"]))
        for j2 in range(j1 + 1, len(snapshot["task_order"]))
        if not np.array_equal(matrix[:, j1], matrix[:, j2])
    )
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "repo"
        shutil.copytree(cohort["repo"], copy)
        mapping_path = copy / "registry" / "anonymous_mapping.jsonl"
        rows = _load_jsonl(mapping_path)
        at_a = registry.find_anonymous_task(snapshot["task_order"][column_a])
        at_b = registry.find_anonymous_task(snapshot["task_order"][column_b])
        for row in rows:
            if row.get("anonymous_task_id") == at_a["anonymous_task_id"]:
                row["anonymous_task_id"] = at_b["anonymous_task_id"]
            elif row.get("anonymous_task_id") == at_b["anonymous_task_id"]:
                row["anonymous_task_id"] = at_a["anonymous_task_id"]
        mapping_path.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
            encoding="utf-8")
        swapped = rc_mod.reconstruct_matrix_from_archives(
            archives, private_mapping=private_mapping,
            registry=SubmissionRegistry(copy), snapshot=snapshot, r=R,
        )
        comparison = rc_mod.compare_matrix(swapped["matrix"], run_dir / "assignment.npy")
        assert comparison["bit_exact"] is False
        assert comparison["difference_count"] > 0


@contextmanager
def _leak_dir():
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


def test_injection_identity_private_secret_path(cohort):
    """§78：canonical id / student id / reference.py / .env / 本地路径注入必命中。"""
    registry = SubmissionRegistry(cohort["repo"])
    row = registry.rows[0]
    tokens = leakage.build_identity_tokens(
        registry.rows,
        extra_tokens=["workspace/manual", "manual/DA01_T1_A"],
    )
    with _leak_dir() as leaked:
        (leaked / "identity.txt").write_text(
            f"owner {row['student_id']} {row['task_id']} {row['agent_id']} "
            f"{row['source_task_id']}\n", encoding="utf-8")
        (leaked / "answers.json").write_text(
            json.dumps({"declared_goal_answer": "gold-value-1",
                        "gold_answer": "gold-value-1"}) + "\n", encoding="utf-8")
        (leaked / "creds.env").write_text("LLM_API_KEY=sk-abcdef1234567890abc\n",
                                          encoding="utf-8")
        (leaked / "path.txt").write_text(r"D:\Ubuntu\DUT_IR\manual\n", encoding="utf-8")
        identity = leakage.scan_identity(leaked, tokens)
        surfaces = {hit["token"] for hit in identity}
        assert {row["student_id"], row["task_id"], row["agent_id"],
                row["source_task_id"]} <= surfaces
        private = leakage.scan_private_content(leaked)
        assert {hit["token"] for hit in private} >= {"declared_goal_answer",
                                                     "gold_answer"}
        secrets = leakage.scan_secrets(leaked)
        assert any(hit["kind"] in ("sk_style_api_key", "api_key_assignment")
                   for hit in secrets)
        local = leakage.scan_local_paths(leaked)
        assert any(hit["kind"] == "windows_drive" for hit in local)


def test_injection_forbidden_files_fail_package_validation(cohort):
    """§8/§38/§39/§78：.env / agent.py / reference.py 进 RC 包 -> 单包校验 FAIL。"""
    archive = _rc_archives(cohort)[0]
    with tempfile.TemporaryDirectory() as tmp:
        pkg = _extract_archive(archive, Path(tmp) / "pkg")
        (pkg / ".env").write_text("LLM_API_KEY=leak\n", encoding="utf-8")
        (pkg / "agent.py").write_text("def run():\n    return 'author agent'\n",
                                      encoding="utf-8")
        (pkg / "reference.py").write_text("def reference(df):\n    return 'gold'\n",
                                          encoding="utf-8")
        poisoned = dist_mod.build_reviewer_archive(pkg, Path(tmp) / "poisoned.tar.gz")
        result = rc_mod.validate_rc_package(poisoned, R)
        assert result["valid"] is False
        assert "DIST_FORBIDDEN_FILE" in " ".join(result["errors"])


def test_injection_extra_exposure_breaks_reconstruction(cohort):
    """§78：额外多一处 exposure -> 与冻结矩阵 diff = 1。"""
    registry = SubmissionRegistry(cohort["repo"])
    snapshot = _snapshot_of(cohort)
    archives = _rc_archives(cohort)
    private_mapping = _private_mapping_for(cohort, archives)
    _fill_assigned_tasks(cohort, private_mapping)
    actual = np.asarray(rc_mod.reconstruct_matrix_from_archives(
        archives, private_mapping=private_mapping, registry=registry,
        snapshot=snapshot, r=R)["matrix"], dtype=np.uint8)
    expected = actual.copy()
    expected[0, 1] = 1
    assert int(np.count_nonzero(expected != actual)) == 1


# ---------------------------------------------------------------------------
# §62/§80/§82：边界 + lineage + 确定性
# ---------------------------------------------------------------------------


def test_no_formal_distribution_created(cohort, phase_f):
    """§62：正式 distributions/ 不新增 reviewer 分发产物。"""
    _, result = phase_f
    assert list((cohort["repo"] / "distributions").rglob("reviewers")) == []
    assert list((cohort["repo"] / "distributions")
                .rglob("review_package_*.tar.gz")) == []
    assert result["phase2_rc"]["formal_distribution_created"] == 0


def test_no_release_action(cohort, phase_f):
    """§61/§64：run 状态不得被推进为 READY；终态 AWAITING_EXPLICIT_RELEASE。"""
    _, result = phase_f
    run = json.loads(
        (cohort["repo"] / "assignments" / cohort["run_id"] / "run.json")
        .read_text(encoding="utf-8"))
    assert run["status"] == "VERIFIED"
    assert "distribution_status" not in run
    rc = result["phase2_rc"]
    assert rc["release_status"] == "NOT_RELEASED"
    assert rc["next_action"] == "AWAITING_EXPLICIT_RELEASE"
    assert rc["packages_released"] == 0
    assert rc["phase2_rc_status"] == "RELEASE_CANDIDATE_VERIFIED"


def test_rc_set_hash_and_lineage(cohort, phase_f):
    """§57/§58/§80/§82/§83：public/private manifest 分离 + lineage 链完整。"""
    cohort_data, result = phase_f
    manifests = _rc_dir(cohort_data) / "manifests"
    public = _load_jsonl(manifests / "phase2_rc_public_manifest.jsonl")
    private = _load_jsonl(manifests / "phase2_rc_private_manifest.jsonl")
    assert len(public) == len(private) == N
    assert all("reviewer_student_id" not in row for row in public)
    assert all(row["reviewer_student_id"] for row in private)
    lineage_payload = result["lineage"]
    for key in ("phase1_submission_set_sha256", "canonical_repository_snapshot_sha256",
                "assignment_run_id", "assignment_input_snapshot_sha256",
                "assignment_matrix_sha256", "anonymous_mapping_sha256",
                "phase2_release_candidate_set_sha256"):
        assert lineage_payload[key], key
    records = _load_jsonl(manifests / "phase2_release_candidate_manifest.jsonl")
    assert lineage.rc_set_sha256(records) == \
        lineage_payload["phase2_release_candidate_set_sha256"]


def test_deterministic_rebuild_same_bytes(cohort, phase_f):
    """§59/§60：同一 package 重建字节一致（verified bytes == released bytes）。"""
    archive = _rc_archives(cohort)[0]
    with tempfile.TemporaryDirectory() as tmp:
        pkg = _extract_archive(archive, Path(tmp) / "pkg")
        rebuilt = dist_mod.build_reviewer_archive(pkg, Path(tmp) / "again.tar.gz")
        assert _sha256(rebuilt) == _sha256(archive)


def test_release_gate_all_pass(phase_f):
    """§70/§71：16 个 Gate 全 PASS 且写入 release_gate.json。"""
    cohort_data, result = phase_f
    gate_report = json.loads(
        (_rc_dir(cohort_data) / "reports" / "release_gate.json").read_text(encoding="utf-8"))
    assert [g["gate"] for g in gate_report["gates"]] == [
        f"G{i:02d}" for i in range(1, 17)]
    assert all(g["status"] == "PASS" for g in gate_report["gates"])


# ---------------------------------------------------------------------------
# 真·200 人工作区（存在时执行；常规真实运行由 scripts/phase2_release_candidate.py 驱动）
# ---------------------------------------------------------------------------

REAL_WORKSPACE = Path(
    os.environ.get("CSA_PHASE_F_REAL_WORKSPACE",
                   str(ROOT / "workspace" / "cohort_dryrun_200"))
)


@pytest.mark.skipif(
    not (REAL_WORKSPACE / "submissions").is_dir()
    or not (REAL_WORKSPACE / "repository" / "assignments" / "AR000001").is_dir(),
    reason="真实 200 人工作区不存在（由脚本执行，不在单测中强制）",
)
def test_real_workspace_phase_f_full_gate():
    result = orchestrator.run_phase_f(
        REAL_WORKSPACE,
        assignment_run_id="AR000001",
        expected_archive_count=200,
        reviews_per_task=10,
    )
    assert result["phase1"]["final_accepted"] == 200
    assert result["phase2_rc"]["rc_packages"] == 200
    assert result["phase2_rc"]["reconstruction_diff"] == 0
    assert result["phase2_rc"]["phase2_rc_status"] == "RELEASE_CANDIDATE_VERIFIED"
