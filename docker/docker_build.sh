#!/usr/bin/env bash
# =============================================================================
# 构建 Octop Docker 镜像
#
# 用法:
#   bash docker/docker_build.sh [镜像标签] [额外 docker build 参数...]
#
# 示例:
#   bash docker/docker_build.sh
#   bash docker/docker_build.sh myreg/octop:v1
#   bash docker/docker_build.sh octop:dev --no-cache
#
# 国内加速（可选 build-arg，默认值见 compose 文件）:
#   NPM_REGISTRY=https://registry.npmmirror.com \
#   PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
#   APT_MIRROR=mirrors.cloud.tencent.com \
#   bash docker/docker_build.sh
#
# 注意：构建依赖预下载制品 docker/wheels 与 docker/requirements.txt
# （BuildKit 构建容器内联网下载 Python 包会挂起），首次或依赖变更后先运行：
#   docker run --rm -v "$PWD:/p" -w /p python:3.12-slim sh docker/fetch_wheels.sh
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

IMAGE_TAG="${1:-octop:latest}"
shift 2>/dev/null || true

if [ ! -d docker/wheels ] || [ ! -f docker/requirements.txt ]; then
    echo "❌ 缺少预下载制品（docker/wheels 或 docker/requirements.txt），请先运行："
    echo "   docker run --rm -v \"$REPO_ROOT:/p\" -w /p python:3.12-slim sh docker/fetch_wheels.sh"
    exit 1
fi

# BuildKit 启用 Dockerfile 缓存挂载，加速 npm / pip / apt 下载
export DOCKER_BUILDKIT=1

BUILD_ARGS=()
if [ -n "${PIP_INDEX_URL:-}" ]; then
    BUILD_ARGS+=(--build-arg "PIP_INDEX_URL=${PIP_INDEX_URL}")
fi
if [ -n "${PIP_TRUSTED_HOST:-}" ]; then
    BUILD_ARGS+=(--build-arg "PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST}")
fi
if [ -n "${NPM_REGISTRY:-}" ]; then
    BUILD_ARGS+=(--build-arg "NPM_REGISTRY=${NPM_REGISTRY}")
fi
if [ -n "${NODE_MAX_OLD_SPACE_SIZE:-}" ]; then
    BUILD_ARGS+=(--build-arg "NODE_MAX_OLD_SPACE_SIZE=${NODE_MAX_OLD_SPACE_SIZE}")
fi
if [ -n "${APT_MIRROR:-}" ]; then
    BUILD_ARGS+=(--build-arg "APT_MIRROR=${APT_MIRROR}")
fi

echo "╔══════════════════════════════════════════════════╗"
echo "║  正在构建 Octop Docker 镜像                      ║"
echo "║  标签: ${IMAGE_TAG}"
echo "╚══════════════════════════════════════════════════╝"
echo ""

docker build \
    -t "$IMAGE_TAG" \
    -f "${REPO_ROOT}/docker/Dockerfile" \
    "${BUILD_ARGS[@]}" \
    "$@" \
    "$REPO_ROOT"

echo ""
echo "✅ 构建完成: ${IMAGE_TAG}"
echo ""
echo "启动示例:"
echo "  docker run -d -p 8088:8088 -v octop-data:/data/.octop -e HOME=/data ${IMAGE_TAG}"
echo ""
echo "或使用 Compose:"
echo "  docker compose -f docker/docker-compose.yml up -d"
