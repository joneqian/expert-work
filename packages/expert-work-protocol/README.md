# expert-work-protocol

Cross-service Pydantic schemas. Single source of truth for inter-service contracts
so Control Plane / Orchestrator / Sandbox Supervisor / Admin UI all agree on shapes.

Modules (Stream A.1):

- `event` — `EventType`, `EventRecord` (event_log row shape)
- `audit` — `AuditAction`, `AuditEntry`, `AuditQuery`
- `thread_meta` — `ThreadStatus`, `ThreadMeta`

Future Stream additions:

- `agent_spec` (Stream B.4 manifest schema)
- `session_event` (Stream E.7 SSE chunk shape)

See [docs/architecture/03-MONOREPO-LAYOUT.md](../../docs/architecture/03-MONOREPO-LAYOUT.md)
and [ADR-0002](../../docs/adr/0002-state-layer-schema.md).
