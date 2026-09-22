#!/bin/sh
# 普通容器内预下载全部 Python 制品，绕开 BuildKit 网络（docker run 网络正常）
# 用法：
#   docker run --rm -v "d:\Projects\agent-next:/p" -w /p \
#     python:3.12-slim sh docker/fetch_wheels.sh
set -eu

PIP_TENCENT="https://mirrors.cloud.tencent.com/pypi/simple"
PIP_TUNA="https://pypi.tuna.tsinghua.edu.cn/simple"

pip install -q -i "$PIP_TENCENT" "uv==0.7.20"

# 从 uv.lock 生成完整运行时清单（不含项目本体，不带 hash）
uv export --frozen --no-dev --extra browser --no-emit-project --no-hashes \
    -o docker/requirements.txt

mkdir -p docker/wheels

# 下载全部制品：有 wheel 取 wheel，纯 sdist 包取 tar.gz（构建时编译）
pip download -q -r docker/requirements.txt -d docker/wheels -i "$PIP_TUNA"

# sdist 离线编译需要的构建后端（uv 隔离构建环境用）
pip download -q -d docker/wheels -i "$PIP_TUNA" setuptools wheel
# 项目本体构建后端（hatchling 及依赖；Dockerfile 第二阶段 uv pip install /app 用）
pip download -q -d docker/wheels -i "$PIP_TUNA" hatchling

echo "wheel files: $(ls docker/wheels | wc -l)"
du -sh docker/wheels
