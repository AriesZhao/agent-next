# Octop Docker 部署

---

本目录包含 Octop 的 Docker 构建与部署相关文件。

### 文件说明

| 文件 | 说明 |
|------|------|
| `Dockerfile` | 多阶段镜像定义（构建上下文为仓库根目录） |
| `../.dockerignore` | 构建上下文忽略规则（Podman 与 BuildKit 均适用） |
| `docker_build.sh` | 从源码构建镜像（默认开启 BuildKit 缓存） |
| `docker-compose.yml` | 本地开发 / 自托管一键启动 |
| `docker-compose.postgres.yml` | 仅 PostgreSQL（+ pgvector）开发/测试库；扩展在 `postgres/init-vector.sql` |
| `postgres/init-vector.sql` | 实例级 `CREATE EXTENSION vector`（initdb.d；**不**进 Octop 迁移） |
| `docker-entrypoint.sh` | 容器入口：首次初始化数据库并启动服务 |
| `fetch_wheels.sh` | 在普通容器内预下载 Python 制品（离线构建用，见下） |
| `docker-compose.cloudflare.yml` | 本机 Cloudflare 接入 override：加入外部网络 `aries-net` |

### 快速开始

**方式一：Compose（推荐）**

在仓库根目录执行：

```bash
docker compose -f docker/docker-compose.yml up -d --build
```

访问 `http://localhost:8088`。未设置 `OCTOP_DEFAULT_PASSWORD` 时，首次初始化会自动生成随机密码并写入 `/data/.octop/credential.txt`；设置了则按设置值初始化（须 ≥8 位且同时包含字母和数字；被应用密码策略拒绝的常见弱密码会自动回退为随机密码）。首次登录后请立即修改密码。

**方式二：构建脚本**

```bash
bash docker/docker_build.sh
docker run -d \
  --name octop \
  -p 8088:8088 \
  -v octop-data:/data/.octop \
  -e HOME=/data \
  octop:latest
```

### 本机离线构建（Windows / Docker Desktop）

Docker Desktop 的 BuildKit 构建容器内下载 Python 包可能挂起（大文件长时间零字节；普通 `docker run` 网络正常）。此时使用「普通容器预下载制品 + 全离线构建」：

```bash
# 1) 预下载（依赖变更后重跑；目标目录已有的文件会跳过）
docker run --rm -v "<仓库根目录的绝对路径>:/p" -w /p \
    python:3.12-slim sh docker/fetch_wheels.sh
# 2) 全离线构建并启动
docker compose -f docker/docker-compose.yml \
    -f docker/docker-compose.cloudflare.yml up -d --build
```

制品写入 `docker/wheels`（约 170MB）与 `docker/requirements.txt`，均为本地产物（已 gitignore）；Dockerfile 以 bind mount 读取、不进入镜像层。仅改源码时直接执行第 2 步，依赖层走缓存，约 1 分钟完成。

### 接入 Cloudflare（本机反代）

`docker-compose.cloudflare.yml` 让容器加入外部网络 `aries-net`，供同网络的 nginx 将域名反代到 `http://octop:8088`，链路为：

```
浏览器 → Cloudflare Tunnel → nginx(aries-net) → octop:8088
```

该 override 为本机环境专用；其默认构建源（`node:22-alpine`、npmmirror、清华 PyPI、腾讯云 apt）可在 `docker/.env` 覆盖。使用前需先创建外部网络（由 aries-infra 提供）：`docker network inspect aries-net`。

### 国内镜像加速

在线构建（网络正常的 Linux / CI 环境）可通过环境变量加速依赖下载：

```bash
PIP_INDEX_URL=https://mirrors.cloud.tencent.com/pypi/simple \
PIP_TRUSTED_HOST=mirrors.cloud.tencent.com \
NPM_REGISTRY=https://registry.npmmirror.com \
APT_MIRROR=mirrors.cloud.tencent.com \
bash docker/docker_build.sh
```

> 注意：`https://mirrors.cloud.tencent.com/npm/` 会 404，npm 源请用 `https://registry.npmmirror.com`。

### 常用环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `HOME` | `/data` | 必须为 `/data`，数据目录映射到 `~/.octop` |
| `OCTOP_PORT` | `8088` | HTTP 服务端口 |
| `OCTOP_DEFAULT_PASSWORD` | _(未设置)_ | 首次管理员密码（≥8 位，字母+数字）。未设置时自动生成随机密码并写入 `credential.txt` |
| `OCTOP_ADMIN_USERNAME` | `admin` | 首次管理员用户名 |
| `OCTOP_DATABASE_URL` | — | PostgreSQL DSN（或其他 `OCTOP_DATABASE_*`，见 [configuration.md](../docs/configuration.md)） |
| `OCTOP_DATABASE_DRIVER` | — | 通过环境变量覆盖时：`sqlite` \| `postgresql` |
| `OPENAI_API_KEY` | — | OpenAI 兼容 API Key |
| `DASHSCOPE_API_KEY` | — | 阿里云通义千问 API Key |

Compose 可在 `docker/.env` 中配置上述变量。注意：`.env` 只参与 Compose 插值，变量必须出现在 `docker-compose.yml` 的 `environment:` 中才会进入容器。也可把相同键写入挂载数据目录下的 `~/.octop/env`。

### 数据持久化

- Compose 默认将宿主机 `~/.octop` 挂载到容器 `/data/.octop`
- `docker run` 示例使用命名卷 `octop-data`
- 首次启动会自动执行 `octop init`，凭据写入容器内 `/data/.octop/credential.txt`。未设置 `OCTOP_DEFAULT_PASSWORD` 时自动生成随机密码；指定的密码被应用密码策略拒绝时自动回退为随机密码（首次初始化绝不因弱默认密码而失败）。

### 健康检查

镜像内置 `HEALTHCHECK`，探测 `GET /api/health`：

```bash
curl http://localhost:8088/api/health
```

### 常用运维命令

```bash
docker logs -f octop
docker exec -it octop octop --version
docker compose -f docker/docker-compose.yml down
docker compose -f docker/docker-compose.yml up -d --build
# 本机 Cloudflare 接入路线
docker compose -f docker/docker-compose.yml -f docker/docker-compose.cloudflare.yml up -d --build
```
