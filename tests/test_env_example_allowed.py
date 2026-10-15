"""v10.1.10 §28：.env.example 支持语义 —— 模板保留、Build 排除、legacy 包仅 WARNING。"""
from __future__ import annotations

import zipfile

from bigdata_v10110_helpers import make_workspace
from codesetarena.submission.local import build_submission


def test_workspace_requires_env_example_and_build_excludes_it(tmp_path):
    ws = make_workspace(tmp_path / "ws", student_id="20260001")
    assert (ws / ".env.example").is_file()
    record = build_submission(ws, phase="phase1", student_id="20260001")
    assert record["exit_code"] == 0
    with zipfile.ZipFile(record["package_path"]) as bundle:
        names = bundle.namelist()
    assert ".env.example" not in names  # Build 自动排除（非 Submission Artifact）


def test_legacy_package_with_env_example_gets_warning_not_error(tmp_path):
    """冻结 corpus 兼容：手工加入 .env.example 的包 -> WARNING DEV_TEMPLATE_FILE。"""
    from pathlib import Path

    from codesetarena.submission.local import build_submission, _zip_tree
    from codesetarena.submission.archive import validate_submission_archive
    from codesetarena.submission.safe_extract import safe_extract_archive

    ws = make_workspace(tmp_path / "ws", student_id="20260002")
    record = build_submission(ws, phase="phase1", student_id="20260002")
    staging = tmp_path / "stage"
    staging.mkdir()
    safe_extract_archive(Path(record["package_path"]), staging)
    (staging / ".env.example").write_text("LLM_BASE_URL=\nLLM_API_KEY=\n", encoding="utf-8")
    legacy = tmp_path / "BigData_P1_20260002.zip"
    _zip_tree(staging, legacy)

    result = validate_submission_archive(legacy, phase="phase1")
    assert result["status"] == "PASS", result
    warnings = result.get("warnings") or []
    assert any(w.get("code") == "DEV_TEMPLATE_FILE" for w in warnings), warnings


def test_release_bundle_ships_env_example_template():
    """发行包必须带 .env.example（学生 cp 为 .env 后填写连接配置）。"""
    from pathlib import Path

    from bigdata_v10110_helpers import REPO_ROOT

    root_env = REPO_ROOT / ".env.example"
    assert root_env.is_file()
    text = root_env.read_text(encoding="utf-8")
    assert "LLM_BASE_URL=" in text and "LLM_API_KEY=" in text
    assert "sk-" not in text
    docker_template_env = Path(REPO_ROOT) / "docker" / "template" / ".env.example"
    if docker_template_env.is_file():
        assert "sk-" not in docker_template_env.read_text(encoding="utf-8")
