# Runbook — Seeding OAuth MCP connectors (Stream MCP-OAUTH OA-5)

The connector catalog ships an **empty** seed template
(`configs/mcp-catalog-seed.json` is `[]`). The primary way to add an OAuth MCP
connector is the **admin-ui catalog UI** (system_admin). This runbook covers the
**optional** env-seed path: declaring connectors in the seed file so they are
created automatically on startup once their client ID env var is set — useful for
reproducible deploys or pre-provisioning a connector before its OAuth app exists.

## How it works

- `configs/mcp-catalog-seed.json` is a JSON array of connector entries. It ships
  empty; append entries to pre-provision connectors. Each oauth2 entry's
  `oauth_client_id` is a `${MCP_OAUTH_<NAME>_CLIENT_ID}` placeholder.
- On startup, when `mcp_catalog_seed_file` points at the file, each entry whose
  placeholders **all resolve** from the environment is created in the catalog
  (idempotently — `create-if-absent`). An entry with an **unset** placeholder is
  skipped, so the platform boots fine before you've registered the app. An empty
  file (the default) seeds nothing.
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
2. **Point at the seed file** (only if using the env-seed path). Set
   `mcp_catalog_seed_file=configs/mcp-catalog-seed.json` (or your own copy).
   Unset (default) seeds nothing.

## Adding a connector via the seed file

1. **Append an entry** to the seed file — a `McpConnectorCatalogUpsert` structure
   with a fresh `${MCP_OAUTH_<NAME>_CLIENT_ID}` placeholder, the vendor's hosted
   MCP `url_template` and `transport` (`sse` / `streamable_http`), and a
   `required_tier` (`free` / `pro` / `enterprise`) gating which plans may use it.
2. **Register an OAuth app** in the connector's developer console. Use the
   `mcp_oauth_redirect_uri` value above as the app's redirect/callback URL
   (add it to the app's allowlist). The MCP authorization spec requires
   OAuth 2.1 + PKCE (S256) — pick that flow; no client secret is stored by expert_work.
3. **Set the client ID env var.** For `name: "linear"` that is
   `MCP_OAUTH_LINEAR_CLIENT_ID=<client id from step 2>`. Env var names follow
   `MCP_OAUTH_<NAME>_CLIENT_ID`, upper-cased from the entry's `name`. (The OAuth
   *client ID* is a public identifier, not a secret — a plain env var is fine.)
4. **Restart** the control plane. On boot it creates the connector. Confirm via
   `GET /v1/platform/mcp-catalog` (system_admin) — the entry should be present.
   Entries whose client-ID env var is unset stay skipped until you set it.

## Limits

- Seeding is **create-if-absent**: it never overwrites an entry that already
  exists. To change a live connector's client ID, URL, scopes, or tier, use the
  admin API `PATCH /v1/platform/mcp-catalog/{id}` (or `DELETE` then re-seed).
- A malformed template (bad JSON, or an entry that fails validation once its
  placeholders resolve) **fails startup** — fix the template and restart.
