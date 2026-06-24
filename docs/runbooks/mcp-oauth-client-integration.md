# Runbook — Integrating a client with MCP OAuth (multi-client)

How any client — the admin-ui, an employee-facing **web app**, a **desktop / CLI**
tool, or code **embedded** in your business system — drives the per-user MCP
OAuth flow so an employee connects a hosted `oauth2` connector with their own
account. Design: [mcp-oauth-multi-client.md](../design/mcp-oauth-multi-client.md).

## What the platform gives you

Three authenticated endpoints (the client sends the logged-in user's bearer
token on every call). They return **raw JSON** (not the `{success,data,error}`
envelope):

| Endpoint | Purpose |
|---|---|
| `POST /v1/mcp-servers/catalog/{catalog_id}/oauth/initiate` | start the flow; returns `{ connection_id, authorize_url, status }` |
| `GET /v1/mcp-oauth/callback?state&code` | finish the flow (exchange code for tokens) |
| `GET /v1/mcp-oauth/connections` | list the caller's own connections |
| `DELETE /v1/mcp-oauth/connections/{id}` | disconnect (revoke + drop) |

`initiate` accepts an optional body `{ "redirect_uri": "<your callback>" }`.
**Supply your own** so the provider returns the browser to *your* client. Omit
it to use the deployment's global default (`mcp_oauth_redirect_uri`).

## The flow

```
1. Client → POST initiate { redirect_uri: R }      → { authorize_url }
2. Client opens authorize_url in a browser; user signs in + consents
3. Provider → redirects browser to R with ?state&code
4. Client captures state + code at R
5. Client → GET callback?state&code (with bearer)  → { status: "connected" }
6. Done — the agent now uses the user's token for that connector
```

The `redirect_uri` you send at step 1 is stored on the connection and reused at
step 5's token exchange — they must be the same (OAuth requirement); the platform
handles that for you.

## Per client form

### Web app (admin-ui, or your own web client)
- `redirect_uri` = a page in your app, e.g. `https://app.example.com/oauth/cb`.
- Step 2: full-page navigate (`window.location.assign(authorize_url)`).
- Step 4: that page reads `state` + `code` from its query string.
- Step 5: it calls `callback` with the user's bearer, then routes back.
- admin-ui ships this at `/settings/mcp-oauth/callback`.

### Desktop / CLI (native app, RFC 8252)
- `redirect_uri` = a **loopback** URI on an ephemeral port your app listens on,
  e.g. `http://127.0.0.1:53122/cb` (loopback is allowed by default — any port).
  A registered custom scheme (`myapp://cb`) also works if you add it to the
  allowlist.
- Step 2: open the system browser at `authorize_url`.
- Step 4: your local listener (or custom-scheme handler) receives `state`+`code`.
- Step 5: call `callback` with the user's bearer.

### Embedded in your business system
- `redirect_uri` = a callback URL in your system.
- Otherwise identical to the web-app flow.

## Configuration (platform operator)

- `mcp_oauth_redirect_uri` — the default callback when a client omits one
  (e.g. admin-ui's page). Implicitly allowed.
- `mcp_oauth_redirect_allowlist` — the permitted client redirect prefixes
  (origin + path). Add each web / embedded client's callback URL and any custom
  scheme. A candidate must origin-match **and** path-prefix-match an entry, or
  it is rejected with `422 MCP_OAUTH_REDIRECT_NOT_ALLOWED` (open-redirect guard).
- `mcp_oauth_allow_loopback_redirect` (default `true`) — accept RFC 8252
  loopback redirects for native clients. Set `false` to forbid them.
- Register every redirect URI you use in the **connector's OAuth app** allowlist
  at the provider too.

## Notes

- All endpoints require the user's bearer token; the connection is scoped to that
  user (`subject_id`). A regular employee (operator role) can manage their own
  connections — no admin needed.
- Tokens never reach the client — only `secret://` refs live server-side; the
  agent injects the token at MCP connect-out. Refresh is automatic near expiry.
