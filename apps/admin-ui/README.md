# Expert Work Admin UI

Production scaffold for the operator-facing Expert Work admin SPA. Tracks
**Stream H.1b** in `docs/streams/STREAM-H-DESIGN.md`. The visual-baseline
sibling (`apps/admin-ui-demo/`) was removed after H.1b PR 2b — this
workspace is now the canonical visual + functional reference.

## Run

```sh
pnpm install
EXPERT_WORK_CONTROL_PLANE_URL=http://localhost:8000 pnpm dev
# → http://localhost:5173
```

The Vite dev server proxies `/v1/*` to `EXPERT_WORK_CONTROL_PLANE_URL` so the
SPA can talk to a local `expert_work.control_plane.main` without any CORS
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

## Status by PR

| PR | what | state |
|---|---|---|
| **H.1b PR 1** | scaffold + auth + tenant scope + live Agents | **merged** (#274) |
| **H.1b PR 2a** | `GET /v1/me` + i18n (zh-CN / en) + Cmd+K real Agents | **merged** (#277) |
| **H.1b PR 2b** | OIDC code-flow (PKCE) + silent renew + Keycloak dev docs | **merged** (#278) |
| **H.1b PR 3** | Remaining 6 SDK clients (runs / skills / triggers / memory / curation / api_keys) + drop `mock/*` from AgentDetail / RunDetail / SettingsApiKeys | pending |
| **H.1b PR 4** | Storybook + Playwright E2E + axe a11y | pending |

## Design baseline

The CSS tokens and shell layout match `docs/design/mockups/shared/`
verbatim. Antd is themed in `src/theme/antdTheme.ts` so all primitives
inherit the Expert Work palette (cyan brand, violet accent, dark-first).

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
