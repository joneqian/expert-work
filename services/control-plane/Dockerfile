# Expert Work control-plane image — Stream I.1.
#
# Reuses the multi-stage uv build pattern established by the
# credential-proxy pilot (Mini-ADR F-15 / I-1). The control-plane runs
# the orchestrator in-process as a library (STREAM-E-DESIGN § 2.6), so
# this single image carries the whole M0 API + execution surface.
#
# The Alembic config + migrations dir are copied in as well: the
# docker-compose ``migrate`` one-shot service reuses this image to run
# ``alembic upgrade head`` (Mini-ADR I-3) — they live outside the
# package ``src/`` tree, so the ``--no-editable`` wheel doesn't carry them.
#
# Build from the repo root:
#   docker build -f services/control-plane/Dockerfile -t expert-work-control-plane .

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY packages/ packages/
COPY services/ services/
RUN uv sync --frozen --no-dev --no-editable --package expert-work-control-plane


FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"
WORKDIR /app

# Non-root service account — parity with the sandbox / credential-proxy
# image hardening.
RUN useradd --uid 10002 --no-create-home --shell /usr/sbin/nologin expert_work

COPY --from=builder --chown=expert_work:expert_work /app/.venv /app/.venv

# Alembic assets for the compose ``migrate`` service. ``alembic.ini`` has
# ``script_location = %(here)s/migrations``, so this layout resolves to
# /app/alembic/migrations. The migrate service overrides ``entrypoint``
# to ``alembic -c /app/alembic/alembic.ini upgrade head``.
COPY --chown=expert_work:expert_work packages/expert-work-persistence/alembic.ini /app/alembic/alembic.ini
COPY --chown=expert_work:expert_work packages/expert-work-persistence/migrations /app/alembic/migrations

# Eval harness (``tools/eval``) for the optional eval worker — the M0
# baseline suite + the 11.4/11.5 live adversarial/trace suites. It lives
# outside the package ``src/`` tree (the ``--no-editable`` wheel doesn't
# carry it), so ship it onto PYTHONPATH; the worker's lazy
# ``from tools.eval...`` imports + dataset loads then resolve in-container.
COPY --chown=expert_work:expert_work tools/eval /app/tools/eval
ENV PYTHONPATH=/app

# MCP connector catalog seed template (Stream MCP-OAUTH OA-5). Lives outside
# the package ``src/`` tree, so the ``--no-editable`` wheel doesn't carry it.
# Ship it so ``mcp_catalog_seed_file=configs/mcp-catalog-seed.json`` (relative
# to WORKDIR /app) resolves at startup; unset → seeding is a no-op.
COPY --chown=expert_work:expert_work configs /app/configs

USER expert_work
EXPOSE 8000
ENTRYPOINT ["uvicorn", "control_plane.main:app", "--host", "0.0.0.0", "--port", "8000"]
