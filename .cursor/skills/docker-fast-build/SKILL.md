---
name: docker-fast-build
description: >-
  Fast local Docker build/deploy of Octop on Windows (Docker Desktop). Uses a
  normal helper container to pre-fetch Python wheels, then builds fully offline
  to avoid BuildKit network hangs; rebuilds the running octop container (which
  serves agent.chusing.com via the existing nginx + Cloudflare tunnel). Use when
  the user asks to build, rebuild, redeploy, or publish Octop to local Docker,
  or says /docker-fast-build.
disable-model-invocation: true
---

# Docker Fast Build

在本机 Windows + Docker Desktop 上快速构建并部署 Octop 容器。

**开始时宣告：** "正在使用 docker-fast-build 技能构建部署 Octop。"

## 背景知识（不要重新踩坑）

- Docker Desktop for Windows 的 **BuildKit 构建网络对大包/并发下载会挂起**（日志长时间零新增），但普通容器 `docker run` 网络正常。
- 因此禁止让 Dockerfile 在构建时联网下载 Python 包。固定路线：**普通容器预下载 wheels → COPY 进镜像 → `uv pip install --no-index` 全离线安装**。
- `uv sync` lock 模式按 lock 中的精确 URL 取包，与 `--find-links` 按名称匹配不兼容；离线安装一律用 **`uv pip install`**。
- 制品来源：Python 用本地 `python:3.12-slim`、Node 用本地 `node:22-alpine`、npm 用 `https://registry.npmmirror.com`、PyPI 用清华源。

## 前置条件

`docker/.env`（已 gitignore，勿提交）至少包含：

```
OCTOP_DATA=D:/servers/octop
OCTOP_DEFAULT_PASSWORD=<初始管理员密码>
NODE_IMAGE=node:22-alpine
NPM_REGISTRY=https://registry.npmmirror.com
PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn
```

> Dockerfile 通过 bind mount 使用 wheels（不进镜像层），并在同层 purge
> build-essential；不要改回 `COPY wheels` 或在线 `uv sync`。

数据目录 `D:/servers/octop` 需存在。

## 构建决策

先判断本次改动范围：

| 改动 | 是否先跑预下载 | 说明 |
|------|----------------|------|
| 仅 `src/` / `dashboard/` 源码 | 否 | wheels 层走缓存，直接构建，约 1 分钟 |
| `dashboard/package-lock.json` 变化 | 否（npm 层在构建内自动重装） | 前端 npm ci 层失效，构建内走 npmmirror |
| `uv.lock` / `pyproject.toml` 依赖变化 | **是** | 必须先重新预下载 wheels |
| 首次构建或 `docker/wheels` 缺失 | **是** | |

### 步骤 1 —（按需）预下载 Python 制品

仅在上面要求时执行。在仓库根目录：

```powershell
docker run --rm -v "d:\Projects\agent-next:/p" -w /p python:3.12-slim sh docker/fetch_wheels.sh
```

脚本会生成 `docker/requirements.txt`（`uv export` 自 uv.lock）并把全部 wheel/sdist
及构建后端（setuptools/wheel/hatchling）下载到 `docker/wheels`（约 170MB，几分钟）。
结束输出应显示 wheel 文件数和目录大小。

### 步骤 2 — 构建并重启容器

```powershell
docker compose -f docker/docker-compose.yml -f docker/docker-compose.cloudflare.yml up -d --build
```

`docker-compose.cloudflare.yml` 让容器接入外部网络 `aries-net`（nginx 经此网络用
`http://octop:8088` 访问），并把构建源固定为国内镜像。

### 步骤 3 — 验证（全部通过才算完成）

```powershell
docker ps --filter "name=^octop$" --format "{{.Names}} | {{.Status}}"
curl.exe -s -w "`nHTTP %{http_code}`n" http://localhost:8088/api/health
curl.exe -s -o NUL -w "public: HTTP %{http_code}`n" https://agent.chusing.com/api/health
curl.exe -s -o NUL -w "research: HTTP %{http_code}`n" https://research.chusing.com/
curl.exe -s -o NUL -w "quant: HTTP %{http_code}`n" https://quant.chusing.com/
```

期望：容器 `Up (healthy)`；本地与外网 health 均 200（返回 JSON）；research/quant 均 200。
nginx 配置无需改动（现有 `agent.conf` 已反代 `http://octop:8088`），除非用户要求调整路由。

## 排障

| 现象 | 处理 |
|------|------|
| 构建长时间无新日志 | BuildKit 网络挂起；确认 wheels 已预下载且 Dockerfile 为离线安装，不要加联网下载 |
| 容器 `Restarting`，日志 `bash\r` | entrypoint 为 CRLF；Dockerfile 已有 `sed -i 's/\r$//'`，确认该层未被绕过 |
| `uv pip install --no-index` 报缺包 | 该包未在 wheels 中；重跑 fetch_wheels.sh（依赖变更后必须重跑） |
| `network.host is not allowed` | Docker Desktop 内置 BuildKit 不允许 host 网络；不要使用，走离线 wheels 方案 |
| 外网域名 5xx、本地正常 | Cloudflare 隧道问题，查 `aries-cloudflared` 容器日志；与本构建无关 |

## 红线

- 不让 Dockerfile 构建步骤联网下载 Python 包（不用回 `uv sync` 在线模式）。
- 不修改/提交 `docker/.env`、`docker/wheels/`、`docker/requirements.txt`（本地产物，已 gitignore）。
- 不编辑 `src/octop/dashboard/`（构建产物）。
