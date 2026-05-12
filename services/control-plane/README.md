# helix-agent-control-plane

Helix-Agent **Control Plane** — the user-facing FastAPI surface that owns
manifest CRUD, session lifecycle, and run trigger. Implementation lands
through [Stream B](../../docs/streams/STREAM-B-DESIGN.md).

## B.1 scope (this milestone)

- FastAPI app factory `control_plane.app.create_app`
- Pydantic v2 `BaseSettings` (`HELIX_AGENT_*` env)
- Lifecycle / health probe wiring (`/healthz/{live,ready,startup}`)
- `/metrics` Prometheus endpoint (consumes the registry installed by Stream A.9)
- Observability middleware — W3C trace context + structured logging
  context + request counter / latency histogram
- Audit-context middleware — header-based `tenant_id` / `actor_id` in
  `HELIX_AGENT_AUTH_MODE=dev` (per ADR B-5; `prod` mode startup guard
  refuses until C.1 OIDC lands)
- Lifecycle in-flight tracker

Later milestones (B.2 – B.7) layer rate-limit middleware, cancellation
middleware, manifest loader + AgentSpec schema, and the
agents / sessions / runs CRUD on top of this skeleton.

## Run locally

```bash
uv run --package helix-agent-control-plane \
    uvicorn control_plane.main:app --host 0.0.0.0 --port 8080
```

Default settings target the `infra/docker-compose.yml` PgBouncer
(localhost:6432); override via the `HELIX_AGENT_*` env block in
`environments/dev.yaml`.
