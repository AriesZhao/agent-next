# Octop Docker Deployment

---

This directory contains the Docker build and deployment assets for Octop.

### Files

| File | Description |
|------|-------------|
| `Dockerfile` | Multi-stage image definition (build context is the repo root) |
| `../.dockerignore` | Build context ignore rules (applies to both Podman and BuildKit) |
| `docker_build.sh` | Build image from source (BuildKit cache enabled by default) |
| `docker-compose.yml` | One-command local / self-hosted deployment |
| `docker-compose.postgres.yml` | PostgreSQL (+ pgvector) for dual-backend dev/tests |
| `postgres/init-vector.sql` | Instance-level `CREATE EXTENSION vector` (initdb.d; **not** Octop migrations) |
| `docker-entrypoint.sh` | Container entrypoint: first-run init + start server |
| `fetch_wheels.sh` | Pre-fetch Python artifacts in a regular container (offline build) |
| `docker-compose.cloudflare.yml` | Local Cloudflare override: joins external `aries-net` network |

### Quick start

**Option 1: Compose (recommended)**

From the repository root:

```bash
docker compose -f docker/docker-compose.yml up -d --build
```

Open `http://localhost:8088`. If `OCTOP_DEFAULT_PASSWORD` is unset, a strong random password is generated on first init and written to `/data/.octop/credential.txt`; if it is set, it is used as-is (must be ≥8 characters with letters and digits; passwords rejected by the app password policy — e.g. common ones — fall back to a random one automatically). Change the password immediately after first login.

**Option 2: Build script**

```bash
bash docker/docker_build.sh
docker run -d \
  --name octop \
  -p 8088:8088 \
  -v octop-data:/data/.octop \
  -e HOME=/data \
  octop:latest
```

### Offline build (Windows / Docker Desktop)

Downloading Python packages inside Docker Desktop's BuildKit containers can hang (large files, zero bytes for minutes; a plain `docker run` on the same host works). Use "pre-fetch in a regular container + fully offline build":

```bash
# 1) Pre-fetch (re-run after dependency changes; files already present are skipped)
docker run --rm -v "<absolute path to repo root>:/p" -w /p \
    python:3.12-slim sh docker/fetch_wheels.sh
# 2) Build offline and start
docker compose -f docker/docker-compose.yml \
    -f docker/docker-compose.cloudflare.yml up -d --build
```

Artifacts go to `docker/wheels` (~170MB) and `docker/requirements.txt` — local-only and git-ignored; the Dockerfile reads them via bind mounts, so they never enter the image layers. For source-only changes just run step 2 — the dependency layer is cached (~1 minute).

### Cloudflare integration (local reverse proxy)

`docker-compose.cloudflare.yml` adds the container to the external `aries-net` network so an nginx on that network can reverse-proxy a domain to `http://octop:8088`:

```
Browser → Cloudflare Tunnel → nginx (aries-net) → octop:8088
```

The override is local-environment specific; its verified default build sources (`node:22-alpine`, npmmirror, Tsinghua PyPI, Tencent apt) can be overridden in `docker/.env`. The external network is provided by aries-infra — check it with `docker network inspect aries-net` first.

### Faster downloads (China mirrors)

For online builds (Linux / CI with healthy networking), pass mirror env vars:

```bash
PIP_INDEX_URL=https://mirrors.cloud.tencent.com/pypi/simple \
PIP_TRUSTED_HOST=mirrors.cloud.tencent.com \
NPM_REGISTRY=https://registry.npmmirror.com \
APT_MIRROR=mirrors.cloud.tencent.com \
bash docker/docker_build.sh
```

> Note: `https://mirrors.cloud.tencent.com/npm/` returns 404 — use `https://registry.npmmirror.com` for npm.

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `HOME` | `/data` | Must be `/data` so `~/.octop` maps to the data volume |
| `OCTOP_PORT` | `8088` | HTTP listen port |
| `OCTOP_DEFAULT_PASSWORD` | _(unset)_ | First-run admin password (≥8 chars, letters + digits). When unset, a random password is generated and written to `credential.txt` |
| `OCTOP_ADMIN_USERNAME` | `admin` | Initial admin username |
| `OCTOP_DATABASE_URL` | — | PostgreSQL DSN (or other `OCTOP_DATABASE_*`; see [configuration.md](../docs/configuration.md)) |
| `OCTOP_DATABASE_DRIVER` | — | `sqlite` \| `postgresql` when overriding defaults via env |
| `OPENAI_API_KEY` | — | OpenAI-compatible API key |
| `DASHSCOPE_API_KEY` | — | Alibaba DashScope API key |

For Compose, put these in `docker/.env`. Values only reach the container if listed under `environment:` in `docker-compose.yml` (Compose interpolates `.env`; it does not auto-export every key). Alternatively write the same keys into the mounted data dir as `~/.octop/env`.

### Data persistence

- Compose mounts host `~/.octop` → container `/data/.octop`
- `docker run` example uses named volume `octop-data`
- First boot runs `octop init`; credentials are written to `/data/.octop/credential.txt`. With `OCTOP_DEFAULT_PASSWORD` unset a random password is generated; a specified password that the app password policy rejects falls back to a random one automatically (the container must never fail its first init because of a weak default).

### Health check

The image probes `GET /api/health`:

```bash
curl http://localhost:8088/api/health
```

### Operations

```bash
docker logs -f octop
docker exec -it octop octop --version
docker compose -f docker/docker-compose.yml down
docker compose -f docker/docker-compose.yml up -d --build
# Local Cloudflare route
docker compose -f docker/docker-compose.yml -f docker/docker-compose.cloudflare.yml up -d --build
```
