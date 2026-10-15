"""Submission Pipeline Tests —— tar.gz 正式格式（§8/§41）+ Batch Corpus（§42）+
Ingest Corpus（§43）+ 匿名泄露（§44）。

全部在 tmp_path 内完成：不依赖外部 corpus、不调用 LLM、不启动 subprocess。
证明同一份 Single-Package Validator 在 BUILD / VALIDATE / BATCH-VALIDATE 三端
复用（v2 §6/§13/§40/§41）与 INGEST 的 Task/Agent 拆分 + 私有映射（§18–§36）。
"""
from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest

from codesetarena.submission import contract as C
from codesetarena.submission.anonymization import scan_for_identity_leaks
from codesetarena.submission.archive import (
    create_submission_archive,
    validate_submission_archive,
)
from codesetarena.submission.batch.validator import validate_batch
from codesetarena.submission.ingest import ingest_submissions
from codesetarena.submission.local import build_submission
from codesetarena.submission.registry import SubmissionRegistry
from codesetarena.submission.safe_extract import safe_extract_archive
from tests.test_submission_conformance import (
    STUDENT,
    TASK_ID,
    make_submission_tree,
    make_workspace,
)

OTHER_STUDENT = "273002"
THIRD_STUDENT = "273003"
PROTO_TAR = f"DataC1004_P1_{STUDENT}.tar.gz"


def pack_targz(tree: Path, out: Path) -> Path:
    return create_submission_archive(tree, out)


def _roster(tmp_path: Path, *students: str) -> Path:
    roster = tmp_path / "roster.csv"
    roster.write_text("student_id\n" + "\n".join(students) + "\n", encoding="utf-8")
    return roster


# ---------------------------------------------------------------------------
# §49 A/B/C/D + §41：正式 tar.gz 的 Build / Validate / Tamper
# ---------------------------------------------------------------------------


def test_formal_build_tar_gz_and_validate(tmp_path):
    """Build（tar.gz）PASS -> validate（同一个 archive）PASS。"""

    workspace = make_workspace(tmp_path / "ws", student_id=STUDENT, task_id=TASK_ID)
    out = tmp_path / PROTO_TAR
    record = build_submission(
        workspace, phase="phase1", student_id=STUDENT, output_path=out,
        archive_format="tar.gz",
    )
    assert record["exit_code"] == 0
    assert record["local_validation"]["status"] == "PASS"
    assert record["archive_format"] == "tar.gz"
    assert out.is_file() and out.name == PROTO_TAR
    with tarfile.open(out, "r:gz") as bundle:
        names = bundle.getnames()
    assert "manifest.json" in names and "task/task.json" in names

    result = validate_submission_archive(out)
    assert result["status"] == "PASS"
    assert result["single_package_status"] == "PASS"


def test_build_validation_fail_does_not_create_archive(tmp_path):
    """Single-Package 校验 FAIL -> 不生成正式 tar.gz（§4/§49-B）。"""

    workspace = make_workspace(tmp_path / "ws", student_id=STUDENT, task_id=TASK_ID)
    out = tmp_path / "DataC1004_P1_273099.tar.gz"  # 学号与 workspace 内不一致
    record = build_submission(
        workspace, phase="phase1", student_id="273099", output_path=out,
        archive_format="tar.gz",
    )
    assert record["exit_code"] == 1
    assert record["local_validation"]["status"] == "FAIL"
    assert record["local_validation"]["error_code"] == C.STUDENT_ID_MISMATCH
    assert not out.exists(), "校验失败不得产生正式 archive"


