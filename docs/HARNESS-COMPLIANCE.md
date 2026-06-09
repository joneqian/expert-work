# helix Harness Compliance (Stream SE — SE-15)

> Status: active · Owner: Stream SE · Lint: `tools/harness/check_harness_compliance.py`

## Why this exists

An agent's behaviour is shaped by far more than its base model — it is
shaped by its **harness**: the system rules, tool descriptions, tool
implementations, middleware, skills, sub-agents and long-term memory
that surround the model. The external research project
[agentic-harness-engineering](https://github.com/) frames a harness as
**seven orthogonal, version-controlled components** and shows that
evolving them (model frozen) is what moves benchmark pass rate.

helix already decomposes a harness into those same seven components —
but as a **declarative manifest** (`AgentSpec`) assembled into a
`BuiltAgent` at runtime, not as a workspace file tree. This document is
the helix-native restatement of that harness contract, plus the
machine-checkable rules that keep manifests honest.

This is a *spec + linter*, not a new subsystem. It does not change how
agents run; it raises the floor on how they are declared.

## The seven components ↔ `AgentSpec`

| Harness component (external HARNESS.md v1.0) | helix `AgentSpec` location |
|---|---|
| System Rules | `spec.system_prompt.template` + `spec.dynamic_context` |
| Tool Descriptions | `spec.tools[]` (builtin / http / mcp) + builtin registry |
| Tool Implementations | orchestrator `build_tool_registry` / `KNOWN_BUILTINS` |
| Middleware | orchestrator `build_middleware_chains` |
| Skills | `spec.skills[]` (+ Stream SE self-evolution) |
| Sub-Agents | `spec.subagents[]` (`SubAgentSpec`) |
| Long-Term Memory | `spec.memory.long_term` (`LongTermMemorySpec`) |

The mapping is deliberate: helix does **not** copy the external
directory layout (AGENTS.md / `tools/*.yaml` / …). A declarative
manifest is a different form factor; the *components* map, the file tree
does not (Mini-ADR SE-A34).

## Orthogonality contract

Each component owns one concern; the linter flags the common
cross-component leaks:

- **System Rules** carry behaviour, not implementation. Embedding a code
  block or an `exec_python(...)` call in `system_prompt.template` means
  tool-layer detail leaked into the rules layer.
- **Tool Descriptions** name tools the platform actually ships. A
  `builtin` whose `name` is not in `KNOWN_BUILTINS` is a description with
  no implementation behind it.
- **High-risk tools** (`exec_python`, `http`, … — `HIGH_RISK_TOOLS`)
  that an agent enables must be listed in
  `policies.approval_required_tools`. This is the manifest-layer mirror
  of the Stream SE hard guard "high-risk capability is always
  human-reviewed".

## Lint rules

Enforced by `tools/harness/check_harness_compliance.py`, wired into the
CI `lint` job (alongside the metric / RLS / audit lints). It scans
`manifests/**` for `kind: Agent` documents.

| Rule | Severity | Check |
|---|---|---|
| R1 | error | manifest parses as a valid `AgentSpec` |
| R2 | error | each `builtin` tool name ∈ `KNOWN_BUILTINS` |
| R3 | error | enabled `HIGH_RISK_TOOLS` ⊆ `policies.approval_required_tools` |
| R4 | warn | `metadata.name` is lowercase-kebab |
| R5 | warn | `system_prompt.template` has no embedded code |
| R6 | warn | no `vision:` block on a vision-capable `model` |
| R-prof | warn | profile-recommended optional blocks present |

Errors fail CI; warnings are advisory. The rules **complement** the
eight `model_validator` rules already baked into `AgentSpec` (network
wildcard, fallback DAG, subagent / skill / trigger naming + dedup) — the
linter only adds what those per-block validators cannot express
(cross-component, naming, compliance). See Mini-ADR SE-A35 for the
boundary.

R2 needs the orchestrator package (for `KNOWN_BUILTINS`). When it is not
importable (a minimal pre-commit env), the rule is **skipped with a
printed notice** rather than silently passing — CI always has the full
workspace synced, so it runs there (Mini-ADR SE-A36).

## Profiles

`tools/harness/helix_profile.yaml` adapts the lint to an agent *form
factor* — the same idea as the external `hermes.yaml` / `codex.yaml` /
`openclaw.yaml` profiles (different forms require different components).
helix has one form factor today (the declarative AgentSpec agent); the
profile's `recommend` list drives the advisory R-prof warning. The
external `validate_harness.py` (a file-tree auditor) is **not** wired
into helix CI — it does not match the manifest form factor (Mini-ADR
SE-A37).

## See also

- `docs/streams/STREAM-SE-DESIGN.md` § SE-F — design + Mini-ADRs SE-A34..A37
- `docs/research/2026-06-08-hermes-vs-stream-l-harness-reconciliation.md` — hermes.yaml ↔ Stream L gap reconciliation
