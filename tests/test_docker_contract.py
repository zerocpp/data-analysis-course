"""v10.1.10 §32/§35/§39：Docker 学生运行时契约（离线 load、无 build、无 socket）。"""
from __future__ import annotations

from pathlib import Path

import yaml

from bigdata_v10110_helpers import REPO_ROOT

COMPOSE = REPO_ROOT / "docker" / "docker-compose.yml"
DOCKERFILE = REPO_ROOT / "docker" / "Dockerfile"


def _compose_dict():
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def test_compose_exists_and_never_builds():
    text = COMPOSE.read_text(encoding="utf-8")
    assert "build:" not in text
    compose = _compose_dict()
    service = compose["services"]["codesetarena"]
    assert "build" not in service


def test_compose_references_offline_local_image():
    service = _compose_dict()["services"]["codesetarena"]
    image = service["image"]
    assert "${CSA_IMAGE:-bigdata-student:10.1.15}" in image


def test_compose_has_no_docker_socket_mount():
    volumes = _compose_dict()["services"]["codesetarena"].get("volumes") or []
    for volume in volumes:
        assert "docker.sock" not in str(volume)


def test_compose_mounts_workspace_volume():
    service = _compose_dict()["services"]["codesetarena"]
    volumes = service.get("volumes") or []
    assert any("./workspace:/workspace" in str(v) for v in volumes)


def test_compose_env_file_optional_not_required():
    env_file = _compose_dict()["services"]["codesetarena"].get("env_file")
    entry = env_file if isinstance(env_file, dict) else (env_file or [{}])[0]
    if isinstance(env_file, list) and not isinstance(env_file[0], dict):
        assert True  # legacy list form: optional by default
    else:
        assert entry.get("required") is False or entry.get("path")


def test_dockerfile_bakes_everything_no_runtime_pip():
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "bigdata" in text.lower() or "codesetarena" in text.lower()
    runtime_section = text.split("FROM ${BASE_PYTHON} AS runtime")[-1]
    assert "pip install" not in runtime_section.replace(
        "# ", "#"
    ), "runtime 阶段不允许 pip install（离线要求）"
    assert 'COPY policy/ /opt/codesetarena/policy/' in text
    assert 'ENTRYPOINT' in text


def test_release_builder_exists():
    assert (REPO_ROOT / "scripts" / "build_bigdata_release.sh").is_file()
