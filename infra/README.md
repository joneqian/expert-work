# Helix-Agent Infra (local dev / dogfood)

Local stack for the data + object-storage layer:
**Postgres 16** + **PgBouncer** (Stream A.3 — subsystems/23 § 9 M0) and
**MinIO** (Stream A.5 — [ADR-0004](../docs/adr/0004-object-storage.md)).

## Quick start

```bash
cd infra
docker compose up -d
docker compose ps
```

Services:

| Service     | Port           | Purpose                                                    |
|-------------|----------------|------------------------------------------------------------|
| `postgres`  | 5432           | Direct Postgres access — migrations, psql, pg_dump         |
| `pgbouncer` | 6432           | Connection pool — application traffic                       |
| `minio`     | 9000 / 9001    | S3 API / web console (uploads, snapshots, archive)         |

## Create the dev bucket (first run only)

The compose stack does not auto-create the MinIO bucket — a one-shot
``minio-init`` service trips ``docker compose up --wait`` because it
exits immediately. Use either path once after the stack is up:

```bash
# Path 1 — web console
open http://localhost:9001   # log in with the dev credentials below;
                              # click "Create Bucket" → helix-agent-dev

# Path 2 — mc CLI
docker run --rm --network infra_default \
    minio/mc:RELEASE.2025-08-13T08-35-41Z \
    sh -c "mc alias set local http://minio:9000 helix_agent helix_agent_dev_minio \
           && mc mb --ignore-existing local/helix-agent-dev"
```

Integration tests create the bucket via the S3 API automatically — no
manual step needed for ``pytest``.

The application **must** point at PgBouncer (`localhost:6432`); migrations
**must** point at Postgres directly (`localhost:5432`) because PgBouncer
transaction mode does not preserve session state across statements.

## Full M0 app stack (`full` profile)

The default `up` brings only the data layer. The helix services are
gated behind compose profiles (Stream I.1 — [STREAM-I-DESIGN](../docs/streams/STREAM-I-DESIGN.md)):

| Profile     | Adds                                                          |
|-------------|---------------------------------------------------------------|
| _(default)_ | data layer — `postgres` / `pgbouncer` / `redis` / `minio`     |
| `proxy`     | `nginx` mTLS terminator                                       |
| `auth`      | Keycloak IdP                                                  |
| `sandbox`   | `credential-proxy` (standalone)                               |
| `full`      | `migrate` / `control-plane` / `sandbox-supervisor` / `credential-proxy` |
| `observability` | Prometheus / Tempo / Loki / Grafana / Alertmanager + **Langfuse** (web/worker/clickhouse/postgres/redis) |

Langfuse (agent trace 调试) 起停 + 接线 + PII 脱敏自检见
[docs/runbooks/langfuse.md](../docs/runbooks/langfuse.md).

**便捷封装**:`make dev-up`（见 [`Makefile`](./Makefile)）一把起全量 dev 栈
（full+auth+observability 必需服务，排除会撞 8001 的 `control-plane-green`），
`make dev-info` 打印各 web 地址 + dev 登录凭据，`make help` 看全部目标。

```bash
# Pre-build the sandbox execution image first (see "Sandbox image" below).
docker build -f infra/sandbox-image/Dockerfile -t helix-sandbox:dev infra/sandbox-image

cd infra
docker compose --profile full up -d
docker compose ps        # control-plane on localhost:8000
```

`migrate` is a one-shot service: it runs `alembic upgrade head` and
exits — `control-plane` / `sandbox-supervisor` gate on it completing.
`sandbox-supervisor` mounts the host `/var/run/docker.sock`
(docker-out-of-docker, Mini-ADR I-2) so it can launch sandbox sibling
containers.

## Credentials

Defaults (placeholder, dev only):
- user: `helix_agent`
- password: `helix_agent_dev`
- database: `helix_agent_dev`

Override via `.env` or your shell:

```bash
export HELIX_DB_USER=…
export HELIX_DB_PASSWORD=…
export HELIX_DB_NAME=…
export HELIX_MINIO_ROOT_USER=…
export HELIX_MINIO_ROOT_PASSWORD=…
export HELIX_MINIO_BUCKET=…
```

