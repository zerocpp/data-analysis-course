#!/usr/bin/env bash
# =============================================================================
# CodeSetArena Student Runtime — offline Docker release builder.
#
# ONE command produces the entire offline distribution:
#
#     scripts/build_student_docker_release.sh
#
# Result:
#
#     dist/codesetarena-student-local-v<VERSION>-linux-amd64/
#     ├── codesetarena-student-v<VERSION>.image.tar
#     ├── docker-compose.yml
#     ├── .env.example
#     ├── README.md
#     ├── VERSION
#     ├── BUILD_INFO.json
#     └── SHA256SUMS
#     dist/codesetarena-student-local-v<VERSION>-linux-amd64.tar.gz
#     dist/codesetarena-student-local-v<VERSION>-linux-amd64.tar.gz.sha256
#
# The version has ONE source: the repository `VERSION` file. It is never
# hard-coded here.
#
# The build is reproducible in shape: the target platform, base image and every
# Python dependency are pinned. Set BASE_PYTHON to a digest to pin the base
# image exactly (recommended for a released class).
#
# Secret safety: the image only ever sees `pyproject.toml VERSION README.md
# src/ policy/ docker/template/ docker/*.sh`. A real `.env` cannot enter a
# layer — see .dockerignore. This script re-scans the loaded image before
# packaging and aborts on any leak.
# =============================================================================
set -euo pipefail

# --- Resolve repo root (works from any CWD) ---------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"
# --- Pinned build inputs ----------------------------------------------------
BASE_PYTHON="${BASE_PYTHON:-python:3.12-slim-bookworm}"
TARGET_PLATFORM="${TARGET_PLATFORM:-linux/amd64}"
IMAGE_NAME="${IMAGE_NAME:-bigdata-student}"
PYTHON_BIN="${PYTHON_BIN:-python}"

