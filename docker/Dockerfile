# =============================================================================
# Octop — 多阶段 Docker 构建
#
# 阶段 1: 构建 React/TypeScript 前端
# 阶段 2: Python 运行时 + 预构建前端
#
# 本机构建（Windows / Docker Desktop，国内网络）:
#   Docker Desktop 的 BuildKit 构建容器内下载 Python 包会挂起（大文件零字节），
#   因此先用普通容器预下载制品，再全离线构建：
#     1) docker run --rm -v "<仓库根目录的绝对路径>:/p" -w /p \
#          python:3.12-slim sh docker/fetch_wheels.sh
#     2) docker compose -f docker/docker-compose.yml \
#          -f docker/docker-compose.cloudflare.yml up -d --build
#   详见 .cursor/skills/docker-fast-build/SKILL.md
#
# 可覆盖的 build-arg（默认值见 compose 文件）:
#   NODE_IMAGE / PYTHON_IMAGE         基础镜像
#   NPM_REGISTRY                      npm 源（推荐 https://registry.npmmirror.com）
#   PIP_INDEX_URL / PIP_TRUSTED_HOST  仅用于构建时 pip 安装 uv
#   APT_MIRROR                        apt 源主机
#   NODE_MAX_OLD_SPACE_SIZE           Vite 构建 Node 堆上限（低内存机器调小）
# =============================================================================

# 基础镜像（全局 ARG，FROM 可用；本机构建用国内前缀覆盖）
ARG NODE_IMAGE=node:20-slim
ARG PYTHON_IMAGE=python:3.12-slim

# ---------------------------------------------------------------------------
# 阶段 1 — 前端构建
# ---------------------------------------------------------------------------
FROM ${NODE_IMAGE} AS frontend-builder

ARG NPM_REGISTRY=
# Node heap ceiling for the vite build. Lower this (e.g. 1024) on low-memory
# build hosts (< 4G RAM) to avoid OOM kills — the value only caps the max,
# it is not pre-allocated.
ARG NODE_MAX_OLD_SPACE_SIZE=2048
WORKDIR /build/dashboard

COPY dashboard/package.json dashboard/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm,sharing=locked \
    set -eu; \
    if [ -n "$NPM_REGISTRY" ]; then npm config set registry "$NPM_REGISTRY"; fi; \
    npm ci --no-audit \
    || { echo "npm ci failed — retrying clean"; rm -rf node_modules; npm ci --no-audit; }; \
    test -x node_modules/.bin/vite

COPY dashboard/ ./

RUN mkdir -p ../src/octop/dashboard

# 镜像构建跳过完整 tsc；生产打包由 vite/esbuild 完成（使用 lockfile 中的 vite，不用 npx）
RUN NODE_ENV=production NODE_OPTIONS="--max-old-space-size=${NODE_MAX_OLD_SPACE_SIZE}" npm run build:docker


# ---------------------------------------------------------------------------
# 阶段 2 — Python 运行时
# ---------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime

LABEL maintainer="OrcaKit Team"
LABEL org.opencontainers.image.title="Octop"
LABEL org.opencontainers.image.description="Smarter self-hosted AI assistant — multi-user, multi-agent."
LABEL org.opencontainers.image.source="https://github.com/TencentCloud/orca"
LABEL org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/data \
    OCTOP_BIND_HOST=0.0.0.0 \
    OCTOP_PORT=8088 \
    OCTOP_LOG_LEVEL=info \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

ARG PIP_INDEX_URL=
ARG PIP_TRUSTED_HOST=
ARG APT_MIRROR=

# GHCR 国内不可达：用 pip 安装 uv（走 PIP_INDEX_URL 指定的国内源）
RUN --mount=type=cache,target=/root/.cache/pip \
    set -eu; \
    pip_args=""; \
    if [ -n "$PIP_INDEX_URL" ]; then \
        pip_args="--index-url $PIP_INDEX_URL"; \
        if [ -n "$PIP_TRUSTED_HOST" ]; then pip_args="$pip_args --trusted-host $PIP_TRUSTED_HOST"; fi; \
    fi; \
    pip install --no-cache-dir $pip_args "uv==0.7.*"

WORKDIR /app

# 依赖安装层（仅改源码时可复用）：
# - wheels/requirements 由 docker/fetch_wheels.sh 预生成，以 bind mount 提供，
#   不进入镜像层（比 COPY 省约 170MB）
# - build-essential 仅供 sdist 编译，同层 purge 回收；libffi-dev 保留
#   （C 扩展运行时依赖 libffi）
# - 不用 uv sync：其 lock 模式按精确 URL 取包，与 find-links 按名称匹配不兼容
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=docker/wheels,target=/tmp/wheels \
    --mount=type=bind,source=docker/requirements.txt,target=/tmp/requirements.txt \
    set -eu; \
    export UV_CACHE_DIR=/root/.cache/uv; \
    if [ -n "$APT_MIRROR" ]; then \
        printf 'Types: deb\nURIs: https://%s/debian\nSuites: trixie trixie-updates\nComponents: main contrib non-free non-free-firm\n\nTypes: deb\nURIs: https://%s/debian-security\nSuites: trixie-security\nComponents: main contrib non-free non-free-firm\n' \
            "$APT_MIRROR" "$APT_MIRROR" > /etc/apt/sources.list.d/debian.sources; \
    fi; \
    apt-get update; \
    apt-get install -y --no-install-recommends build-essential libffi-dev curl git; \
    uv venv --python /usr/local/bin/python3 /app/.venv; \
    . /app/.venv/bin/activate; \
    uv pip install --no-index --find-links /tmp/wheels -r /tmp/requirements.txt; \
    apt-get purge -y --auto-remove build-essential; \
    rm -rf /var/lib/apt/lists/*

ENV PATH="/app/.venv/bin:$PATH" \
    PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright

COPY src/ ./src/
COPY --from=frontend-builder /build/src/octop/dashboard/ ./src/octop/dashboard/

COPY .env.example ./
COPY docker/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
# Windows 检出可能为 CRLF，shebang 会变成 bash\r（exit 127）；规范化行尾
RUN sed -i 's/\r$//' /usr/local/bin/docker-entrypoint.sh \
    && chmod +x /usr/local/bin/docker-entrypoint.sh

# 安装项目本体（依赖已在上面装好；hatchling 在 wheels 中供隔离构建）
# 注：CJK 字体（fonts-noto-cjk）体积大且需构建时联网，暂不内置；
# 仅影响服务端图片/PDF 中文渲染，不影响 Dashboard
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=docker/wheels,target=/tmp/wheels \
    export UV_CACHE_DIR=/root/.cache/uv; \
    . /app/.venv/bin/activate \
    && uv pip install --no-index --find-links /tmp/wheels --no-deps /app

RUN mkdir -p /data/.octop

EXPOSE 8088

HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD ["sh", "-c", "curl -f http://localhost:${OCTOP_PORT:-8088}/api/health || exit 1"]

ENTRYPOINT ["docker-entrypoint.sh"]
CMD []
