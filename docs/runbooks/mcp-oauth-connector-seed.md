# Runbook — Seeding OAuth MCP connectors (Stream MCP-OAUTH OA-5)

How to make a hosted, OAuth-only MCP connector (Linear, Notion, Sentry,
Atlassian, …) available to tenants. The catalog ships a **template** with
placeholder client IDs; you register an OAuth app per connector, drop its
client ID into an environment variable, and the platform seeds the connector on
the next start.

## How it works

- `configs/mcp-catalog-seed.json` declares each oauth2 connector. Its
  `oauth_client_id` is a `${MCP_OAUTH_<NAME>_CLIENT_ID}` placeholder.
- On startup, when `mcp_catalog_seed_file` points at that file, each entry whose
  placeholders **all resolve** from the environment is created in the catalog
  (idempotently — `create-if-absent`). An entry with an **unset** placeholder is
  skipped, so the platform boots fine before you've registered the app.
- A connector becomes usable to a user via the normal flow: the user calls
  `POST /v1/mcp-servers/catalog/{id}/oauth/initiate`, authorizes in the browser,
  and the callback stores their per-user token (OA-2/OA-3). Tokens refresh
  automatically near expiry (OA-6).

## One-time platform setup

1. **Set the callback URL.** Configure `mcp_oauth_redirect_uri` to the **admin-ui
   callback page** — `https://app.example.com/settings/mcp-oauth/callback` — *not*
   the backend endpoint. The OAuth provider redirects the browser there (a plain
   navigation with no auth header); that page then calls
   `GET /v1/mcp-oauth/callback?state&code` with the logged-in user's bearer token.
   Until set, the initiate endpoint returns `503`.
2. **Point at the seed file.** Set `mcp_catalog_seed_file=configs/mcp-catalog-seed.json`
   (or your own copy). Unset (default) seeds nothing.

## Per-connector steps (example: Linear)

1. **Register an OAuth app** in the connector's developer console. Use the
   `mcp_oauth_redirect_uri` value above as the app's redirect/callback URL
   (add it to the app's allowlist). The MCP authorization spec requires
   OAuth 2.1 + PKCE (S256) — pick that flow; no client secret is stored by helix.
2. **Verify the MCP endpoint.** Confirm the connector's hosted MCP `url_template`
   and `transport` (`sse` or `streamable_http`) against the vendor's MCP docs,
   and adjust the template entry if they differ from the shipped defaults.
3. **Set the client ID env var.** For `name: "linear"` that is
   `MCP_OAUTH_LINEAR_CLIENT_ID=<client id from step 1>`. (The OAuth *client ID*
   is a public identifier, not a secret — a plain env var is fine.)
4. **Restart** the control plane. On boot it creates the connector. Confirm via
   `GET /v1/platform/mcp-catalog` (system_admin) — the entry should be present.

Env var names follow `MCP_OAUTH_<NAME>_CLIENT_ID`, upper-cased from each entry's
`name`. The shipped template covers `linear`, `notion`, `sentry`, `atlassian`.

## Adding a new connector

Append an entry to the seed file with a fresh `${MCP_OAUTH_<NAME>_CLIENT_ID}`
placeholder, then follow the per-connector steps. `required_tier` gates which
plans can use it (`free` / `pro` / `enterprise`).

## Limits

- Seeding is **create-if-absent**: it never overwrites an entry that already
  exists. To change a live connector's client ID, URL, scopes, or tier, use the
  admin API `PATCH /v1/platform/mcp-catalog/{id}` (or `DELETE` then re-seed).
- A malformed template (bad JSON, or an entry that fails validation once its
  placeholders resolve) **fails startup** — fix the template and restart.
