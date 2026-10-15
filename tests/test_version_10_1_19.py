"""v10.1.19 §30/§31：版本单一来源 + 全公开面一致为 BigData 10.1.19。"""
from __future__ import annotations

from bigdata_v10110_helpers import REPO_ROOT, cli


def test_version_file_is_10_1_19():
    assert (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip() == "10.1.19"


def test_version_module_resolves_10_1_19():
    import sys

    sys.path.insert(0, str(REPO_ROOT / "src"))
    from codesetarena.version import get_version

    assert get_version() == "10.1.19"


def test_cli_version_output_contains_bigdata_10_1_19():
    code, out = cli(["--version"])
    assert code == 0
    assert "BigData 10.1.19" in out
    assert "10.1.10" not in out


def test_compose_default_image_tag_is_10_1_10():
    import yaml

    compose = yaml.safe_load((REPO_ROOT / "docker" / "docker-compose.yml").read_text())
    image = compose["services"]["codesetarena"]["image"]
    assert image.endswith("bigdata-student:10.1.19}")
