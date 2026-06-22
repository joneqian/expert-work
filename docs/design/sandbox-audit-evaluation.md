# Enforcement & audit-rule evaluation — necessary security vs. over-blocking

**Principle (operator-set):** This is an enterprise platform, but beyond the
*necessary* security measures we must not degrade capability or smoothness with
excess restriction. Prefer **audit-trail traceability** over up-front blocking.
(Same principle that drove sandbox egress: allow + audit, don't wall.)

This doc evaluates every enforcement/blocking rule across the platform (Tool-
and Agent-level), classifies each as **necessary / over-block / redundant**, and
records the change set. It is the rationale behind removing the sandbox-exec call
denylist (Phase 1 below).

## 1. What the real security boundary is

The controls that genuinely contain a hostile/injected agent — keep all:

- **Sandbox isolation** — gVisor `runsc` (prod) + read-only rootfs, `cap-drop
  ALL`, `no-new-privileges`, `pids`/`memory`/`cpu` caps, tmpfs `/workspace`,
  non-root uid 10000, proxy-only egress network, pinned seccomp. Kernel/runtime
  enforced, **not LLM-bypassable**. *This* is the boundary for sandbox code.
- **Egress** — transparent CONNECT proxy: SSRF block + DNS-rebind IP-pin +
  private/loopback/metadata refusal + per-sandbox HMAC token + signed allowlist,
  every connection audited (`sandbox_egress_audit`). (docs: sandbox-egress-per-agent)
- **URL-template host-pivot guard** — reject structural chars before a tenant
  param is `format()`-ed into a URL template (stops authority pivot).
- **Multi-tenant authz** — RBAC `require`/`is_allowed` (deny-by-default) + ABAC
  instance conditions + cross-tenant scope block + system_admin-only credential
  writes. All audited.
- **Irreversible-action approval** — `approval_required_tools` (declared) +
  auto-union of `side_effect="irreversible"` tools → human-in-the-loop pause.
- **Injection (treatment layer)** — PI-1 spotlight (annotate-only, no block) +
  PI-2 output screen (credential/exfil shapes, high precision).
- **Skill import structural rejection** — path traversal / symlink / zip-bomb /
  size & entry caps (U-18).

## 2. Over-blocking (relax to audit / warn)

| rule | problem | action |
| --- | --- | --- |
| `sandbox_audit` `_DENIED_PYTHON_CALLS` (`subprocess.*`, `os.system`, `eval`, `exec`, `fork`, …) | **Theater + capability-killer.** Bypassed by the `bash` tool (not in the audited set, and it *is* `subprocess.run(shell=True)`) and by trivial AST evasions (`from subprocess import run`, `getattr`, aliases). The gVisor sandbox is the real boundary. Cost: blocks the office image's `soffice`/`poppler` invocation — which, because each tool call is a fresh tmpfs, **must** happen via `subprocess` inside one `exec_python` call. | **Phase 1 (this doc): removed.** Record submitted code into the tool audit instead. |
| `_DENIED_SHELL_SUBSTRINGS` + the `shell` branch | **Dead code** — no tool is named `shell` (the real one is `bash`, not audited). | Removed with the middleware. |
| egress `_ip_is_blocked` `is_reserved` | Sweeps in 198.18.0.0/15 (RFC2544) and CGNAT 100.64/10 — broader than the metadata/RFC1918 threat. | Phase 2: narrow. |
| `scan_for_threats scope="strict"` on **user/operator-authored** content (memory write, recalled memory, trigger seeds) | strict patterns (`authorized_keys`, `cat .env`, `you are now a`) fire on legitimate devops/security content → hard-block / silent-drop. The fire-time scan already chose the saner `warn` default. | Phase 3: warn + audit; add the missing `skill_seed` audit row. |

## 3. Redundant / inert (no real gate today)

- **PI-2b output judge / PI-3b action judge / output DLP** — default-off and
  fail-open; not load-bearing. Leave opt-in (a judge is itself injectable; never
  the primary defense).
- **IMDS shell substrings** — redundant with the egress proxy SSRF block (which
  refuses metadata even via `subprocess`); also in the dead branch.
- **`rate_limit_override`** — persisted in `tenant_config` but consumed by no
  limiter. Wire or remove (tech debt, not security).

## 4. Not touched (necessary or deliberate)

RBAC / ABAC / tenant isolation / irreversible-approval (necessary). Tier
entitlement gating + credential platform-exclusivity (Y-1) are **commercial
governance the operator already chose**, not safety over-blocks — out of scope.

## 5. Audit-trail is the substitute — so it must exist

"Audit over blocking" only holds if the action is *traceable*. Gaps found:

- **Sandbox exec code was not recorded** (`_emit_tool_audit` logged arg *names*
  only). **Phase 1 fixes this**: `exec_python`/`bash` now record a capped code
  preview + full-content `sha256` + byte size in the tool-call audit row.
- (Phase 3/4) `skill_seed` drops files with only a log; egress 407/405 and
  output-screen blocks emit metrics but no durable audit row — to be closed.

## 6. Change set (phased)

1. **Phase 1 (this PR):** delete the `sandbox_audit` blocking middleware (denylist
   + dead shell branch) and its wiring/exports/tests; record sandbox exec code in
   the tool audit (`_SANDBOX_CODE_ARGS`, preview cap + sha256). Unblocks the
   office image (`soffice` via `subprocess`); every run stays traceable.
2. Phase 2: narrow `_ip_is_blocked` (drop the over-broad reserved sweep).
3. Phase 3: `strict`→`warn`+audit on user/operator-authored scan surfaces; add
   `skill_seed` audit rows.
4. Phase 4: close audit-trail gaps (egress 407/405, output-screen durable rows).
5. Phase 5: remove dead code / wire-or-drop `rate_limit_override`.
