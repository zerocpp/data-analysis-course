"""v10.1.10 §28/§53：.env（真实 Secret）禁止进入 P1/P2 包；API key 不泄漏。"""
from __future__ import annotations

import zipfile
from pathlib import Path

from bigdata_v10110_helpers import make_workspace
from codesetarena.submission.local import _zip_tree, build_submission
from codesetarena.submission.safe_extract import safe_extract_archive

SECRET = "sk-TEST-SECRET-DO-NOT-SHIP"


def test_p1_zip_excludes_env_and_secret_string(tmp_path):
    ws = make_workspace(tmp_path / "ws", student_id="20260001")
    record = build_submission(ws, phase="phase1", student_id="20260001")
    assert record["exit_code"] == 0
    with zipfile.ZipFile(record["package_path"]) as bundle:
        names = bundle.namelist()
        all_bytes = b"".join(bundle.read(n) for n in names)
    assert ".env" not in names
    assert not any(".env" in n for n in names)
    assert SECRET.encode() not in all_bytes


def test_p2_validator_rejects_secret_content_hidden_in_subdir(tmp_path):
    """§29/§53：把 api_key 内容藏进包内任意子目录 -> Validator 内容层扫描 ERROR。"""
    ws = make_workspace(tmp_path / "ws", student_id="20260001", phase="P2")
    record = build_submission(ws, phase="phase2", student_id="20260001")
    assert record["exit_code"] == 0, record["local_validation"]
    staging = tmp_path / "stage"
    staging.mkdir()
    safe_extract_archive(Path(record["package_path"]), staging)
    (staging / "report").mkdir()
    (staging / "report" / "api_key.txt").write_text(f"token: {SECRET}\n", encoding="utf-8")
    evil = tmp_path / "BigData_P2_20260001.zip"
    _zip_tree(staging, evil)

    from codesetarena.submission.archive import validate_submission_archive

    result = validate_submission_archive(evil, phase="phase2")
    assert result["status"] == "FAIL"
    assert result["error_code"] == "SECRET_FILE"


def test_p2_zip_excludes_env_even_inside_report_dir(tmp_path):
    """Build 端：workspace 内任何未声明交付物（含嵌套 .env）永不进入 P2 包。"""
    ws = make_workspace(tmp_path / "ws", student_id="20260001", phase="P2")
    (ws / "report").mkdir()
    (ws / "report" / ".env").write_text(f"LLM_API_KEY={SECRET}\n", encoding="utf-8")
    record = build_submission(ws, phase="phase2", student_id="20260001")
    assert record["exit_code"] == 0, record["local_validation"]
    with zipfile.ZipFile(record["package_path"]) as bundle:
        names = bundle.namelist()
        all_bytes = b"".join(bundle.read(n) for n in names)
    assert not any(n.startswith("report/") for n in names)
    assert not any(".env" in n and n != ".env.example" for n in names)
    assert SECRET.encode() not in all_bytes


def test_validator_rejects_archive_with_env_even_if_hand_crafted(tmp_path):
    """代码级强校验：手工构造含 .env 的包，Validator 必须 ERROR 拒绝（§29）。"""
    from codesetarena.submission.local import _zip_tree

    ws = make_workspace(tmp_path / "ws", student_id="20260001")
    record = build_submission(ws, phase="phase1", student_id="20260001")
    good = Path(record["package_path"])
    staging = tmp_path / "stage"
    staging.mkdir()
    safe_extract_archive(good, staging)
    (staging / ".env").write_text(f"LLM_API_KEY={SECRET}\n", encoding="utf-8")
    evil = tmp_path / "BigData_P1_20260001.zip"
    _zip_tree(staging, evil)

    from codesetarena.submission.archive import validate_submission_archive

    result = validate_submission_archive(evil, phase="phase1")
    assert result["status"] == "FAIL"
    assert result["error_code"] in ("FORBIDDEN_FILE", "SECRET_FILE")


def test_secret_guard_rejects_key_files(tmp_path):
    """*.key / credentials / secret 文件：Build 端排除 + Validator 端 SECRET_FILE。"""
    from codesetarena.submission.base_validation import validate_base_submission

    ws = make_workspace(tmp_path / "ws", student_id="20260001")
    record = build_submission(ws, phase="phase1", student_id="20260001")
    from pathlib import Path

    staging = tmp_path / "stage2"
    staging.mkdir()
    from codesetarena.submission.safe_extract import safe_extract_archive

    safe_extract_archive(Path(record["package_path"]), staging)
    (staging / "server.key").write_text("-----BEGIN PRIVATE KEY-----", encoding="utf-8")
    (staging / "credentials.json").write_text("{\"k\": \"v\"}", encoding="utf-8")
    result = validate_base_submission(staging)
    assert result["status"] == "FAIL"
    codes = {e["code"] for e in result["errors"]}
    assert "SECRET_FILE" in codes
