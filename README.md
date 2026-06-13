# Helix-Agent

Business-agnostic, multi-tenant **agent platform** for running per-user
persistent agents. Each user gets a durable agent instance — its own
conversation history, long-term memory, and persistent workspace — executing
on the Anthropic **Brain-Hands-Session** paradigm: a stateless harness loop
(LangGraph), an isolated execution sandbox (Docker / gVisor), and an
append-only Postgres event log as the single source of truth.

The engine is not bound to any business domain. Compliance posture, PII
fields, domain prompts/tools, and isolation strength are tenant-level,
pluggable concerns — the core stays generic. (It began as a replacement for a
Dify deployment, then generalized into a standalone platform.)

> **Status**: Active development. M0 (product-grade MVP) closeout complete;
> hardening toward M1. See [docs/ITERATION-PLAN.md](docs/ITERATION-PLAN.md)
> for live milestone status (M0 → M1 → M2 → M3) and
> [docs/architecture/00-OVERVIEW.md](docs/architecture/00-OVERVIEW.md) for the
> design.

## Capabilities

- **Per-user persistent agents** — durable per (tenant, user): conversation,
  long-term memory, and a persistent `/workspace`.
- **Multi-tier memory** — working window + task-level plan state + episodic +
  long-term, with hybrid retrieval (pgvector + BM25 fused via RRF, temporal
  decay, MMR, optional cross-encoder rerank) and a background consolidator.
- **Context engineering** — LLM-free working-memory window, LLM-backed
  context compressor (summarise-the-middle), recoverable tool-output overflow,
  error-as-guidance recovery advisories, and event-driven effort escalation.
- **Agent Skills** — Anthropic Agent Skills spec (SKILL.md + YAML frontmatter)
  with a platform catalog, lazy progressive disclosure, and threat scanning.
- **MCP client** — consumes external MCP servers (GitHub / Postgres / Linear /
  …); platform catalog + per-tenant instantiation. Helix is a client, not a
  server.
- **Sandbox isolation** — per-session Docker / gVisor sandbox; filesystem,
  network, and subprocess isolation managed by a sandbox supervisor.
- **Multi-tenant governance** — Postgres RLS, OIDC/JWT via Keycloak, audit
  log, platform-centralized LLM/embedding config, plus metering & billing
  rollups.
- **Admin UI** — product-grade React console for agents, runs, memory,
  skills, and tenant administration.
- **Observability** — structured logs with redaction, W3C trace propagation
  across trusted hops, and an as-built Prometheus metric catalog.

## Repository layout

```
helix-agent/
├── apps/            # frontends (admin-ui — React/TS console)
├── services/        # deployable services (orchestrator, control-plane,
│                    #   sandbox-supervisor, credential-proxy, + workers/jobs)
├── packages/        # reusable libs (helix-protocol / -persistence /
│                    #   -runtime / -common, under the helix_agent.* namespace)
├── infra/           # local dev stack (Postgres + PgBouncer + MinIO)
├── manifests/       # declarative agent manifests (YAML)
├── configs/         # environment / deployment config
├── tools/           # CLI, eval harness, dev tooling
├── tests/           # cross-package suite + fixtures
└── docs/            # architecture, ADRs, stream designs, iteration plan
```

Packages and services are created just-in-time as their Stream starts — see
[docs/architecture/03-MONOREPO-LAYOUT.md](docs/architecture/03-MONOREPO-LAYOUT.md).

## Quick start

Backend (Python 3.12+, [uv](https://docs.astral.sh/uv/) workspace):

```bash
uv sync                              # create .venv, install workspace
uv run pytest -m "not integration"   # unit suite (integration needs the infra stack)
uv run ruff check && uv run ruff format --check
uv run mypy packages                 # type check (CI also checks select services/src)
```

Local infra stack (Postgres / PgBouncer / MinIO):

```bash
cd infra && docker compose up -d     # see infra/README.md
```

Admin UI (pnpm):

```bash
cd apps/admin-ui && pnpm install && pnpm dev
```

## License

Apache-2.0
