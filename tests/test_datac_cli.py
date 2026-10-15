"""v10.1.10 §47：datac CLI contract（唯一公开入口 + deprecated 别名）。"""
from __future__ import annotations

from bigdata_v10110_helpers import REPO_ROOT, cli


def test_help_works_and_reports_datac():
    code, out = cli(["--help"])
    assert code == 0
    assert "usage: datac" in out
    for command in ("run", "doctor", "init", "exposure", "submission"):
        assert command in out


def test_version_flag_reports_bigdata():
    code, out = cli(["--version"])
    assert code == 0
    assert "BigData 10.1.19" in out


def test_doctor_and_init_exist():
    code, out = cli(["doctor", "--help"])
    assert code == 0
    code, out = cli(["init", "--help"])
    assert code == 0


def test_exposure_subcommands_exist():
    code, out = cli(["exposure", "--help"])
    assert code == 0
    for sub in ("import", "status", "run"):
        assert sub in out


def test_codesetarena_alias_still_runs_same_cli():
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-m", "codesetarena.main", "--version"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0
    assert "BigData 10.1.19" in proc.stdout


def test_student_release_docs_do_not_teach_codesetarena_entry():
    """学生 Release 文档只公开 datac（README 全文不得出现 codesetarena 调用行）。"""
    release_readme = REPO_ROOT / "release" / "BigData-v10.1.10" / "README.md"
    if not release_readme.is_file():  # release 未组装前只约束源码 README 的 datac 段
        root_readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        assert "datac" in root_readme
        return
    text = release_readme.read_text(encoding="utf-8")
    assert "datac" in text
    assert "codesetarena" not in text
