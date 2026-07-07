# orchestrator

Expert Work orchestrator service. Owns the LangGraph execution surface — compiles
agent state graphs against a shared checkpointer so that runs are durable across
process restarts.

Implements **Stream E** per [docs/streams/STREAM-E-DESIGN.md](../../docs/streams/STREAM-E-DESIGN.md).

## Sub-PR scope (Stream E roadmap)

| PR | Capability |
|----|-----------|
| **E.1 (this PR)** | `GraphRunner` + `AgentState` + LangGraph saver wiring (memory + Postgres backed) |
| E.2 | `MiddlewareChain` (@Next/@Prev anchor system) — wired into `GraphRunner` constructor |
| E.3–E.5 | `dynamic_context` / `llm_error_handling` / Langfuse middlewares |
| E.6 | ReAct graph builder (single-agent) |
| E.7–E.9 | Tools: web_search, HTTP, MCP |
| E.10 | `sandbox_audit_middleware` |
| E.11–E.13 | LLM router (fallback chain) + provider rate limiting + response cache |
| E.14 | SSE streaming + backpressure |
| E.15 | Cancellation token propagation |

Each later PR widens the `GraphRunner.__init__` surface — current PR only requires
`checkpointer`.

## Tests

```bash
uv run pytest services/orchestrator/tests/
uv run pytest services/orchestrator/tests/ -m integration   # requires Docker
```

## Design references

- [docs/streams/STREAM-E-DESIGN.md](../../docs/streams/STREAM-E-DESIGN.md)
- [packages/expert-work-runtime/src/expert_work/runtime/checkpointer/factory.py](../../packages/expert-work-runtime/src/expert_work/runtime/checkpointer/factory.py) — A.2 saver factory wrapped by `GraphRunner`
- [ADR-0002 — state-layer schema](../../docs/adr/0002-state-layer-schema.md)
