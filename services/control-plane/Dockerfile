# Helix control-plane image — Stream I.1.
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
#   docker build -f services/control-plane/Dockerfile -t helix-control-plane .

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY packages/ packages/
COPY services/ services/
RUN uv sync --frozen --no-dev --no-editable --package helix-agent-control-plane


FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"
WORKDIR /app

# Non-root service account — parity with the sandbox / credential-proxy
# image hardening.
RUN useradd --uid 10002 --no-create-home --shell /usr/sbin/nologin helix

COPY --from=builder --chown=helix:helix /app/.venv /app/.venv

# Alembic assets for the compose ``migrate`` service. ``alembic.ini`` has
# ``script_location = %(here)s/migrations``, so this layout resolves to
# /app/alembic/migrations. The migrate service overrides ``entrypoint``
# to ``alembic -c /app/alembic/alembic.ini upgrade head``.
COPY --chown=helix:helix packages/helix-persistence/alembic.ini /app/alembic/alembic.ini
COPY --chown=helix:helix packages/helix-persistence/migrations /app/alembic/migrations

USER helix
EXPOSE 8000
ENTRYPOINT ["uvicorn", "control_plane.main:app", "--host", "0.0.0.0", "--port", "8000"]
