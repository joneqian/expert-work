# Sandbox Supervisor (Stream F.1)

Manages the lifecycle of `exec_python` sandbox containers — `acquire`,
`release`, `destroy` — behind an internal HTTP API the control-plane
(orchestrator) calls. STREAM-F-DESIGN § 2.1 / § 4.1.

M0 is the **cold-start** version (Mini-ADR F-4): every `acquire` is a
fresh `docker run`, every `release` a `docker rm -f`. No warm pool —
that is M1-A. The state machine is `CREATING → IN_USE → DESTROYED`
(`FAILED` on a launch error).

## HTTP API

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/v1/sandboxes:acquire` | Quota-check + launch a sandbox; returns `AcquireResponse` |
| POST | `/v1/sandboxes/{id}:release` | Routine teardown — `204` |
| POST | `/v1/sandboxes/{id}:destroy` | Forced teardown with a reason — `204` |
| GET | `/v1/health` | Liveness + Docker-daemon reachability |

## Run

```bash
uvicorn sandbox_supervisor.main:app
```

Settings come from `HELIX_SANDBOX_*` env vars (see `settings.py`):
`HELIX_SANDBOX_OCI_RUNTIME` (`runc` dev / `runsc` prod),
`HELIX_SANDBOX_SANDBOX_IMAGE`, `HELIX_SANDBOX_DB_DSN`, reaper tuning.

## TTL reaper

A background task sweeps every `reaper_interval_s`, force-destroying
`IN_USE` sandboxes whose `acquired_at` is older than
`timeout_s + reaper_grace_s` — the safety net for a caller that crashed
before `release` (STREAM-F-DESIGN § 2.7).