log()  { printf '\033[1;34m[release]\033[0m %s\n' "$*" >&2; }
warn() { printf '\033[1;33m[release]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[release] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# --- 1. Version (single source of truth) -----------------------------------
VERSION="$("${PYTHON_BIN}" scripts/docker/read_version.py)" \
    || die "无法读取版本号（仓库根 VERSION）"
TAG="v${VERSION}"
IMAGE_REF="${IMAGE_NAME}:${TAG}"
RELEASE_NAME="codesetarena-student-local-${TAG}-linux-amd64"
RELEASE_DIR="dist/${RELEASE_NAME}"
IMAGE_TAR="${IMAGE_NAME}-${TAG}.image.tar"
FINAL_TGZ="dist/${RELEASE_NAME}.tar.gz"

log "version=${VERSION}  image=${IMAGE_REF}  platform=${TARGET_PLATFORM}"

# --- 2. Preconditions -------------------------------------------------------
command -v docker >/dev/null 2>&1 || die "未找到 docker CLI。Docker daemon 不可用时无法构建镜像。"
docker info >/dev/null 2>&1 || die "Docker daemon 不可用（docker info 失败）。"

if ! docker buildx version >/dev/null 2>&1; then
    die "未找到 docker buildx（构建 ${TARGET_PLATFORM} 跨平台镜像所必需）。"
fi

[[ -f VERSION ]]      || die "缺少 VERSION 文件"
[[ -f docker/Dockerfile ]]     || die "缺少 docker/Dockerfile"
[[ -f docker/docker-compose.yml ]] || die "缺少 docker/docker-compose.yml"
[[ -f docker/template/.env.example ]] || die "缺少 docker/template/.env.example"

GIT_COMMIT="$(git rev-parse HEAD 2>/dev/null || echo 'unavailable')"
BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# --- 3. Build linux/amd64 ---------------------------------------------------
log "开始构建镜像（buildx, ${TARGET_PLATFORM}）..."
docker buildx build \
    --platform "${TARGET_PLATFORM}" \
    --build-arg "BASE_PYTHON=${BASE_PYTHON}" \
    --build-arg "CSA_VERSION=${VERSION}" \
    --label "org.opencontainers.image.version=${VERSION}" \
    --label "org.opencontainers.image.revision=${GIT_COMMIT}" \
    --label "org.opencontainers.image.created=${BUILD_DATE}" \
    --tag "${IMAGE_REF}" \
    --load \
    --file docker/Dockerfile \
    .

# --- 4. Verify: platform + CLI ---------------------------------------------
log "校验镜像平台与 CLI..."
ARCH="$(docker image inspect "${IMAGE_REF}" --format '{{.Architecture}}')"
OS_NAME="$(docker image inspect "${IMAGE_REF}" --format '{{.Os}}')"
[[ "${ARCH}" == "amd64" ]] || die "镜像 Architecture=${ARCH}，期望 amd64"
[[ "${OS_NAME}" == "linux" ]] || die "镜像 Os=${OS_NAME}，期望 linux"
log "platform OK: os=${OS_NAME} arch=${ARCH}"

log "镜像内 CLI 自检..."
docker run --rm "${IMAGE_REF}" codesetarena --version >/dev/null \
    || die "镜像内 codesetarena --version 失败"
docker run --rm "${IMAGE_REF}" codesetarena --help >/dev/null \
    || die "镜像内 codesetarena --help 失败"
log "CLI OK"

IMAGE_ID="$(docker image inspect "${IMAGE_REF}" --format '{{.Id}}')"
IMAGE_SIZE="$(docker image inspect "${IMAGE_REF}" --format '{{.Size}}')"

# --- 5. Secret scan of the built image -------------------------------------
log "扫描镜像层中的机密..."
LEAK_SCAN_OUT="$(mktemp)"
trap 'rm -f "${LEAK_SCAN_OUT}"' EXIT

# Fail on: a literal .env file, a real API key shape, or a private course file.
docker run --rm --entrypoint sh "${IMAGE_REF}" -c '
    set -e
    found=0
    # 1. no .env anywhere (only .env.example is allowed)
    if find / -name ".env" -not -path "/proc/*" -not -path "/sys/*" 2>/dev/null | grep -q .; then
        echo "LEAK: .env present in image"; found=1
    fi
    # 2. no OpenCode course key shape (oc_sk_...)
    if grep -rIl "oc_sk_" /opt /workspace 2>/dev/null | grep -q .; then
        echo "LEAK: API key material found"; found=1
    fi
    # 3. no private course state
    for bad in roster.csv assignments.jsonl registry packages private; do
        if [ -e "/opt/codesetarena/${bad}" ]; then
            echo "LEAK: private path /opt/codesetarena/${bad}"; found=1
        fi
    done
    exit $found
' 2>&1 | tee "${LEAK_SCAN_OUT}" || die "镜像机密扫描失败（见上）"

if grep -q "LEAK:" "${LEAK_SCAN_OUT}"; then
    die "镜像中检测到机密泄漏，已中止发布。"
fi
log "secret scan OK"

# --- 6. docker save --------------------------------------------------------
log "保存镜像为 ${IMAGE_TAR} ..."
mkdir -p "${RELEASE_DIR}"
docker save "${IMAGE_REF}" -o "${RELEASE_DIR}/${IMAGE_TAR}"

# --- 7. Assemble the release directory -------------------------------------
log "组装发布目录 ${RELEASE_DIR} ..."

# compose references the loaded image; substitute the version placeholder.
sed "s|\${CSA_IMAGE:-bigdata-student:10.1.10}|${IMAGE_REF}|" \
    docker/docker-compose.yml > "${RELEASE_DIR}/docker-compose.yml"

# Student-facing .env.example = connection template only.
cp docker/template/.env.example "${RELEASE_DIR}/.env.example"

# Release README, version marker.
cp docker/RELEASE_README.md "${RELEASE_DIR}/README.md"
printf '%s\n' "${VERSION}" > "${RELEASE_DIR}/VERSION"

# An empty workspace/ so the bind mount has a home on first `up`.
mkdir -p "${RELEASE_DIR}/workspace"
cat > "${RELEASE_DIR}/workspace/README.md" <<'WS'
# Workspace

你的 Task / Agent / Submission 文件放在这里。

容器会在 workspace 为空时用公开模板初始化它；一旦你写入任何文件，
之后的重启都不会覆盖你的内容。

Connection Configuration 放在**本目录下的** `.env`：

    cp ../.env.example .env
    # 然后编辑 .env，只填 LLM_BASE_URL / LLM_API_KEY
WS

# --- 8. SHA256SUMS ---------------------------------------------------------
log "生成 SHA256SUMS ..."
(
    cd "${RELEASE_DIR}"
    sha256sum \
        "${IMAGE_TAR}" \
        docker-compose.yml \
        .env.example \
        README.md \
        VERSION \
        > SHA256SUMS
)

# --- 9. BUILD_INFO.json (no secrets) ---------------------------------------
log "写入 BUILD_INFO.json ..."
cat > "${RELEASE_DIR}/BUILD_INFO.json" <<INFO
{
  "version": "${VERSION}",
  "image_ref": "${IMAGE_REF}",
  "image_id": "${IMAGE_ID}",
  "image_size_bytes": ${IMAGE_SIZE},
  "platform": "${TARGET_PLATFORM}",
  "base_python": "${BASE_PYTHON}",
  "git_commit": "${GIT_COMMIT}",
  "build_date": "${BUILD_DATE}",
  "builder": "scripts/build_student_docker_release.sh"
}
INFO

# --- 10. Final tarball -----------------------------------------------------
log "打包 ${FINAL_TGZ} ..."
tar -czf "${FINAL_TGZ}" -C dist "${RELEASE_NAME}"
sha256sum "${FINAL_TGZ}" > "${FINAL_TGZ}.sha256"

log "完成。"
printf '\n\033[1;32m发布产物:\033[0m\n'
printf '  目录      : %s\n' "${RELEASE_DIR}"
printf '  归档      : %s\n' "${FINAL_TGZ}"
printf '  校验      : %s\n' "${FINAL_TGZ}.sha256"
printf '  镜像      : %s (%s)\n' "${IMAGE_REF}" "${TARGET_PLATFORM}"
printf '\n学生使用:\n'
printf '  tar -xzf %s\n' "$(basename "${FINAL_TGZ}")"
printf '  cd %s\n' "${RELEASE_NAME}"
printf '  cp .env.example workspace/.env   # 编辑并填写连接配置\n'
printf '  docker load -i %s\n' "${IMAGE_TAR}"
printf '  docker compose up -d && docker compose ps\n\n'