## Why PgBouncer transaction mode

Per [subsystems/23 § 5.1](../docs/architecture/subsystems/23-postgres-scalability.md#51-pgbouncer-模式选择):

- 1000 client connections → 50 backend connections (10–20× density)
- Required for M0 dogfood under modest concurrency before M2 read replicas

**Application constraints under transaction mode**:

- ❌ No advisory locks that span transactions (`pg_advisory_lock` → use
  `pg_advisory_xact_lock` instead — already in `DbEventStore`).
- ❌ No `LISTEN`/`NOTIFY` (migrate to Redis pub/sub if needed).
- ⚠️ Prepared statements need PgBouncer ≥ 1.21 (this stack ships 1.24.1).
  Helix uses **asyncpg with `statement_cache_size=0`** to sidestep the
  client-side prepared cache entirely — see
  `packages/helix-persistence/src/helix_agent/persistence/database.py`.

## Postgres tuning applied

Database-wide defaults (set by `postgres/init/00-helix-init.sql` on first
boot — production RDS must apply these manually):

- `statement_timeout = 30s`
- `idle_in_transaction_session_timeout = 60s`
- `lock_timeout = 5s`

Server-level (set via `command:` in `docker-compose.yml`):

- `shared_preload_libraries = pg_stat_statements`
- `log_min_duration_statement = 500` (ms — log slow queries to stderr)

Extensions installed:

- `pg_stat_statements`
- `vector` (pgvector) — for long-term memory (M1)

`pg_partman` installation is deferred to M1 alongside actual partitioning
(see [subsystems/23 § 9 M1](../docs/architecture/subsystems/23-postgres-scalability.md#m1--分区--rls--autovacuum-调优)).

## Reset

Postgres + Keycloak data live in **host bind mounts** under `infra/.data/`
(gitignored), not Docker-managed volumes — so `docker volume prune` /
`docker system prune --volumes` / Docker Desktop disk reclaim can never silently
wipe the dev DB or admin bindings. `docker compose down -v` therefore does *not*
clear them either. To reset on purpose, delete the dirs:

```bash
make dev-clean                 # stop + remove containers (keeps .data/)
rm -rf .data/postgres .data/keycloak   # explicit, deliberate wipe
```

## At-rest encryption (D.4)

Per [ADR-0008](../docs/adr/0008-data-at-rest-encryption.md). M0 ships
without at-rest encryption by default so the fixture stays plug-and-play;
enable explicitly when you need to verify the SSE-KMS / encrypted-volume
path or run a security-review pass.

### MinIO SSE-KMS (dev)

```bash
# 1. Generate a 32-byte master key; the value is base64 of raw bytes.
export HELIX_MINIO_KMS_SECRET_KEY="helix-master:$(openssl rand -base64 32)"

# 2. Restart MinIO with the key in env.
docker compose up -d --force-recreate minio

# 3. Mark a bucket SSE-KMS-default (one-off via mc).
docker run --rm --network infra_default \
    -e MC_HOST_local="http://${HELIX_MINIO_ROOT_USER:-helix_agent}:${HELIX_MINIO_ROOT_PASSWORD:-helix_agent_dev_minio}@minio:9000" \
    minio/mc:RELEASE.2025-08-13T08-35-41Z \
    mc encrypt set sse-kms helix-master local/helix-agent-dev

# 4. Verify: write an object + inspect the on-disk file inside the
#    container. Object body should be ciphertext.
docker exec helix-minio sh -c 'head -c 64 /data/helix-agent-dev/<key>/xl.meta'
```

The same `mc encrypt set` line applied to the audit-WORM bucket
(`helix-agent-audit-worm`, D.1c) gives SSE-KMS + Object Lock together.

For a KES-backed dev setup (closer to prod), spin up KES manually next
to this compose; ADR-0008 keeps that pathway optional.

## Sandbox image (`sandbox-image/`)

The image the `exec_python` tool runs LLM-generated code in (Stream F.2 —
[STREAM-F-DESIGN](../docs/streams/STREAM-F-DESIGN.md)). It is **not** a
compose service: the Sandbox Supervisor (F.1) `docker run`s it per call.

```bash
docker build -t helix-sandbox:dev infra/sandbox-image
```

`runner.py` is the container's PID 1 — it reads line-delimited JSON
requests on stdin and writes one response per line (protocol in
STREAM-F-DESIGN § 4.2). Image-level hardening (non-root uid 10000, no
pip, exec-form entrypoint) lives in the `Dockerfile`; the `docker run`
flags (read-only rootfs, `--cap-drop=ALL`, pids/memory limits, network)
are applied by the F.3 `SandboxRuntimeProvider`, not baked into the image.

### Office image (`sandbox-image-office/`)

The heavier variant an agent gets when its manifest sets
`sandbox.image_variant: office`. It is `python:3.12-slim` + the office
Python libs (pandas/openpyxl/python-docx/python-pptx/pypdf/pdfplumber/
Pillow/matplotlib) **and** the system binaries those skills shell out to —
`soffice` (LibreOffice headless, no-GUI), `poppler-utils`, `ffmpeg` — so
the Anthropic `docx`/`xlsx`/`pptx`/`pdf` catalog runs whole (formula
recalc, thumbnails, accept-changes, PDF→image) rather than half-broken
(see `docs/design/skill-runtime-capability.md` §5.4).

It is **not** built by `make dev-up` (LibreOffice makes it ~1.3 GB).
Build it once on the host daemon the supervisor drives:

```bash
make -C infra build-sandbox-office
# or: docker build -f infra/sandbox-image-office/Dockerfile -t helix-sandbox-office:dev infra/
```

The build context is `infra/` (the **parent** dir), not the
`sandbox-image-office/` subdir, so the Dockerfile can `COPY` the single
shared `sandbox-image/runner.py`. The tag must match
`HELIX_SANDBOX_IMAGE_OFFICE` (default `helix-sandbox-office:dev`). soffice
adds ~1–3 s to the *first* `soffice` call in a container, not to container
startup (it is not launched at boot).

### Postgres at-rest (dev)

- **macOS host**: FileVault is enough — the whole disk is encrypted; the
  `./.data/postgres` bind-mount dir sits on the encrypted filesystem.
  No additional setup needed.
- **Linux host**: replace the named volume with a bind-mount onto a
  LUKS-encrypted path. Outline (one-off, host-side):

  ```bash
  # As root, one-shot:
  sudo dd if=/dev/zero of=/var/lib/helix/pgdata.luks bs=1G count=10
  sudo cryptsetup luksFormat /var/lib/helix/pgdata.luks
  sudo cryptsetup luksOpen /var/lib/helix/pgdata.luks helix-pgdata
  sudo mkfs.ext4 /dev/mapper/helix-pgdata
  sudo mkdir -p /mnt/helix-pgdata
  sudo mount /dev/mapper/helix-pgdata /mnt/helix-pgdata

  # Each boot, before ``docker compose up``:
  sudo cryptsetup luksOpen /var/lib/helix/pgdata.luks helix-pgdata
  sudo mount /dev/mapper/helix-pgdata /mnt/helix-pgdata
  ```

  Then edit the `postgres` service's `volumes:` in `docker-compose.yml`
  to bind-mount `/mnt/helix-pgdata:/var/lib/postgresql/data`. Locked
  state → raw `pgdata/*` is ciphertext (verification gate per
  [STREAM-D-DESIGN § 5 #4](../docs/streams/STREAM-D-DESIGN.md)).

### Production

Prod skips both of the above:

- Aliyun **RDS PostgreSQL**: enable "instance encryption" at create
  time (Aliyun KMS-backed).
- Aliyun **OSS**: SSE-KMS bucket policy at create time (same KMS).

See [ADR-0008 § 2](../docs/adr/0008-data-at-rest-encryption.md) for the
full prod wiring + key-rotation cadence.
