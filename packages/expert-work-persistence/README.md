# expert-work-persistence

SQLAlchemy 2.0 (async) ORM models + Alembic migrations for the Expert Work
state layer.

Tables (Stream A.1):

- `event_log` — append-only session events ([ADR-0002](../../docs/adr/0002-state-layer-schema.md))
- `thread_meta` — LangGraph thread lifecycle (vendor-aligned per
  [06-OPEN-SOURCE-DEPS](../../docs/architecture/06-OPEN-SOURCE-DEPS.md) §P0)
- `audit_log` — admin / compliance operations ([subsystems/17](../../docs/architecture/subsystems/17-audit-log.md))

LangGraph's PostgresSaver manages its own `checkpoints` / `checkpoint_blobs` /
`checkpoint_writes` tables — those land via Stream A.2 vendor.

RLS policies (`tenant_id` isolation) are defined but **disabled** in A.1
migrations; Stream C.4 turns them on once Stream B/C auth context is wired.

## Run migrations locally

```bash
# Against the dev Postgres in docker-compose (Stream A.2+)
uv run alembic -c packages/expert-work-persistence/alembic.ini upgrade head
```

## Integration tests

Use the session-scoped `postgres_container` fixture (Phase 0.5). Each test
fresh-applies migrations.
