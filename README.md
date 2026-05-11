# Helix-Agent

Business-agnostic, multi-tenant enterprise Agent execution engine — replaces
Dify with a thin, composable orchestration engine on the Anthropic
Brain-Hands-Session paradigm (LangGraph + Docker/gVisor sandbox + Postgres
append-only event log).

> **Status**: Phase 0 bootstrap. See [docs/ITERATION-PLAN.md](docs/ITERATION-PLAN.md)
> for the full development roadmap (Phase 0 → M0 → M1 → M2 → M3).

## Quick start

```bash
# Install dependencies (creates .venv via uv)
uv sync

# Run tests
uv run pytest

# Lint + format
uv run ruff check
uv run ruff format

# Type check
uv run mypy packages
```

## Architecture

See [docs/architecture/00-OVERVIEW.md](docs/architecture/00-OVERVIEW.md) and
the [iteration plan](docs/ITERATION-PLAN.md). High-level layout:

```
helix-agent/
├── packages/        # reusable Python libraries (helix_agent.* namespace)
├── services/        # deployable services (created just-in-time per Stream)
├── tools/           # CLI and dev tools
├── tests/           # cross-package test suite + fixtures
├── environments/    # dev/staging/prod config
├── deploy/          # docker-compose + k8s manifests
└── docs/            # architecture, ADRs, decisions, iteration plan
```

Per [docs/architecture/03-MONOREPO-LAYOUT.md](docs/architecture/03-MONOREPO-LAYOUT.md),
packages and services are created just-in-time when their respective Stream
starts — not upfront.

## License

Apache-2.0
