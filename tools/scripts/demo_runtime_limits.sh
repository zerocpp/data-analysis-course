#!/usr/bin/env bash
# BigData Runtime Resource Limits — One-Click Teacher Demo (v10.1.17+)
#
# 从任意目录调用均可：自动定位 Release 根目录；容器未启动时自动 compose up；
# 四个案例复用同一容器，全程约 20–30 秒；真实 Runtime 执行，无任何伪造。
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT_DIR}"

docker compose up -d >/dev/null 2>&1 || true
exec bin/datac demo runtime --all --workspace /workspace
