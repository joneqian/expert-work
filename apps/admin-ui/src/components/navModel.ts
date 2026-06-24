/**
 * Navigation IA model — scope-driven, platform/tenant separated.
 *
 * See ``docs/design/admin-ui-nav-ia.md``. The sidebar is a function of
 * ``(scope, isSystemAdmin)``:
 *
 *   - a concrete tenant scope → workspace + tenant-settings groups
 *     (the tenant's agents + its settings); never the platform group.
 *   - the cross-tenant ``"*"`` scope (platform level) → platform group
 *     only (platform governance + read-only cross-tenant overview).
 *
 * Gating is nav-level noise reduction; the control-plane RBAC /
 * ``is_system_admin`` checks remain the real gate (defense in depth).
 *
 * This is the single source of truth shared by ``Sidebar``,
 * ``CommandPalette`` and the scope-switch redirect so the three never
 * drift.
 */
import { SCOPE_ALL, type TenantScopeValue } from "../tenant/TenantScopeContext";

export type NavGroup = "workspace" | "tenant-settings" | "platform";

export interface NavEntry {
  /** Menu item key (stable; used for selection + routing). */
  key: string;
  /** i18n key under ``nav.*`` for the menu label. */
  labelKey: string;
  /** Route the entry navigates to. */
  path: string;
  /** Which IA group the entry belongs to. */
  group: NavGroup;
  /** Wrap the label in the pending-approval badge (Runs/Approvals). */
  badge?: boolean;
}

/** A. Workspace — the tenant's agent operations. */
export const WORKSPACE_ITEMS: readonly NavEntry[] = [
  {
    key: "agents",
    labelKey: "nav.agents",
    path: "/agents",
    group: "workspace",
  },
  { key: "runs", labelKey: "nav.runs", path: "/runs", group: "workspace" },
  {
    key: "approvals",
    labelKey: "nav.approvals",
    path: "/approvals",
    group: "workspace",
    badge: true,
  },
  {
    key: "curation",
    labelKey: "nav.curation",
    path: "/curation",
    group: "workspace",
  },
  { key: "eval", labelKey: "nav.eval", path: "/eval-runs", group: "workspace" },
  {
    key: "memory",
    labelKey: "nav.memory",
    path: "/memory",
    group: "workspace",
  },
  {
    key: "artifacts",
    labelKey: "nav.artifacts",
    path: "/artifacts",
    group: "workspace",
  },
  {
    key: "knowledge",
    labelKey: "nav.knowledge",
    path: "/knowledge",
    group: "workspace",
  },
  {
    key: "skills",
    labelKey: "nav.skills",
    path: "/skills",
    group: "workspace",
  },
  {
    key: "skill-marketplace",
    labelKey: "nav.skill_marketplace",
    path: "/skill-marketplace",
    group: "workspace",
  },
  {
    key: "triggers",
    labelKey: "nav.triggers",
    path: "/triggers",
    group: "workspace",
  },
  {
    key: "webhooks",
    labelKey: "nav.webhooks",
    path: "/webhooks",
    group: "workspace",
  },
];

/** B. Tenant settings — managed for the current tenant. */
export const TENANT_SETTINGS_ITEMS: readonly NavEntry[] = [
  {
    key: "settings-members",
    labelKey: "nav.members",
    path: "/settings/members",
    group: "tenant-settings",
  },
  {
    key: "settings-credentials",
    labelKey: "nav.credentials",
    path: "/settings/credentials",
    group: "tenant-settings",
  },
  {
    key: "settings-api-keys",
    labelKey: "nav.api_keys",
    path: "/settings/api-keys",
    group: "tenant-settings",
  },
  {
    key: "settings-service-accounts",
    labelKey: "nav.service_accounts",
    path: "/settings/service-accounts",
    group: "tenant-settings",
  },
  {
    key: "settings-mcp-servers",
    labelKey: "nav.mcp_servers",
    path: "/settings/mcp-servers",
    group: "tenant-settings",
  },
  {
    key: "settings-mcp-oauth",
    labelKey: "nav.mcp_oauth",
    path: "/settings/mcp-oauth",
    group: "tenant-settings",
  },
  {
    key: "settings-audit",
    labelKey: "nav.audit",
    path: "/settings/audit",
    group: "tenant-settings",
  },
  {
    key: "settings-egress-audit",
    labelKey: "nav.egress_audit",
    path: "/settings/egress-audit",
    group: "tenant-settings",
  },
  {
    key: "settings-usage",
    labelKey: "nav.usage",
    path: "/settings/usage",
    group: "tenant-settings",
  },
];

/** C. Platform governance — system_admin, platform level only. */
export const PLATFORM_ITEMS: readonly NavEntry[] = [
  {
    key: "settings-tenants",
    labelKey: "nav.tenants",
    path: "/settings/tenants",
    group: "platform",
  },
  {
    key: "settings-platform-users",
    labelKey: "nav.platform_users",
    path: "/settings/platform-users",
    group: "platform",
  },
  {
    key: "settings-platform",
    labelKey: "nav.platform_credentials",
    path: "/settings/platform",
    group: "platform",
  },
  {
    key: "settings-mcp-catalog",
    labelKey: "nav.mcp_catalog",
    path: "/settings/mcp-catalog",
    group: "platform",
  },
  {
    key: "settings-platform-skills",
    labelKey: "nav.platform_skills",
    path: "/settings/platform-skills",
    group: "platform",
  },
  {
    key: "settings-rate-card",
    labelKey: "nav.rate_card",
    path: "/settings/rate-card",
    group: "platform",
  },
  {
    key: "settings-chargeback",
    labelKey: "nav.chargeback",
    path: "/settings/billing-chargeback",
    group: "platform",
  },
  // Read-only cross-tenant overview — reuses the members page under the
  // ``"*"`` scope (already renders read-only there). §2-C / §8 point 2.
  {
    key: "platform-members-all",
    labelKey: "nav.members_all_tenants",
    path: "/settings/members",
    group: "platform",
  },
];

export const ALL_NAV_ENTRIES: readonly NavEntry[] = [
  ...WORKSPACE_ITEMS,
  ...TENANT_SETTINGS_ITEMS,
  ...PLATFORM_ITEMS,
];

/** ``true`` when the caller is operating at the platform level. */
export function isPlatformScope(scope: TenantScopeValue): boolean {
  return scope === SCOPE_ALL;
}

/**
 * Visible groups for ``(scope, isSystemAdmin)``.
 *
 *   - platform scope → platform group (with a ``isSystemAdmin`` belt-and
 *     -braces guard; the switcher only offers ``"*"`` to admins anyway).
 *   - any concrete tenant → workspace + tenant-settings.
 */
export function visibleGroups(
  scope: TenantScopeValue,
  isSystemAdmin: boolean,
): NavGroup[] {
  if (isPlatformScope(scope)) {
    return isSystemAdmin ? ["platform"] : [];
  }
  return ["workspace", "tenant-settings"];
}

/** Which group a route path belongs to (longest-prefix wins so eg.
 *  ``/settings/platform-users`` doesn't match ``/settings/platform``
 *  before the more specific entry). */
export function groupForPath(path: string): NavGroup | null {
  const match = [...ALL_NAV_ENTRIES]
    .filter((e) => path === e.path || path.startsWith(`${e.path}/`))
    .sort((a, b) => b.path.length - a.path.length)[0];
  return match?.group ?? null;
}
