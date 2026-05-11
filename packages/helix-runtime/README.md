# helix-agent-runtime

Runtime infrastructure layer. Most modules here are **borrowed-and-adapted**
from [bytedance/deer-flow](https://github.com/bytedance/deer-flow) per
[06-OPEN-SOURCE-DEPS](../../docs/architecture/06-OPEN-SOURCE-DEPS.md) §P0.

Sub-packages:

| Module | Stream | Status |
|--------|--------|--------|
| `runtime/event_log/` | A.2 (this PR) | ✅ EventStore protocol + InMemory + Postgres impls |
| `runtime/checkpointer/` | A.2 later | ⏳ |
| `runtime/store/` | A.2 later | ⏳ |
| `runtime/stream_bridge/` | A.2 later | ⏳ |
| `runtime/runs/` | A.2 later | ⏳ |
| `runtime/context.py` | A.2 later (Stream C wires tenant_id contextvar) | ⏳ |

## Vendoring strategy

Per [06 §P0 revised 2026-05-11](../../docs/architecture/06-OPEN-SOURCE-DEPS.md),
we keep DeerFlow's **algorithms** (FOR UPDATE seq allocation, batch writes,
content truncation, etc.) but adopt **our own interface** aligned with
[ADR-0002](../../docs/adr/0002-state-layer-schema.md) schema (`thread_id` +
`tenant_id` UUID + `payload` + `trace_id`).

Every borrowed module has a provenance header citing the DeerFlow source +
commit SHA + summary of changes.
