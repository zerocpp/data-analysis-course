"""v10.1.10 §21/§48：结构性证明 —— 学生 Build / 学生 Validate / TA Validate /\nBatch 第一遍全部复用同一个 core validator（validate_single_package）。"""
from __future__ import annotations

import inspect
from pathlib import Path

from bigdata_v10110_helpers import make_workspace


def _single_package_func():
    from codesetarena.submission import extension_validation

    return extension_validation.validate_single_package


def test_all_four_entries_share_the_same_core_function():
    """四条入口全部汇入同一个 core（extension_validation.validate_submission，
    由 validate_single_package 作为唯一单包入口包装；无第二套实现）。"""
    from codesetarena.submission import archive as archive_mod
    from codesetarena.submission import base_validation, local as local_mod
    from codesetarena.submission.batch import validator as batch_mod

    core = _single_package_func()
    assert core.__module__ == "codesetarena.submission.extension_validation"

    # local.build_submission / validate_local_zip 源码级引用同一入口族
    assert "validate_submission(" in inspect.getsource(local_mod.build_submission)
    assert "validate_single_package" in inspect.getsource(local_mod.validate_local_zip)
    # archive.validate_submission_archive（TA VALIDATE 核心 + batch 第一遍）
    assert "validate_single_package" in inspect.getsource(archive_mod.validate_submission_archive)
    assert "validate_submission_archive" in inspect.getsource(batch_mod.validate_archive)
    # batch 没有重新实现 base 规则：文件中不出现 Base 结构检查
    batch_src = inspect.getsource(batch_mod)
    assert "BASE_REQUIRED_FILES" not in batch_src
    assert "validate_base_submission" not in inspect.getsource(batch_mod)
    # validate_single_package 内部就是 core：base + phase extension
    core_src = inspect.getsource(core)
    assert "validate_submission(" in core_src
    assert "validate_base_submission" in inspect.getsource(base_validation.validate_base_submission)


def test_spy_same_validator_called_for_student_and_server_paths(tmp_path):
    """monkeypatch spy：student build / student validate / TA validate 都命中
    同一个 core（extension_validation.validate_submission）。"""
    import codesetarena.submission.extension_validation as ev
    import codesetarena.submission.local as local_mod

    calls: list[str] = []
    real_ev = ev.validate_submission
    real_local = local_mod.validate_submission

    def spy(root, phase, archive_name=None):
        calls.append(f"{archive_name}:{phase}")
        return real_ev(root, phase, archive_name=archive_name)

    ev.validate_submission = spy
    local_mod.validate_submission = spy
    try:
        from codesetarena.submission.local import build_submission, validate_local_zip
        from codesetarena.submission.archive import validate_submission_archive

        ws = make_workspace(tmp_path / "ws", student_id="20260001")
        record = build_submission(ws, phase="phase1", student_id="20260001")
        assert record["exit_code"] == 0
        package = Path(record["package_path"])

        validate_local_zip(package, phase="phase1")            # student validate
        validate_submission_archive(package, phase="phase1")   # TA/server validate
    finally:
        ev.validate_submission = real_ev
        local_mod.validate_submission = real_local

    assert len(calls) >= 3, calls
    name = "BigData_P1_20260001.zip"
    assert all(entry.startswith(name) for entry in calls), calls


def test_tampered_package_same_error_code_both_sides(tmp_path):
    """同一篡改包：学生 validate 与 TA validate 得到相同 error_code。"""
    import json
    import shutil
    import zipfile

    from codesetarena.submission.local import build_submission, validate_local_zip
    from codesetarena.submission.archive import validate_submission_archive

    ws = make_workspace(tmp_path / "ws", student_id="20260009")
    record = build_submission(ws, phase="phase1", student_id="20260009")
    src = Path(record["package_path"])
    tampered = tmp_path / "BigData_P1_20260009.zip"
    shutil.copy(src, tampered)
    with zipfile.ZipFile(tampered) as bundle:
        items = {n: bundle.read(n) for n in bundle.namelist()}
    manifest = json.loads(items["manifest.json"])
    manifest["student_id"] = "99999999"
    items["manifest.json"] = json.dumps(manifest, ensure_ascii=False, indent=2).encode()
    with zipfile.ZipFile(tampered, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name, data in items.items():
            bundle.writestr(name, data)

    student = validate_local_zip(tampered, phase="phase1")
    ta = validate_submission_archive(tampered, phase="phase1")
    assert student["status"] == ta["status"] == "FAIL"
    assert student["error_code"] == ta["error_code"] == "STUDENT_ID_MISMATCH"
