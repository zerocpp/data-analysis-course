#!/usr/bin/env bash
# =============================================================================
# Assemble the offline release package WITHOUT a Docker daemon.
#
# WHY THIS EXISTS
#   scripts/build_student_docker_release.sh is the canonical builder: it builds
#   the linux/amd64 image, verifies it, scans it, and saves it. That script
#   requires a working Docker daemon.
#
#   When no daemon is available (locked-down build host, CI without DinD), the
#   *packaging* half of the pipeline can still be produced and validated: the
#   release directory layout, compose file, .env.example, README, VERSION,
#   SHA256SUMS and BUILD_INFO.json are all derived from repository files, not
#   from the image. Only the .image.tar cannot be produced.
#
#   This script therefore emits a COMPLETE, structurally-correct release
#   directory and tarball with an explicit PLACEHOLDER for the image tar, and
#   refuses to pretend a real image exists. Run the canonical script on a
#   daemon-capable machine to obtain the shippable artifact.
#
# NEVER ship the output of this script to students. It is for layout
# verification and offline review only.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

PYTHON_BIN="${PYTHON_BIN:-python}"
IMAGE_NAME="${IMAGE_NAME:-codesetarena-student}"
MODE="${1:-assemble}"

log() { printf '\033[1;36m[assemble]\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31m[assemble] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

if [[ "${MODE}" == "--require-docker" ]]; then
    exec "${SCRIPT_DIR}/build_student_docker_release.sh"
fi

VERSION="$("${PYTHON_BIN}" scripts/docker/read_version.py)" || die "无法读取 VERSION"
TAG="v${VERSION}"
IMAGE_REF="${IMAGE_NAME}:${TAG}"
RELEASE_NAME="codesetarena-student-local-${TAG}-linux-amd64"
RELEASE_DIR="dist/${RELEASE_NAME}"
IMAGE_TAR="${IMAGE_NAME}-${TAG}.image.tar"
FINAL_TGZ="dist/${RELEASE_NAME}.tar.gz"

log "版本=${VERSION}（注意：本模式不构建镜像）"
rm -rf "${RELEASE_DIR}"
mkdir -p "${RELEASE_DIR}/workspace"

# compose bound to this exact version
sed "s|\${CSA_IMAGE:-codesetarena-student:v9.0.0}|${IMAGE_REF}|" \
    docker/docker-compose.yml > "${RELEASE_DIR}/docker-compose.yml"

cp docker/template/.env.example "${RELEASE_DIR}/.env.example"
cp docker/RELEASE_README.md "${RELEASE_DIR}/README.md"
printf '%s\n' "${VERSION}" > "${RELEASE_DIR}/VERSION"

cat > "${RELEASE_DIR}/workspace/README.md" <<'WS'
# Workspace

你的 Task / Agent / Submission 文件放在这里。详见上一级 README.md。
WS

GIT_COMMIT="$(git rev-parse HEAD 2>/dev/null || echo 'unavailable')"
BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

cat > "${RELEASE_DIR}/BUILD_INFO.json" <<INFO
{
  "version": "${VERSION}",
  "image_ref": "${IMAGE_REF}",
  "image_id": null,
  "image_size_bytes": null,
  "platform": "linux/amd64",
  "git_commit": "${GIT_COMMIT}",
  "build_date": "${BUILD_DATE}",
  "builder": "scripts/docker/assemble_release_no_daemon.sh",
  "image_built": false,
  "note": "Docker daemon unavailable on this host: only the release layout was assembled. Run scripts/build_student_docker_release.sh on a daemon-capable host for the shippable artifact."
}
INFO

# Explicit placeholder so nobody mistakes this for a shippable package.
cat > "${RELEASE_DIR}/${IMAGE_TAR}.PLACEHOLDER.txt" <<'PH'
此文件是占位符。

真正的镜像 tar 必须由 scripts/build_student_docker_release.sh 在有 Docker
daemon 的机器上生成：

    docker buildx build --platform linux/amd64 ...
    docker save codesetarena-student:vVERSION -o codesetarena-student-vVERSION.image.tar

本目录仅用于校验发布包的目录结构 / compose / .env.example / README。
不得直接交付给学生。
PH

# SHA256SUMS covers everything that is real
(
    cd "${RELEASE_DIR}"
    sha256sum docker-compose.yml .env.example README.md VERSION BUILD_INFO.json > SHA256SUMS
)

tar -czf "${FINAL_TGZ}" -C dist "${RELEASE_NAME}"

log "已组装（无镜像）：${RELEASE_DIR}"
ls -la "${RELEASE_DIR}"
log "归档：${FINAL_TGZ}"
