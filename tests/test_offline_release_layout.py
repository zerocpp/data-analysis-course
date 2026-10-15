"""v10.1.10 §41/§55/§56：离线学生发行包布局 + SHA256SUMS 完整性。"""
from __future__ import annotations

import hashlib

from bigdata_v10110_helpers import REPO_ROOT

VERSION = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
RELEASE = REPO_ROOT / "release" / f"BigData-v{VERSION}"


def _release_built() -> bool:
    return (RELEASE / "VERSION").is_file()


def test_release_layout_complete():
    assert _release_built(), "release 尚未组装（先运行 scripts/build_bigdata_release.sh）"
    for required in (
        "README.md",
        "RELEASE_NOTES.md",
        "VERSION",
        "docker-compose.yml",
        ".env.example",
        "images",
        "bin/datac",
        "bin/datac.bat",
        "workspace",
        "examples",
        "docs",
        "scripts/install.sh",
        "scripts/install.bat",
        "scripts/doctor.sh",
        "scripts/doctor.bat",
        "SHA256SUMS",
    ):
        assert (RELEASE / required).exists(), f"缺少发行物: {required}"
    images = list((RELEASE / "images").glob(f"bigdata-student-{VERSION}*.tar"))
    assert images, "缺少离线镜像 tar"


def test_workspace_ships_preextracted_two_phase_templates():
    """v10.1.15：workspace 预置 phase1/ phase2/ 已解压模板 + _restore/ 原始压缩包。"""
    assert _release_built(), "release 尚未组装（先运行 scripts/build_bigdata_release.sh）"
    ws = RELEASE / "workspace"
    for required in (
        "phase1/metadata.json",
        "phase2/metadata.json",
        "exposure/assignment.json",
        "runs/phase1",
        "runs/phase2",
        "runs/exposure",
        "store/packages",
        "store/builds",
        ".env.example",
        "README.md",
    ):
        assert (ws / required).exists(), f"workspace 缺少预置模板: {required}"
    assert (ws / "phase1" / ".env").is_file() is False, "模板不得携带真实 .env"
    assert not list((ws / "exposure").glob("AT*")),         "v10.1.17 发行包不得预置 demo 匿名题"

    # v10.1.17+：phase2 不再预置 assignment/匿名题；exposure 出厂为空
    assert (ws / "phase2" / "assignment.json").exists() is False,         "v10.1.18 phase2 不预置 assignment.json（匿名题由 exposure import 生成）"
    assert not list((ws / "demos").glob("*")) or not (ws / "demos").exists(),         "正式 workspace 不含 demos 目录"


def test_sha256sums_covers_release_files():
    assert _release_built()
    sums_text = (RELEASE / "SHA256SUMS").read_text(encoding="utf-8")
    entries = {}
    for line in sums_text.strip().splitlines():
        digest, name = line.split("  ", 1)
        entries[name] = digest
    for required in ("docker-compose.yml", "RELEASE_NOTES.md", "README.md", ".env.example"):
        assert required in entries
        path = RELEASE / required
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert entries[required] == digest, required
    image_entries = [n for n in entries if n.startswith("images/")]
    assert image_entries


def test_dist_offline_zip_exists():
    dist = REPO_ROOT / "dist"
    zips = list(dist.glob(f"BigData-v{VERSION}-Student-Offline.*"))
    assert zips, f"缺少最终离线发行包: {dist}"


def test_release_readme_is_student_facing():
    assert _release_built()
    text = (RELEASE / "README.md").read_text(encoding="utf-8")
    for section in ("docker load", "datac doctor", "BigData_P1", "Exposure", "BigData_P2"):
        assert section in text, section
    assert "codesetarena" not in text


def test_release_notes_document_scope():
    assert _release_built()
    text = (RELEASE / "RELEASE_NOTES.md").read_text(encoding="utf-8")
    for token in ("10.1.10", "datac", "Exposure", "offline"):
        assert token in text