def test_gateway_fail_no_archive(tmp_path):
    """workspace 网关失败（非法 FM）-> StagingError -> 无 archive（§49-B）。"""

    from codesetarena.submission.local import SubmissionBuildError

    workspace = make_workspace(tmp_path / "ws", student_id=STUDENT)
    (workspace / "task.json").write_text(
        json.dumps({"task_id": TASK_ID, "student_id": STUDENT,
                    "hypothesized_failure_mode": "FM99_NOPE",
                    "task_statement": "x"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    out = tmp_path / PROTO_TAR
    with pytest.raises(SubmissionBuildError):
        build_submission(workspace, phase="phase1", student_id=STUDENT,
                         output_path=out, archive_format="tar.gz")
    assert not out.exists()


def test_tampered_archive_fails_validate(tmp_path):
    """Build PASS 后手工删除 task/task.json -> validate 必须 FAIL（§41/§49-D）。"""

    workspace = make_workspace(tmp_path / "ws", student_id=STUDENT, task_id=TASK_ID)
    out = tmp_path / PROTO_TAR
    record = build_submission(workspace, phase="phase1", student_id=STUDENT,
                              output_path=out, archive_format="tar.gz")
    assert record["exit_code"] == 0

    extracted = tmp_path / "extract"
    safe_extract_archive(out, extracted)
    (extracted / "task/task.json").unlink()
    tampered = tmp_path / "tampered" / PROTO_TAR
    pack_targz(extracted, tampered)

    result = validate_submission_archive(tampered)
    assert result["status"] == "FAIL"
    assert result["error_code"] == C.MISSING_TASK_JSON


def test_tar_gz_deterministic(tmp_path):
    """同一棵树两次打包字节一致（确定性与审计可复现）。"""

    tree1 = make_submission_tree(tmp_path / "t1")
    a = pack_targz(tree1, tmp_path / "a.tar.gz")
    b = pack_targz(tree1, tmp_path / "b.tar.gz")
    assert a.read_bytes() == b.read_bytes()


# ---------------------------------------------------------------------------
# §8：tar.gz Archive Intake 安全
# ---------------------------------------------------------------------------


def _write_malicious_tar(members: list[tuple[str, bytes | None, int | None]], out: Path) -> Path:
    """手工构造恶意 tar 成员：(name, data, type_flag)。symlink: type=SYMTYPE。"""

    out.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(out, "w") as bundle:
        for name, data, type_flag in members:
            info = tarfile.TarInfo(name)
            if type_flag == tarfile.SYMTYPE:
                info.type = tarfile.SYMTYPE
                info.linkname = "agent/agent.py"
                info.size = 0
            else:
                info.size = len(data or b"")
            info.mtime = 0
            info.uid = info.gid = 0
            bundle.addfile(info, io_handle(data) if data is not None and type_flag != tarfile.SYMTYPE else None)
    return out


def io_handle(data: bytes):
    import io

    return io.BytesIO(data)


def test_tar_path_traversal_rejected(tmp_path):
    out = _write_malicious_tar([("../evil.txt", b"evil", tarfile.REGTYPE)], tmp_path / "evil.tar.gz")
    result = validate_submission_archive(out)
    assert result["status"] == "FAIL"
    assert result["error_code"] == C.TAR_PATH_TRAVERSAL


def test_tar_symlink_rejected(tmp_path):
    out = _write_malicious_tar([("agent/agent.py", b"x", tarfile.REGTYPE),
                                ("task/data/data.csv", None, tarfile.SYMTYPE)],
                               tmp_path / "symlink.tar.gz")
    result = validate_submission_archive(out)
    assert result["status"] == "FAIL"
    assert result["error_code"] == C.TAR_UNSAFE_LINK


def test_zip_still_supported_formal_validate(tmp_path):
    """legacy zip 仍可通过 validate 入口（fixture corpus 兼容；正式格式为 tar.gz）。"""

    from tests.test_submission_conformance import write_zip

    pkg = tmp_path / "DataC1004_P1_273001.zip"
    write_zip(make_submission_tree(tmp_path / "tree"), pkg)
    result = validate_submission_archive(pkg)
    assert result["status"] == "PASS"


# ---------------------------------------------------------------------------
# §42：Batch Corpus —— 单包错误与包间错误严格分离
# ---------------------------------------------------------------------------


def _phase2_extras(tree: Path) -> Path:
    """给提交树追加 Phase2 Extension（v10.1.14：assignment.json +
    review-comments.json + 实验报告.docx；旧 reviews/ report/ 已删除）。"""

    from bigdata_v10110_helpers import make_docx_bytes

    assigned = ["AT028", "AT035"]
    (tree / "assignment.json").write_text(
        json.dumps({"assignment_id": "AR000001_R000001", "phase": "phase2",
                    "anonymous_tasks": assigned}, ensure_ascii=False) + "\n",
        encoding="utf-8")
    (tree / "review-comments.json").write_text(
        json.dumps({at: {"task_id": at, "score": 4, "comments": "审稿记录。"}
                    for at in assigned}, ensure_ascii=False) + "\n",
        encoding="utf-8")
    (tree / "实验报告.docx").write_bytes(make_docx_bytes())
    return tree


def _batch_with(tmp_path: Path, pkgs: dict[str, tuple[str, str, str]]) -> dict:
    """{name: (student_id, task_id, phase)} -> 打包目录 + batch report。"""

    batch_in = tmp_path / "batch_in"
    for name, (sid, tid, phase) in pkgs.items():
        tree = make_submission_tree(tmp_path / f"t_{name}", student_id=sid, task_id=tid,
                                    phase=phase)
        if phase == "phase2":
            _phase2_extras(tree)
        pack_targz(tree, batch_in / name)
    return validate_batch(batch_in)


def test_batch_valid_two_clean(tmp_path):
    report = _batch_with(tmp_path, {
        f"DataC1004_P1_{STUDENT}.tar.gz": (STUDENT, "T_1", "phase1"),
        f"DataC1004_P1_{OTHER_STUDENT}.tar.gz": (OTHER_STUDENT, "T_2", "phase1"),
    })
    assert report["status"] == "PASS"
    assert all(p["final_status"] == "ACCEPTABLE" for p in report["packages"])


def test_batch_duplicate_task_id(tmp_path):
    report = _batch_with(tmp_path, {
        f"DataC1004_P1_{STUDENT}.tar.gz": (STUDENT, "T_1", "phase1"),
        f"DataC1004_P1_{OTHER_STUDENT}.tar.gz": (OTHER_STUDENT, "T_1", "phase1"),
    })
    codes = [e["code"] for e in report["errors"]]
    assert C.BATCH_TASK_ID_DUPLICATE in codes
    assert all(p["final_status"] == "REJECT" for p in report["packages"])
    assert all(p["batch_status"] == "FAIL" for p in report["packages"])


def test_batch_duplicate_student_id_per_phase(tmp_path):
    """同一学生同一阶段两个 active submission -> DUPLICATE_ACTIVE_STUDENT_ID。"""

    report = _batch_with(tmp_path, {
        f"DataC1004_P1_{STUDENT}.tar.gz": (STUDENT, "T_1", "phase1"),
        f"DataC1004_P1_{STUDENT}_v2.tar.gz": (STUDENT, "T_2", "phase1"),
    })
    codes = [e["code"] for e in report["errors"]]
    assert C.BATCH_STUDENT_ID_DUPLICATE in codes


def test_batch_mixed_phase_is_acceptable(tmp_path):
    """课程 Policy：唯一性 key 是 (student_id, phase)；同一学生 P1+P2 允许。"""

    report = _batch_with(tmp_path, {
        f"DataC1004_P1_{STUDENT}.tar.gz": (STUDENT, "T_1", "phase1"),
        f"DataC1004_P2_{STUDENT}.tar.gz": (STUDENT, "T_1", "phase2"),
    })
    assert report["status"] == "PASS"
    assert all(p["final_status"] == "ACCEPTABLE" for p in report["packages"])


def test_batch_valid_plus_invalid_separation(tmp_path):
    """单包 FAIL 与 Batch Conflict 分开记录（§15）。"""

    bad_tree = make_submission_tree(tmp_path / "bad", student_id=THIRD_STUDENT, task_id="T_9")
    (bad_tree / "agent/agent.py").unlink()
    batch_in = tmp_path / "batch_in"
    pack_targz(make_submission_tree(tmp_path / "a", student_id=STUDENT, task_id="T_1"),
               batch_in / f"DataC1004_P1_{STUDENT}.tar.gz")
    pack_targz(bad_tree, batch_in / f"DataC1004_P1_{THIRD_STUDENT}.tar.gz")

    report = validate_batch(batch_in)
    by_arch = {p["archive"]: p for p in report["packages"]}
    good = by_arch[f"DataC1004_P1_{STUDENT}.tar.gz"]
    bad = by_arch[f"DataC1004_P1_{THIRD_STUDENT}.tar.gz"]
    assert good["single_package_status"] == "PASS"
    assert good["batch_status"] == "PASS"
    assert good["final_status"] == "ACCEPTABLE"
    assert bad["single_package_status"] == "FAIL"
    assert bad["batch_status"] == "NOT_RUN"
    assert bad["final_status"] == "REJECT"


# ---------------------------------------------------------------------------
# §43：Ingest Corpus + §44：匿名泄露
# ---------------------------------------------------------------------------


def _ingest_batch(tmp_path: Path, pkgs: dict[str, tuple[str, str]]) -> tuple[Path, dict]:
    """打包目录（tar.gz）+ ingest 到独立 repo。"""

    batch_in = tmp_path / "batch_in"
    for name, (sid, tid) in pkgs.items():
        pack_targz(make_submission_tree(tmp_path / f"t_{name}", student_id=sid, task_id=tid),
                   batch_in / name)
    repo = tmp_path / "repo"
    return repo, ingest_submissions(repo, batch_in)


def test_ingest_single_valid(tmp_path):
    repo, report = _ingest_batch(tmp_path, {PROTO_TAR: (STUDENT, TASK_ID)})
    assert report["summary"]["ingested"] == 1
    assert report["summary"]["single_fail"] == 0

    row = SubmissionRegistry(repo).rows[0]
    assert row["student_id"] == STUDENT
    assert row["task_id"].startswith("T") and row["task_id"] != TASK_ID
    assert row["agent_id"].startswith("A")
    assert row["submission_id"].startswith("S")

    task_dir = repo / "tasks" / row["task_id"]
    agent_dir = repo / "agents" / row["agent_id"]
    assert task_dir.is_dir() and (task_dir / "task/task.json").is_file()
    assert (task_dir / "task/data/data.csv").is_file()
    assert (task_dir / "reference.py").is_file()  # canonical-private gold
    assert (task_dir / "metadata.json").is_file()
    assert (agent_dir / "agent.py").is_file()
    assert (agent_dir / "agent_manifest.json").exists() is False
    assert (repo / "received" / PROTO_TAR).is_file()


def test_ingest_multiple_independent_objects(tmp_path):
    repo, report = _ingest_batch(tmp_path, {
        f"DataC1004_P1_{STUDENT}.tar.gz": (STUDENT, "T_1"),
        f"DataC1004_P1_{OTHER_STUDENT}.tar.gz": (OTHER_STUDENT, "T_2"),
    })
    rows = SubmissionRegistry(repo).rows
    assert len(rows) == 2
    assert len([p for p in (repo / "tasks").iterdir() if p.is_dir()]) == 2
    assert len([p for p in (repo / "agents").iterdir() if p.is_dir()]) == 2
    assert rows[0]["task_id"] != rows[1]["task_id"]


def test_ingest_skips_single_fail_to_quarantine(tmp_path):
    bad_tree = make_submission_tree(tmp_path / "bad", student_id=STUDENT, task_id="T_9")
    (bad_tree / "agent/agent.py").unlink()
    batch_in = tmp_path / "batch_in"
    pack_targz(bad_tree, batch_in / PROTO_TAR)

    repo = tmp_path / "repo"
    report = ingest_submissions(repo, batch_in)
    assert report["summary"]["single_fail"] == 1
    assert report["summary"]["ingested"] == 0
    assert not list((repo / "tasks").iterdir())
    assert not list((repo / "agents").iterdir())
    assert (repo / "quarantine" / "single_validation_failed" / PROTO_TAR).is_file()


def test_ingest_skips_batch_conflict_to_quarantine(tmp_path):
    batch_in = tmp_path / "batch_in"
    for sid in (STUDENT, OTHER_STUDENT):
        pack_targz(make_submission_tree(tmp_path / f"t_{sid}", student_id=sid, task_id="T_DUP"),
                   batch_in / f"DataC1004_P1_{sid}.tar.gz")

    repo = tmp_path / "repo"
    report = ingest_submissions(repo, batch_in)
    assert report["summary"]["batch_conflict"] == 2
    assert report["summary"]["ingested"] == 0  # Batch Conflict 不得进入正式 tasks/agents
    assert not list((repo / "tasks").iterdir())
    assert len(list((repo / "quarantine" / "cross_package_conflict").iterdir())) == 2


def test_ingest_idempotent_same_archive_skipped(tmp_path):
    repo, report = _ingest_batch(tmp_path, {PROTO_TAR: (STUDENT, TASK_ID)})
    task_dirs_before = sorted(p.name for p in (repo / "tasks").iterdir())

    second = ingest_submissions(repo, tmp_path / "batch_in")
    assert second["summary"]["ingested"] == 0
    assert second["summary"]["skipped"] == 1
    rows = SubmissionRegistry(repo).rows
    assert len(rows) == 1  # 不重复入库
    assert sorted(p.name for p in (repo / "tasks").iterdir()) == task_dirs_before


def test_reverse_trace_anonymous_to_student(tmp_path):
    """AT -> mapping -> canonical -> registry -> student_id（§30 可逆追溯）。"""

    repo, _ = _ingest_batch(tmp_path, {PROTO_TAR: (STUDENT, TASK_ID)})
    registry = SubmissionRegistry(repo)
    assert len(registry.mappings) == 2

    anon_task = registry.mappings[0]["anonymous_task_id"]
    mapping = registry.find_canonical_task(anon_task)
    row = next(r for r in registry.rows if r["task_id"] == mapping["task_id"])
    assert row["student_id"] == STUDENT


def test_canonical_task_id_not_source(tmp_path):
    """source_task_id 与 canonical task_id 分离（§25）；canonical 不含 student_id。"""

    source_with_identity = f"{STUDENT}_task"
    repo, _ = _ingest_batch(tmp_path, {PROTO_TAR: (STUDENT, source_with_identity)})
    row = SubmissionRegistry(repo).rows[0]
    assert row["source_task_id"] == source_with_identity
    assert row["task_id"].startswith("T") and student_in(row["task_id"]) is False

    canonical_task = json.loads(
        (repo / "tasks" / row["task_id"] / "task/task.json").read_text(encoding="utf-8")
    )
    assert "student_id" not in canonical_task
    assert canonical_task.get("canonical_task_id") == row["task_id"]


def student_in(value: str) -> bool:
    return STUDENT in value


def test_no_identity_leak_in_distributions(tmp_path):
    """§44：distributions/** 不得出现 student_id / source id / archive 名。"""

    repo, _ = _ingest_batch(tmp_path, {
        f"DataC1004_P1_{STUDENT}.tar.gz": (STUDENT, f"{STUDENT}_task"),
        f"DataC1004_P1_{OTHER_STUDENT}.tar.gz": (OTHER_STUDENT, "T_2"),
    })
    registry = SubmissionRegistry(repo)
    leak_domains = {
        STUDENT, OTHER_STUDENT,
        "DataC1004_P1_273001.tar.gz", "DataC1004_P1_273002.tar.gz",
        *[row.get("submission_id", "") for row in registry.rows],
        *[row.get("source_task_id", "") for row in registry.rows],
    }
    for root in (repo / "distributions" / "tasks", repo / "distributions" / "agents"):
        hits = scan_for_identity_leaks(root, leak_domains)
        assert hits == [], f"匿名分发泄露: {hits}"


def test_anonymous_views_do_not_carry_private_material(tmp_path):
    """匿名 Task 无 reference.py / 身份字段；匿名 Agent 无 agent_manifest。"""

    repo, _ = _ingest_batch(tmp_path, {PROTO_TAR: (STUDENT, TASK_ID)})
    registry = SubmissionRegistry(repo)
    task_mapping = registry.mappings[0]
    agent_mapping = next(m for m in registry.mappings if "anonymous_agent_id" in m)

    view = repo / "distributions" / "tasks" / task_mapping["anonymous_task_id"]
    task_json = json.loads((view / "task/task.json").read_text(encoding="utf-8"))
    assert task_json.get("anonymous_task_id") == task_mapping["anonymous_task_id"]
    assert "student_id" not in task_json and "task_id" not in task_json
    assert not (view / "reference.py").exists()  # private gold 不进分发
    assert (view / "metadata.json").is_file()  # 匿名 metadata 存在
    metadata = json.loads((view / "metadata.json").read_text(encoding="utf-8"))
    assert "source_task_id" not in metadata

    agent_view = repo / "distributions" / "agents" / agent_mapping["anonymous_agent_id"]
    assert (agent_view / "agent.py").is_file()
    assert not (agent_view / "agent_manifest.json").exists()
    assert "student_id" not in (agent_view / "metadata.json").read_text(encoding="utf-8")