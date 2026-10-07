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
