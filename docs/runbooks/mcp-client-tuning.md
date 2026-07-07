# MCP Client Tuning & Triage

Runbook for the Capability Uplift Sprint #5 MCP client HTTP/SSE transport.
Used by operators when alerts fire on `Expert Work:uplift:mcp_*` rules or when
an agent reports a missing / failing `mcp:<server>.<tool>` call.

> **Scope**:Expert Work is **MCP client only** (Mini-ADR永久 — see
> [memory:mcp-direction-client-only]). This runbook covers consuming
> external MCP servers; Expert Work does not expose an MCP server endpoint.

## § 1 Transport selection guide

| Server form | `transport` | When to pick |
|------------|-------------|------------|
| Local subprocess (`npx @modelcontextprotocol/server-filesystem /data`) | `stdio` | Operator-controlled local tools; no network exposure |
| Legacy SSE endpoint (older MCP servers, anthropic `mcp-server-time` SSE flavour) | `sse` | Server explicitly documents SSE mode |
| Modern bidirectional HTTP (GitHub MCP, most 2026+ public servers) | `streamable_http` | Default for any remote MCP server unless docs say otherwise |

Unknown transport in `mcp_servers_config_file` → boot fails fast. The
JSON file is operator-controlled (Mini-ADR E-17); per-tenant manifests
can only **enable / filter** entries from the central pool — they cannot
add new URLs or change transports. This is by design to prevent tenant-
injected exfiltration targets.

## § 2 Failure-mode triage

Alert `ExpertWorkUpliftMCPCallFailureRateSpike` fires → check the per-server
breakdown:

```promql
sum by (transport, server, result) (
  rate(expert_work_uplift_mcp_call_total[15m])
)
```

`result` decoding:

| result | Meaning | First check |
|--------|---------|------------|
| `ok` | Successful round-trip | — |
| `timeout` | Exceeded `timeout_s` (default 30s) | Network latency to remote endpoint; raise timeout in config if legitimate slow tool |
| `transport_err` | SDK raised before / after the call (connection refused, mid-stream close, protocol violation) | Remote endpoint health; check `curl <url>` from a control-plane shell |
| `circuit_open` | Breaker tripped, short-circuiting | See § 5 — server is already known unhealthy |
| `4xx` / `5xx` | HTTP status from remote (currently surfaces as `transport_err`; reserved for future granularity) | Auth misconfig / remote down |

Common roots:

1. **Remote auth rotated** → bearer token in secret store stale. Update
   the `secret://` value and restart control-plane (no per-server
   reconnect API yet — that lands when M1 adds tenant config validation).
2. **Public MCP server deprecated** → replace URL in
   `mcp_servers_config_file` or remove entry.
3. **Network egress blocked** → in K8s, check NetworkPolicy / egress
   allowlist for the host.

## § 3 Secret / bearer auth configuration

`mcp_servers_config_file` entry:

```json
{
  "name": "github",
  "transport": "streamable_http",
  "url": "https://api.githubcopilot.com/mcp/",
  "auth_type": "bearer",
  "auth_config": {"token_ref": "secret://mcp/github/api-token"}
}
```

The `token_ref` value points at the Expert Work secret store (Mini-ADR U-11);
the actual token never appears in:

- the config file (which sits in git)
- log lines (`MCPServerConfig.__repr__` redacts `headers` + `auth_config`)
- audit rows (tool-call audit records the namespaced tool name, not
  request headers)

To rotate:

```bash
# Update the secret in your backend (local-dev: .env entry).
# Restart control-plane to pick up the new value.
```

To verify a token resolves correctly at boot, watch the control-plane
log for `expert_work_uplift_mcp_call_total{result="ok"}` activity on the
target server within the first minute after startup; absence + alerts
on `transport_err` typically means a stale or wrong-scope token.

## § 4 OAuth 2 configuration (status: schema only)

`auth_type: "oauth2"` is **accepted by the schema but not implemented**
(Mini-ADR U-12 — the full flow lands in the follow-up Mini-ADR L.L8-MCP
sprint). If you configure it, boot fails fast with:

```
MCPOAuthNotImplementedError: mcp server 'linear': oauth2 auth flow not
implemented in this release — see Mini-ADR L.L8-MCP. Switch to
auth_type="bearer" with a token_ref or remove the server.
```

Until L.L8-MCP ships, options for OAuth-requiring servers:

1. **Pre-mint a long-lived bearer token** via the server's admin UI and
   wire it via `auth_type: "bearer"` + `token_ref`.
2. **Skip the server** — drop it from `mcp_servers_config_file`.

Do not attempt to wire OAuth client_id / scope hoping the flow will
work — `_build_mcp_client` raises before the pool finishes booting.

## § 5 Circuit breaker state & manual reset

Mini-ADR U-13: per-server breaker trips after 5 consecutive failures
within a 30-minute window. Once open, all calls to that server are
rejected for the cool-down. After the window elapses, the breaker
half-opens — the next call is a probe; success closes, failure re-opens.

Check current state via the transition counter:

```promql
# How many servers have been open in the last 30m?
Expert Work:uplift:mcp_circuit_open_total

# Transition timeline for a specific server
expert_work_uplift_mcp_circuit_state_total{server="github"}
```

**Manual reset** is not exposed via API in this sprint (intentional —
operators should fix the root cause and let the breaker recover via
its natural half-open probe). If you must force-recover before the
window expires, restart the control-plane process: breaker state is
in-memory and resets at boot.

If a breaker repeatedly trips on a server that's actually healthy,
investigate:

- Transport mismatch (wrong `transport` value for the endpoint)
- TLS / cert issue (check control-plane log for SSL exceptions)
- Rate limiting on the remote endpoint (in which case raise
  `timeout_s` or remove the server)

## § 6 Adding a new remote MCP server

1. **Find an authoritative URL** for the server (vendor docs; do not
   trust unknown listings).
2. **Determine transport** — most 2026+ servers are `streamable_http`;
   only fall back to `sse` if docs say so.
3. **If auth required** — pre-mint a bearer token, store in secret
   backend as `mcp/<vendor>/<purpose>`, reference via
   `auth_config.token_ref: "secret://mcp/<vendor>/<purpose>"`.
4. **Append to `mcp_servers_config_file`** — operator-controlled, PR'd
   like any other config. Keep entries lowercase ASCII (tools registry
   namespaces as `mcp:<server>.<tool>`).
5. **Restart control-plane** — startup either succeeds (server appears
   in tool registry) or fails fast with a clear error.
6. **Verify** by watching the first 5 minutes of
   `rate(expert_work_uplift_mcp_call_total[5m])` on the new server name.

`max_servers=5` per pool is the platform cap (Stream E.9 § 6) — coordinate
with the platform team before adding the 6th MCP server.
