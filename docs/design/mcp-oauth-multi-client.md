# MCP OAuth — multi-client redirect (per-initiate redirect_uri)

Status: accepted (2026-06-24) · Stream MCP-OAUTH · extends
[STREAM-MCP-OAUTH-DESIGN.md](../streams/STREAM-MCP-OAUTH-DESIGN.md).

## Problem

Per-user OAuth lets each employee authorize a hosted MCP connector (Linear,
Notion, the tenant's own business system) with their own account, so the agent
sees only that employee's data. The OAuth authorize → callback round-trip needs
a **redirect URI**: where the provider sends the browser (with `?state&code`)
after the user consents.

Today that redirect URI is a **single global deployment value**
(`mcp_oauth_redirect_uri`). That only works when exactly **one** client drives
OAuth. We need more:

- **Three client forms** all initiate OAuth:
  1. **Web app** (admin-ui, or an employee-facing web client) — callback is an
     `https://…/callback` page.
  2. **Desktop / CLI** (native app) — RFC 8252 native-app flow: callback is a
     **loopback** URI (`http://127.0.0.1:<port>/callback`) the app listens on,
     or a **custom scheme** (`myapp://callback`).
  3. **Embedded** in the customer's own business system — callback is a page in
     that system.
- **Both admin-ui and the employee's independent client** initiate OAuth.

A single global redirect URI cannot serve all of these — the provider redirects
to one place. So the **client must supply its own redirect URI per initiate**,
and the backend must accept and validate it.

## Decision

Add a **per-initiate `redirect_uri`** parameter, validated against a configured
**allowlist**, persisted on the connection row, and reused verbatim at callback
for the token exchange.

### Why store it on the connection

OAuth 2.1 requires the `redirect_uri` sent at the token-exchange step
(`exchange_code`) to **exactly match** the one sent at authorize. The authorize
happens at `initiate`; the exchange happens at `callback` (a separate request,
possibly from a different client instance). So the value chosen at initiate must
be persisted and read back at callback — a new `redirect_uri` column on
`mcp_oauth_connection`.

### Allowlist (the security control)

`redirect_uri` is attacker-influenceable (the caller supplies it). An
unconstrained redirect is an **open-redirect / authorization-code exfiltration**
risk. The allowlist is the gate. Note the redirect URI is **never fetched
server-side** — it is where the *browser* goes — so SSRF guards do not apply; the
control we need is open-redirect prevention, not private-IP blocking.

`mcp_oauth_redirect_allowlist: list[str]` — each entry is a permitted redirect
**prefix**. A candidate is allowed iff one of:

- **Web (http/https)**: same `scheme` + `host` + `port` as an allowlist entry,
  and the candidate path starts with the entry's path. (Origin match + path
  prefix — blocks `https://good.example.com.evil.com/…` and bare-host swaps.)
- **Loopback** (RFC 8252, gated by `mcp_oauth_allow_loopback_redirect: bool`,
  default true): `scheme=http` and host ∈ {`127.0.0.1`, `::1`, `localhost`} —
  **port is ignored** (native apps bind an ephemeral port) but the path must
  still start with an allowed loopback entry's path (or any path when an entry
  is just `http://127.0.0.1`). Loopback is localhost-only, never remotely
  reachable.
- **Custom scheme**: an allowlist entry with a non-http scheme (`myapp://…`)
  matches by exact `scheme` (+ optional host/path prefix). For native apps that
  register a private-use URI scheme.

Anything else → `422 MCP_OAUTH_REDIRECT_NOT_ALLOWED`.

The existing global `mcp_oauth_redirect_uri` stays as the **default** when a
client omits `redirect_uri` (back-compat: admin-ui's current flow keeps working),
and is implicitly allowed.

### Defense in depth (residual risk)

Even if an attacker coerced a redirect to an allowlisted-but-attacker-controlled
page, the authorization code alone is not enough to steal the connection:

- **PKCE (S256)** — the `code_verifier` lives only server-side (on the
  connection); the code is useless without it.
- The code is **single-use** and short-lived.
- Our `callback` endpoint **requires the user's bearer token** and correlates by
  `state` to the user's own `pending` connection — an attacker cannot complete
  the exchange as the victim.
- The OAuth provider independently validates `redirect_uri` against the
  `client_id`'s registered redirects.

The allowlist is the primary control; these are the backstops.

## Flow per client form

```
initiate(redirect_uri=R)  →  validate R against allowlist  →  store R on connection
                          →  authorize_url(redirect_uri=R)  →  return to client
        ↓ browser/app navigates to authorize_url, user consents
provider → redirects to R with ?state&code
        ↓ client obtains state+code at R
callback(state, code)  →  load connection  →  exchange_code(redirect_uri=R from row)  →  store tokens
```

| Form | `redirect_uri` | How the client gets state+code | Calls `callback` |
|---|---|---|---|
| Web app | `https://app/…/callback` | Its callback page reads the query | with the user's bearer |
| Desktop / CLI | `http://127.0.0.1:<port>/cb` | Local loopback listener captures the query | with the user's bearer |
| Embedded | host system's callback URL | Host system reads the query | with the user's bearer |

The `callback` endpoint is identical for all three — only the redirect target
differs. (admin-ui supplies `${origin}/settings/mcp-oauth/callback`.)

## Implementation

1. **Settings** (`settings.py`): `mcp_oauth_redirect_allowlist: list[str] = []`,
   `mcp_oauth_allow_loopback_redirect: bool = True`. Keep
   `mcp_oauth_redirect_uri` as the omitted-default.
2. **Validation** (`control_plane/mcp_oauth.py` or a sibling): pure
   `validate_oauth_redirect(redirect_uri, *, allowlist, allow_loopback) -> str`
   raising `McpOAuthError("MCP_OAUTH_REDIRECT_NOT_ALLOWED", …)`.
3. **Migration** `0091_mcp_oauth_redirect_uri`: `ADD COLUMN redirect_uri TEXT NULL`.
4. **Protocol** (`mcp_oauth_connection.py`): `McpOAuthConnectionRecord.redirect_uri:
   str | None`; store `create()` gains `redirect_uri`.
5. **Persistence** (base/sql/memory): `create()` + row mapping.
6. **initiate** (`mcp_oauth_api.py`): optional body `{redirect_uri?}`; resolve
   `effective = body.redirect_uri or settings.mcp_oauth_redirect_uri`; validate
   (the global default is auto-allowed); store on the connection; pass to
   `build_authorize_url`.
7. **callback**: use `connection.redirect_uri or settings.mcp_oauth_redirect_uri`
   for `exchange_code` (the row is the source of truth for the flow).
8. **admin-ui** (separate, in the OAuth-frontend PR): `initiateMcpOAuth` passes
   `${window.location.origin}/settings/mcp-oauth/callback`.
9. **Integration guide** ([runbook](../runbooks/mcp-oauth-client-integration.md)):
   the three client forms + allowlist configuration.

## Out of scope

- Per-tenant redirect allowlists (platform-global only for now).
- Dynamic client registration (DCR) — still static `client_id` per connector.
