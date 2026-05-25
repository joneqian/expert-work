# helix Admin UI

Production scaffold for the operator-facing helix admin SPA. Tracks
**Stream H.1b** in `docs/streams/STREAM-H-DESIGN.md`; the visual baseline
sibling lives at [`../admin-ui-demo`](../admin-ui-demo) and stays around
as a sandbox until H.2-H.4 wrap.

## Run

```sh
pnpm install
HELIX_CONTROL_PLANE_URL=http://localhost:8000 pnpm dev
# → http://localhost:5173
```

The Vite dev server proxies `/v1/*` to `HELIX_CONTROL_PLANE_URL` so the
SPA can talk to a local `helix.control_plane.main` without any CORS
fiddling.

## Scripts

| script | what |
|---|---|
| `pnpm dev` | Vite dev server on `:5173` with the `/v1` proxy |
| `pnpm build` | tsc -b + vite build → `dist/` |
| `pnpm preview` | serve the built `dist/` on `:4173` |
| `pnpm typecheck` | tsc -b --noEmit |
| `pnpm test` | vitest run (CI uses this) |
| `pnpm test:watch` | vitest watch |

## What's in PR 1 (Stream H.1b PR 1/N)

The first slice delivers a working **read** path against the live
control-plane plus the auth + tenant-scope plumbing every page
downstream will reuse.

| layer | path | what |
|---|---|---|
| **API SDK** | `src/api/client.ts` | axios + auth interceptor + envelope unwrap + `withTenantScope` helper |
| **API SDK** | `src/api/agents.ts` | `listAgents({ tenantScope })` → `GET /v1/agents` |
| **Auth** | `src/auth/AuthContext.tsx` | token-paste login persisted to localStorage; decodes JWT to surface `isSystemAdmin` |
| **Auth** | `src/auth/ProtectedRoute.tsx` | redirects anonymous callers to `/login`, preserves `from` |
| **Auth** | `src/pages/Login.tsx` | textarea + sign-in button; lands on the original path |
| **Tenant** | `src/tenant/TenantScopeContext.tsx` | scope = `home` / `"*"` / specific UUID; persisted to sessionStorage; defends against stale `"*"` for non-admins |
| **Tenant** | `src/components/TenantSwitcher.tsx` | topbar dropdown; system_admin sees "All tenants"; emits purple `cross` tag |
| **Pages** | `src/pages/AgentsList.tsx` | hooked to live `/v1/agents`; shows the `cross-tenant view` banner when `cross_tenant=true` |
| **Tests** | `src/api/__tests__/client.test.ts` | `withTenantScope` / `unwrap` invariants |
| **Tests** | `src/components/__tests__/TenantSwitcher.test.tsx` | tenant_admin disabled / system_admin enabled / JWT parser |

## Not in PR 1 — follow-up PRs (still under H.1b)

| PR | scope |
|---|---|
| **H.1b PR 2** | OIDC code-flow login (replace token paste) + `GET /v1/me` for server-truth `isSystemAdmin` + i18n (zh-CN / en) + Cmd+K wired to real routes |
| **H.1b PR 3** | Remaining 6 API clients (runs / skills / triggers / memory / curation / api_keys) + replace `mock/*` references in CommandPalette + AgentDetail + RunDetail + SettingsApiKeys |
| **H.1b PR 4** | Storybook + E2E tests (Playwright) + a11y audit (axe) on every page |

After H.1b PR 4 ships, `apps/admin-ui-demo/` can be deleted.

## Design baseline

The CSS tokens and shell layout match `docs/design/mockups/shared/`
verbatim. Antd is themed in `src/theme/antdTheme.ts` so all primitives
inherit the helix palette (cyan brand, violet accent, dark-first).

See [admin-ui-design-baseline](../../docs/streams/STREAM-H-DESIGN.md)
and `docs/design/admin-ui-language.md` for the rationale.

## Stream N integration

Cross-tenant capability for platform admins is shipped end-to-end on
the backend (PRs #265-#271). This SPA surfaces it via:

- `useTenantScope().apiTenantScope` returns `undefined` / `"*"` /
  UUID — pass straight to any SDK call via `withTenantScope`.
- The `TenantSwitcher` only offers `"*"` when
  `useAuth().identity?.isSystemAdmin` is true.
- The AgentsList page renders a purple `cross-tenant view` banner
  when the server replies with `cross_tenant: true`.

A non-admin pasting a forged "*" through the URL still gets 403
`CROSS_TENANT_FORBIDDEN` server-side via `ensure_tenant_scope` — the
client guards are UX, not security.
