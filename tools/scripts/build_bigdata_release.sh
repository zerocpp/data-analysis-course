#!/usr/bin/env bash
# =============================================================================
# BigData v10.1.19 — Student Offline Release Builder
#
# ONE command assembles the complete offline student distribution:
#
#     scripts/build_bigdata_release.sh
#
# Outputs:
#     release/BigData-v10.1.19/                 (student runtime bundle)
#     dist/BigData-v10.1.19-Student-Offline.zip (final deliverable)
#
# Requires: Docker Engine + buildx (build-time only). Students never build —
# they `docker load` the shipped image tar and use the bundled wrappers.
#
# Version has ONE source: the repository VERSION file (10.1.19).
# Secret safety: the image only ever sees pyproject.toml VERSION README.md
# src/ policy/ docker/template/ docker/*.sh; .dockerignore excludes any real
# .env. This script re-verifies the image before shipping (import yaml +
# datac --version + secret scan inside image).
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

VERSION="$(tr -d ' \t\r\n' < VERSION)"
[[ "${VERSION}" =~ ^10\.[0-9]+\.[0-9]+$ ]] || { echo "VERSION must be a 10.x.y release, got '${VERSION}'" >&2; exit 2; }
IMAGE_NAME="bigdata-student"
IMAGE_REF="${IMAGE_NAME}:${VERSION}"
PLATFORM="linux/amd64"
RELEASE_DIR="release/BigData-v${VERSION}"
IMAGE_TAR="bigdata-student-${VERSION}.image.tar"

log() { printf '[release] %s\n' "$*" >&2; }
die() { printf '[release] ERROR: %s\n' "$*" >&2; exit 1; }

[[ -f docker/Dockerfile ]] || die "缺少 docker/Dockerfile"
[[ -f docker/docker-compose.yml ]] || die "缺少 docker/docker-compose.yml"

# ---- 1. Build the student image (offline-capable runtime, deps baked in) ----
log "building ${IMAGE_REF} (${PLATFORM}) ..."
docker build --platform "${PLATFORM}" \
    --build-arg CSA_VERSION="${VERSION}" \
    -t "${IMAGE_REF}" -f docker/Dockerfile .

# ---- 2. Smoke the image BEFORE shipping ----
log "smoke: datac --version / --help / import yaml ..."
docker run --rm "${IMAGE_REF}" datac --version | grep -q "BigData ${VERSION}" \
    || die "image datac --version 不含 BigData ${VERSION}"
docker run --rm "${IMAGE_REF}" datac --help >/dev/null || die "image datac --help 失败"
docker run --rm --entrypoint python "${IMAGE_REF}" -c "import yaml, pandas, numpy; print('deps ok')" \
    >/dev/null || die "image 依赖缺失（yaml/pandas/numpy）"

log "secret scan inside image ..."
docker run --rm --entrypoint sh "${IMAGE_REF}" -c \
    'grep -RIlE "(^|[^a-zA-Z-])sk-[A-Za-z0-9_-]{16,}|BEGIN [A-Z ]*PRIVATE KEY|LLM_API_KEY=[A-Za-z0-9_]" /opt/csa /opt/codesetarena 2>/dev/null | grep -v "sk-TEST" && exit 1; exit 0' \
    || die "image 内发现疑似 secret"

# ---- 3. Assemble the release directory ----
log "assembling ${RELEASE_DIR} ..."
rm -rf "${RELEASE_DIR}"
mkdir -p "${RELEASE_DIR}"/{images,bin,workspace,examples,docs,scripts}

cp docker/docker-compose.yml "${RELEASE_DIR}/docker-compose.yml"
cp .env.example "${RELEASE_DIR}/.env.example"
printf '%s\n' "${VERSION}" > "${RELEASE_DIR}/VERSION"

log "saving image tar (offline delivery) ..."
docker save "${IMAGE_REF}" -o "${RELEASE_DIR}/images/${IMAGE_TAR}"

# ---- wrappers: host-side datac via docker compose exec ----
cat > "${RELEASE_DIR}/bin/datac" <<WRAPPER
#!/usr/bin/env bash
# BigData v${VERSION} student wrapper: runs the course CLI inside the local container.
set -euo pipefail
cd "\$(dirname "\${BASH_SOURCE[0]}")/.."
docker compose up -d >/dev/null
exec docker compose exec -T codesetarena datac "\$@"
WRAPPER
chmod +x "${RELEASE_DIR}/bin/datac"

cat > "${RELEASE_DIR}/bin/datac.bat" <<WRAPPER
@echo off
rem BigData v${VERSION} student wrapper (Windows): course CLI inside the local container.
cd /d "%~dp0.."
docker compose up -d >NUL
docker compose exec -T codesetarena datac %*
WRAPPER

# ---- installer / doctor helpers ----
cat > "${RELEASE_DIR}/scripts/install.sh" <<'INSTALL'
#!/usr/bin/env bash
# BigData offline install: load the image, start the stack, run doctor.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
VERSION="$(cat VERSION)"
echo "[install] loading bigdata-student:${VERSION} image ..."
docker load -i "images/bigdata-student-${VERSION}.image.tar"
echo "[install] starting container ..."
docker compose up -d
echo "[install] doctor:"
bin/datac doctor || true
echo "[install] done. 用 bin/datac --help 查看课程 CLI。"
INSTALL
chmod +x "${RELEASE_DIR}/scripts/install.sh"

