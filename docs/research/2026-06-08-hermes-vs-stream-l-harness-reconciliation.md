# hermes.yaml ↔ Stream L Harness Reconciliation

> Date: 2026-06-08 · Stream SE (SE-15) one-shot research artifact

## Purpose

The external `agentic-harness-engineering` repo ships a reusable skill
whose `profiles/hermes.yaml` describes the Hermes harness as seven
weighted components. Stream L ("Hermes-derived sprint") already lifted
Hermes's single-turn mature capabilities into expert_work. This table
reconciles **hermes.yaml's component profile + Stream L's eight items**
against Expert Work's actual implementation, to (a) prove Stream L closed the
Hermes gap and (b) isolate what is genuinely missing.

Source read: `agentic-harness-engineering/skills/agentic-harness-engineering/profiles/hermes.yaml`
and `references/HARNESS.md`.

## Reconciliation

| Hermes capability (profile component / Stream L item) | Expert Work implementation | Status |
|---|---|---|
| System Rules / Tool Desc / Tool Impl / Middleware / Skills / Sub-Agents / Long-Term Memory (7 components) | `AgentSpec` fields + assembly (declarative vs file tree) | ✅ all present (Skills exceeds: self-evolution) |
| L1 Prompt caching | `ModelSpec.cache_enabled` + `_apply_cache_control` | ✅ |
| L2 Context compression | `ContextCompressor` | ✅ |
| L3 Stream stale-detection | `_invoke_with_deadline` + `stream_deadline_s` | ✅ |
| L4 Mutation verifier footer | `mutation_classifier` + `<mutation-advisory>` | ✅ |
| L5 Iteration budget refund | `ToolResult.refund_iterations` | ✅ |
| L6 Adaptive tool parallelization | `scheduling.plan_stages` | ✅ |
| L7 Trajectory recording | `orchestrator.trajectory` (also the SE distillation input) | ✅ |
| L8 OAuth 401 auto-refresh | `OAuthCapableProvider` + `_handle_unauthorized` | ✅ |
| Change Manifest evolution observability (failure_evidence + root_cause + predicted_impact + falsify) | Stream SE `skill_eval_result` + SE-4 replay + SE-5b attribution (statistical pairing — **stronger** than predicted-impact assertions) | ✅ exceeds |
| harness compliance linter (`validate_harness.py`) | — | ❌ → **SE-15** (this sprint) |
| cross-form harness spec doc (`HARNESS.md`) | — | ❌ → **SE-15** (this sprint) |
| Best-of-N candidate diversity | co-evolve is sequential refinement only | ❌ → **SE-14** |
| layered source-linked failure report (Agent Debugger) | attribution is coarse, no layered report | ❌ → **SE-12** |

## Conclusion

The Hermes 7-component profile + Stream L's 8 single-turn capabilities +
the HARNESS.md Change-Manifest loop are **all already implemented or
exceeded** in expert_work. The HARNESS.md Change Manifest (predicted-impact
assertions, falsified next iteration) is covered by Stream SE in a
*stronger* form — `skill_eval_result` + the SE-4 replay gate carry
statistical-pairing evidence rather than self-asserted predictions, so
the manifest schema is not worth porting verbatim.

The four real gaps map cleanly onto the new SE backlog items:
**SE-12** (failure report), **SE-14** (Best-of-N), **SE-15** (this
sprint: linter + spec), plus the broader evolution-scope (**SE-10**) and
prediction-discipline (**SE-11**) items borrowed from the same external
analysis.
