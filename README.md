<div align="center">

# Expert Work

**A business-agnostic, multi-tenant platform for per-user persistent AI agents.**

Every user gets a durable agent instance — its own conversation history, long-term
memory, and persistent workspace — running on the Anthropic **Brain · Hands · Session**
paradigm: a stateless harness loop (LangGraph), an isolated execution sandbox
(Docker / gVisor), and an append-only Postgres event log as the single source of truth.

[![License](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%2B-3776AB.svg)](pyproject.toml)
[![Frontend](https://img.shields.io/badge/admin--ui-React%2019%20%2B%20TS-61DAFB.svg)](apps/admin-ui)
[![Status](https://img.shields.io/badge/status-M0%20complete%20%E2%86%92%20M1-success.svg)](docs/ITERATION-PLAN.md)

[Quick Start](#-quick-start) · [Deployment](#-deployment) · [Architecture](#-architecture) · [Documentation](#-documentation)

</div>

---

## Why Expert Work

Most agent stacks are bound to one product. Expert Work keeps the **engine generic** and
pushes everything domain-specific — compliance posture, PII fields, prompts/tools,
isolation strength — to the **tenant** layer. The result is one platform that any
team can run multi-tenant, where each person has a real, stateful agent rather than a
stateless chat box.

- **Stateful, not stateless.** Per-user durable agents: conversation + multi-tier
  memory + a persistent `/workspace`, with the event log as source of truth.
- **Generic core, pluggable edges.** The orchestrator knows nothing about your
  business; tenants configure models, tools, MCP servers, and policy.
- **Operated like a product.** OIDC SSO, RLS isolation, audit, metering/billing,
  a white-labeled login, an in-app setup wizard, and a product-grade admin console.

> **Status** — M0 (product-grade MVP) closeout complete; hardening toward M1.
> Live milestone status in [docs/ITERATION-PLAN.md](docs/ITERATION-PLAN.md);
> design in [docs/architecture/00-OVERVIEW.md](docs/architecture/00-OVERVIEW.md).
> (Expert Work began as a replacement for a Dify deployment, then generalized into a
> standalone platform.)

## ✨ Capabilities

| Area | What you get |
|------|--------------|
| **Per-user agents** | Durable per `(tenant, user)`: conversation, long-term memory, persistent `/workspace`. |
| **Multi-tier memory** | Working window + plan state + episodic + long-term; hybrid retrieval (pgvector + BM25 fused via RRF, temporal decay, MMR, optional cross-encoder rerank) + background consolidator. |
| **Context engineering** | LLM-free working window, summarize-the-middle compressor, recoverable tool-output overflow, error-as-guidance recovery, event-driven effort escalation. |
| **Agent Skills** | Anthropic Agent Skills spec (SKILL.md + frontmatter); platform catalog, lazy progressive disclosure, threat scanning. |
| **MCP client** | Consumes external MCP servers (GitHub / Postgres / Linear / …) via a platform catalog + per-tenant instantiation. Expert Work is a client, not a server. |
| **Sandbox isolation** | Per-session Docker / gVisor sandbox; filesystem, network, and subprocess isolation via a sandbox supervisor. |
| **Multi-tenant governance** | Postgres RLS, OIDC/JWT via Keycloak, RBAC + ABAC, audit log, platform-centralized LLM/embedding config, metering & billing rollups. |
| **Account & onboarding** | White-labeled login theme, in-app first-run setup wizard, member invites, cross-tenant platform admin — all without touching the IdP console. |
| **Admin UI** | Product-grade React console for agents, runs, memory, skills, members, and tenant administration. |
| **Observability** | Structured logs with redaction, W3C trace propagation across trusted hops, an as-built Prometheus metric catalog, Langfuse LLM traces. |

## 🏗 Architecture

```
                         ┌──────────────┐
   Browser ──TLS──▶ nginx│ blue / green │   admin-ui (React)
                         └──────┬───────┘
                                ▼
                       ┌─────────────────┐      ┌──────────────┐
                       │  control-plane  │◀────▶│   Keycloak   │  OIDC / JWT
                       │  (stateless API)│      └──────────────┘
                       └───┬──────────┬──┘
              event log /  │          │  per-session
              state (RLS)  ▼          ▼  sandboxes
                   ┌────────────┐  ┌────────────────────┐
                   │ Postgres   │  │ sandbox-supervisor │──▶ Docker / gVisor
                   │ + pgvector │  └────────────────────┘     (isolated /workspace)
                   └────────────┘
   Redis (limits/queues) · MinIO/OSS (uploads/snapshots) · credential-proxy (egress)
```

The harness loop runs in the **orchestrator/runtime**; the **control-plane** is the
stateless HTTP surface (auth, RLS, routing) and can run blue/green against one DB.
Full design in [docs/architecture](docs/architecture/).

## 🚀 Quick Start

Local dev stack (macOS + Docker). Full "company-from-zero" walkthrough:
[docs/runbooks/getting-started.md](docs/runbooks/getting-started.md).

```bash
# 1. Backend deps (Python 3.12+, uv workspace) — only needed for tests/tooling
uv sync

# 2. Configure local env (git-ignored; copy the template, add your Anthropic key)
cd infra && cp .env.example .env

# 3. Bring up the full dev stack (data + backend + Keycloak + observability;
#    runs migrations and auto-promotes the dev user to system_admin)
make dev-up

# 4. Admin UI (separate, on the host)
cd ../apps/admin-ui && pnpm install && pnpm dev      # http://localhost:5173
```

`make dev-up` prints all addresses (`make dev-info`). Defaults: admin-ui `:5173`
(SSO, `dev` / `devpass`), control-plane `:8000`, Keycloak `:8080`, Langfuse `:3001`,
MinIO `:9001`, Grafana `:3000`. See [infra/README.md](infra/README.md).

## 📦 Deployment

**The single source for deploying Expert Work is the
[Deployment Manual](docs/runbooks/deployment.md)** — it covers, end to end:

- Architecture & topology, prerequisites, config sources, key env vars
- **First-time deploy** — local dev/dogfood and staging/prod
- **Creating the first platform admin** via the in-app setup wizard (no IdP console)
- **Update deploy** — blue/green rolling release with optional canary
- **Rollback**, expand-contract migrations, post-deploy verification
- Observability bring-up, backup/restore, and a release checklist

Deep dives it links to: [Postgres](docs/runbooks/postgres.md),
[TLS certs](docs/runbooks/tls-certs.md),
[bootstrap admin](docs/runbooks/bootstrap-admin.md),
[SLO](docs/runbooks/slo.md).

## 🗂 Repository layout

```
expert-work/
├── apps/            # frontends (admin-ui — React/TS console)
├── services/        # deployable services (control-plane, sandbox-supervisor,
│                    #   credential-proxy, orchestrator, + workers/jobs)
├── packages/        # reusable libs (expert-work-protocol / -persistence / -runtime /
│                    #   -common, under the expert_work.* namespace)
├── infra/           # local dev stack (compose), Makefile, env template
├── environments/    # per-env structured config (dev / staging / prod .yaml)
├── manifests/       # declarative agent manifests (YAML)
├── tools/           # CLI, eval harness, deploy/rollback scripts
├── tests/           # cross-package suite + fixtures
└── docs/            # architecture, ADRs, stream designs, runbooks, iteration plan
```

Packages/services are created just-in-time as their Stream starts —
[docs/architecture/03-MONOREPO-LAYOUT.md](docs/architecture/03-MONOREPO-LAYOUT.md).

## 📚 Documentation

| Topic | Doc |
|-------|-----|
| Architecture overview | [docs/architecture/00-OVERVIEW.md](docs/architecture/00-OVERVIEW.md) |
| Milestone / iteration plan | [docs/ITERATION-PLAN.md](docs/ITERATION-PLAN.md) |
| **Deployment manual** | [docs/runbooks/deployment.md](docs/runbooks/deployment.md) |
| Local getting started | [docs/runbooks/getting-started.md](docs/runbooks/getting-started.md) |
| First platform admin | [docs/runbooks/bootstrap-admin.md](docs/runbooks/bootstrap-admin.md) |
| ADRs (decisions) | [docs/adr/](docs/adr/) |
| Stream designs | [docs/streams/](docs/streams/) |
| Runbooks (ops) | [docs/runbooks/](docs/runbooks/) |

## 🛠 Development

```bash
uv run pytest -m "not integration"           # unit suite (integration needs the infra stack)
uv run ruff check && uv run ruff format --check
uv run mypy packages                          # type check (CI also checks select services/src)

pnpm -C apps/admin-ui typecheck && pnpm -C apps/admin-ui test   # frontend
```

Integration tests use testcontainers (real Postgres) — bring up Docker first.
See [docs/architecture/03-MONOREPO-LAYOUT.md](docs/architecture/03-MONOREPO-LAYOUT.md)
for workspace conventions.

## License

[GNU AGPL-3.0](LICENSE) — copyleft with a network clause: if you run a modified
Expert Work as a network service, you must offer users that service's source under the
same license.
