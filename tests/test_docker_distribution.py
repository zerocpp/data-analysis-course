"""Docker student distribution contracts.

Guards the invariants the offline distribution depends on, so a future edit
cannot silently break the delivered artifact:

1. the shipped workspace template is valid under the CURRENT submission
   contract (a broken template would break every student's first build);
2. the shipped ``.env.example`` carries connection configuration only —
   never Runtime Policy fields;
3. the version has exactly one source of truth;
4. the image build surface contains no real ``.env`` and no key material.

These tests never touch Docker: they validate the files the Docker build
consumes, which is the part CI can check without a daemon.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCKER_DIR = REPO_ROOT / "docker"
TEMPLATE_DIR = DOCKER_DIR / "template"

# Runtime Policy fields that must NEVER appear in a student .env.example.
POLICY_FIELDS = frozenset(
    {
        "LLM_TIMEOUT",
        "LLM_TOKEN_BUDGET",
        "MEMORY_LIMIT",
        "DISK_LIMIT",
        "WALL_TIME_LIMIT",
        "MAX_LLM_CALLS",
    }
)


def _active_env_lines(text: str) -> list[str]:
    """Uncommented KEY=VALUE lines only; comments are documentation."""

    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


# ---------------------------------------------------------------------------
# 1. Version — single source of truth
# ---------------------------------------------------------------------------


def test_version_file_exists_and_is_wellformed():
    version_file = REPO_ROOT / "VERSION"
    assert version_file.is_file(), "VERSION file is the single source of truth"
    value = version_file.read_text(encoding="utf-8").strip()
    assert value, "VERSION must not be empty"
    assert re.match(r"^[0-9]+(\.[0-9]+)*(-[.a-zA-Z0-9+]*)?$", value), (
        f"VERSION {value!r} is not a well-formed version"
    )


def test_version_has_no_second_source():
    """No module may hard-code a version literal; VERSION is authoritative."""

    assert (REPO_ROOT / "VERSION").is_file()

    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'dynamic = ["version"]' in pyproject, (
        "pyproject.toml must declare a dynamic version sourced from the package"
    )
    assert "code-set-arena-student" in pyproject

    init_src = (REPO_ROOT / "src/codesetarena/__init__.py").read_text(encoding="utf-8")
    assert "get_version" in init_src, (
        "codesetarena/__init__.py must resolve the version, not hard-code it"
    )
    assert not re.search(r'__version__\s*=\s*"\d', init_src), (
        "no hard-coded version literal in __init__.py"
    )


def test_read_version_script_matches_version_file():
    import subprocess
    import sys

    script = REPO_ROOT / "scripts/docker/read_version.py"
    assert script.is_file()
    out = subprocess.run(
        [sys.executable, str(script), "--tag"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert out == f"v{(REPO_ROOT / 'VERSION').read_text(encoding='utf-8').strip()}"


def test_read_version_rejects_malformed(tmp_path):
    import subprocess
    import sys

    bad = tmp_path / "VERSION"
    bad.write_text("not a version!!", encoding="utf-8")
    script = REPO_ROOT / "scripts/docker/read_version.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--version-file", str(bad)],
        capture_output=True, text=True,
    )
    assert proc.returncode != 0, "read_version.py must refuse a malformed version"


# ---------------------------------------------------------------------------
# 2. .env.example — connection configuration only
# ---------------------------------------------------------------------------


def test_env_example_is_connection_only():
    env_example = TEMPLATE_DIR / ".env.example"
    assert env_example.is_file(), "the release ships a public .env.example"

    keys = {
        line.split("=", 1)[0].strip()
        for line in _active_env_lines(env_example.read_text(encoding="utf-8"))
        if "=" in line
    }
    assert keys == {"LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"}, (
        f".env.example must only carry connection+model fields, got {sorted(keys)}"
    )


def test_env_example_has_no_runtime_policy_fields():
    text = (TEMPLATE_DIR / ".env.example").read_text(encoding="utf-8")
    active = _active_env_lines(text)
    leaked = sorted(
        key
        for key in POLICY_FIELDS
        if any(line.startswith(f"{key}=") for line in active)
    )
    assert not leaked, f"Runtime Policy fields leaked into .env.example: {leaked}"


def test_no_real_env_file_shipped():
    """A real .env must never exist in the Docker build surface."""

    assert not (TEMPLATE_DIR / ".env").exists()
    assert not (DOCKER_DIR / ".env").exists()


# ---------------------------------------------------------------------------
# 3. Template validity under the current submission contract
# ---------------------------------------------------------------------------


def test_template_task_json_has_phase1_required_fields():
    task = json.loads((TEMPLATE_DIR / "task.json").read_text(encoding="utf-8"))
    assert isinstance(task, dict)
    assert str(task.get("task_statement") or "").strip(), (
        "Phase 1 requires task_statement"
    )
    fm = str(task.get("hypothesized_failure_mode") or "").strip()
    assert fm, "Phase 1 requires hypothesized_failure_mode"

    from codesetarena.constants import HYPOTHESIZED_FM_CODES

    assert fm in HYPOTHESIZED_FM_CODES, (
        f"template FM {fm!r} is not a valid taxonomy key"
    )


def test_template_agent_exposes_contract_signature():
    """agent.py must expose run(task, df, llm) — exactly three parameters."""

    import ast

    source = (TEMPLATE_DIR / "agent.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    run_fns = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "run"
    ]
    assert len(run_fns) == 1, "template agent.py must define exactly one run()"
    assert len(run_fns[0].args.args) == 3, "run() takes (task, df, llm)"


def test_template_agent_matches_authoritative_source():
    """The shipped template must not drift from main/agent_template.py."""

    authoritative = (REPO_ROOT / "src/codesetarena/main/agent_template.py").read_text(
        encoding="utf-8"
    )
    shipped = (TEMPLATE_DIR / "agent.py").read_text(encoding="utf-8")
    assert shipped == authoritative, (
        "docker/template/agent.py drifted from src/codesetarena/main/agent_template.py"
    )


def test_template_reference_is_pure():
    """reference.py: only `df` input, no I/O imports."""

    source = (TEMPLATE_DIR / "reference.py").read_text(encoding="utf-8")
    assert "def reference(df)" in source
    for banned in ("open(", "import os", "import sys", "subprocess"):
        assert banned not in source, f"template reference.py must not use {banned}"


# ---------------------------------------------------------------------------
# 4. Build surface hygiene
# ---------------------------------------------------------------------------


def test_dockerignore_excludes_secrets():
    text = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")
    for pattern in (".env", ".env.*", "*.pem", "*.key"):
        assert pattern in text, f".dockerignore must exclude {pattern!r}"
    # runtime state must never enter the image
    for pattern in ("workspace/", "runs/", "results/"):
        assert pattern in text, f".dockerignore must exclude {pattern!r}"


def test_dockerfile_never_copies_env():
    text = (DOCKER_DIR / "Dockerfile").read_text(encoding="utf-8")
    assert ".env" not in re.sub(r"#.*", "", text), (
        "the Dockerfile must not COPY any .env"
    )
    # it must not copy the whole repository
    assert "COPY . ." not in text, "the Dockerfile must enumerate what it copies"


def test_dockerfile_targets_amd64_capable_base():
    text = (DOCKER_DIR / "Dockerfile").read_text(encoding="utf-8")
    assert "python:3.12" in text, "the runtime base must pin Python 3.12"


def test_compose_never_builds_and_has_no_docker_socket():
    import yaml

    compose = yaml.safe_load((DOCKER_DIR / "docker-compose.yml").read_text(encoding="utf-8"))
    service = compose["services"]["codesetarena"]
    assert "build" not in service, "students must never run `docker compose build`"
    assert "image" in service, "compose references the pre-loaded image"
    mounts = " ".join(str(v) for v in service.get("volumes", []))
    assert "docker.sock" not in mounts, "the runtime needs no Docker socket"
    assert not service.get("ports"), "this distribution exposes no ports"
    assert "./workspace:/workspace" in service["volumes"], "workspace must persist"
    assert "healthcheck" in service, "an offline healthcheck is required"


def test_compose_healthcheck_is_offline():
    text = (DOCKER_DIR / "docker-compose.yml").read_text(encoding="utf-8")
    hc = text.split("healthcheck:", 1)[1]
    for online in ("curl", "wget", "http://", "https://"):
        assert online not in hc, f"healthcheck must not depend on {online!r}"


def test_entrypoint_preserves_existing_workspace():
    """Seeding must be conditional on an empty workspace."""

    text = (DOCKER_DIR / "entrypoint.sh").read_text(encoding="utf-8")
    assert "cp -Rn" in text or "cp -n" in text, (
        "entrypoint must not overwrite existing workspace files"
    )
    for marker in ("task.json", "agent.py", "data.csv"):
        assert marker in text, f"empty-workspace detection must consider {marker}"


@pytest.mark.parametrize(
    "script",
    ["docker/entrypoint.sh", "docker/healthcheck.sh", "scripts/build_student_docker_release.sh"],
)
def test_shell_scripts_are_strict_mode(script):
    text = (REPO_ROOT / script).read_text(encoding="utf-8")
    assert "set -euo pipefail" in text, f"{script} must fail fast"


def test_release_script_reads_version_from_single_source():
    text = (REPO_ROOT / "scripts/build_student_docker_release.sh").read_text(encoding="utf-8")
    assert "read_version.py" in text, "the release builder must read the canonical version"
    assert "--platform" in text, "the release builder must pin the target platform"
    assert "docker save" in text and "docker buildx" in text
    assert "SHA256SUMS" in text