cat > "${RELEASE_DIR}/scripts/install.bat" <<'INSTALL'
@echo off
rem BigData offline install (Windows): load image, start stack, doctor.
cd /d "%~dp0.."
set /p VERSION=<VERSION
echo [install] loading bigdata-student:%VERSION% image ...
docker load -i images\bigdata-student-%VERSION%.image.tar
echo [install] starting container ...
docker compose up -d
echo [install] done. Use bin\datac.bat --help
INSTALL

# ---- teacher demo: Runtime Resource Limits (one-click) ----
cp scripts/demo_runtime_limits.sh "${RELEASE_DIR}/scripts/" && chmod +x "${RELEASE_DIR}/scripts/demo_runtime_limits.sh"
cp scripts/demo_runtime_limits.bat "${RELEASE_DIR}/scripts/" 2>/dev/null || true
if [[ -f docs/RUNTIME_LIMITS_DEMO.md ]]; then
  cp docs/RUNTIME_LIMITS_DEMO.md "${RELEASE_DIR}/docs/"
fi

cat > "${RELEASE_DIR}/scripts/doctor.sh" <<'DOCTOR'
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
exec bin/datac doctor --workspace /workspace "$@"
DOCTOR
chmod +x "${RELEASE_DIR}/scripts/doctor.sh"

cat > "${RELEASE_DIR}/scripts/doctor.bat" <<'DOCTOR'
@echo off
cd /d "%~dp0.."
call bin\datac.bat doctor --workspace /workspace %*
DOCTOR

# ---- examples: deterministic smoke workspace ----
cp -r examples/deterministic_workspace "${RELEASE_DIR}/examples/"
rm -rf "${RELEASE_DIR}/examples/deterministic_workspace/.env"
printf 'LLM_BASE_URL=\nLLM_API_KEY=\nLLM_MODEL=\n' > "${RELEASE_DIR}/examples/deterministic_workspace/.env"

# ---- docs ----
cp docs/release/README_Student.md "${RELEASE_DIR}/README.md" 2>/dev/null \
    || cp docs/release/README_Student.md "${RELEASE_DIR}/README.md"
cp docs/release/RELEASE_NOTES.md "${RELEASE_DIR}/RELEASE_NOTES.md"
cp docs/release/学生手册_BigData_v10.1.19.md "${RELEASE_DIR}/docs/" 2>/dev/null || true
for manual in docs/学生手册_Phase1_Current.md docs/学生手册_Phase2_Review_Current.md; do
    [[ -f "$manual" ]] && sed 's/\bcodesetarena\b/datac/g' "$manual" > "${RELEASE_DIR}/docs/$(basename "$manual")"
done

# ---- workspace seed (host-side student workspace; v10.1.19 两阶段模板预解压) ----
# workspace/
#   phase1/    P1 模板（已解压；= release/phase1_student_sample/BigData-Phase1-Sample）
#   phase2/    P2 模板（已解压；demo 分配 R000001，正式发放可用 build_phase2_package.py 重建）
#   _restore/  两套模板的原始压缩包（学生改坏后解压到 workspace/ 覆盖还原）
log "assembling frozen workspace (v10.1.19 two-phase layout) ..."
WS="${RELEASE_DIR}/workspace"
rm -rf "${WS}" && mkdir -p "${WS}"
# 播种需要完整 course 包（pandas/yaml）：优先仓库 .venv，回退系统 python3
HOST_PY="${REPO_ROOT}/.venv/bin/python"
[[ -x "${HOST_PY}" ]] || HOST_PY="$(command -v python3)"
PYTHONPATH="${REPO_ROOT}/src" "${HOST_PY}" - "${WS}" <<'PYWS'
import sys
from pathlib import Path

from codesetarena.main.init_workspace import init_workspace

record = init_workspace(Path(sys.argv[1]))
print(f"[release] frozen workspace seeded: layout={record['layout']} "
      f"created={len(record['created'])}")
PYWS

# ---- 5. SHA256SUMS (every shipped file except the sums file itself) ----
log "generating SHA256SUMS ..."
(
  cd "${RELEASE_DIR}"
  find . -type f ! -name 'SHA256SUMS' -print0 | sort -z \
    | xargs -0 sha256sum | sed 's|\./||' > SHA256SUMS
)

# ---- 6. Final offline ZIP deliverable (deterministic; no zip binary needed) ----
log "packing dist/BigData-v${VERSION}-Student-Offline.zip ..."
mkdir -p dist
rm -f "dist/BigData-v${VERSION}-Student-Offline.zip"
python3 - "${VERSION}" <<'PYZIP'
import sys
import zipfile
from pathlib import Path

version = sys.argv[1]
bundle = Path("release") / f"BigData-v{version}"
out = Path(f"dist/BigData-v{version}-Student-Offline.zip")
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
    for path in sorted(p for p in bundle.rglob("*") if p.is_file()):
        info = zipfile.ZipInfo(f"BigData-v{version}/" + path.relative_to(bundle).as_posix(),
                               date_time=(2026, 10, 6, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        zf.writestr(info, path.read_bytes())
print(f"[release] packed {out} ({out.stat().st_size} bytes)")
PYZIP
cd "${REPO_ROOT}"

log "OK: ${RELEASE_DIR}"
log "OK: dist/BigData-v${VERSION}-Student-Offline.zip"
