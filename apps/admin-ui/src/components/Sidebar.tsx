import { useNavigate, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Menu } from "antd";
import {
  Banknote,
  Bot,
  ListChecks,
  CheckSquare,
  FlaskConical,
  Brain,
  BookOpen,
  FileText,
  Fingerprint,
  Clock,
  Key,
  KeyRound,
  Boxes,
  LayoutTemplate,
  Gauge,
  LineChart,
  MessagesSquare,
  Network,
  Plug,
  Receipt,
  Sparkles,
  ShieldCheck,
  Store,
  UserCircle2,
  Users,
  Building,
  Shield,
  Webhook,
} from "lucide-react";
import { BrandGlyph } from "../icons/BrandGlyph";
import { ApprovalPendingBadge } from "./ApprovalPendingBadge";
import { useAuth } from "../auth/AuthContext";
import { useTenantScope } from "../tenant/TenantScopeContext";
import {
  ALL_NAV_ENTRIES,
  PLATFORM_ITEMS,
  TENANT_SETTINGS_ITEMS,
  WORKSPACE_ITEMS,
  visibleGroups,
  type NavEntry,
  type NavGroup,
} from "./navModel";

/** Icon per nav key — kept out of the shared model so it stays a pure
 *  data module (no JSX). */
const ICONS: Record<string, React.ReactNode> = {
  agents: <Bot size={16} strokeWidth={1.5} />,
  conversations: <MessagesSquare size={16} strokeWidth={1.5} />,
  approvals: <ListChecks size={16} strokeWidth={1.5} />,
  curation: <CheckSquare size={16} strokeWidth={1.5} />,
  eval: <FlaskConical size={16} strokeWidth={1.5} />,
  memory: <Brain size={16} strokeWidth={1.5} />,
  knowledge: <BookOpen size={16} strokeWidth={1.5} />,
  skills: <FileText size={16} strokeWidth={1.5} />,
  "agent-template-marketplace": <Store size={16} strokeWidth={1.5} />,
  triggers: <Clock size={16} strokeWidth={1.5} />,
  webhooks: <Webhook size={16} strokeWidth={1.5} />,
  "settings-members": <Users size={16} strokeWidth={1.5} />,
  "settings-credentials": <KeyRound size={16} strokeWidth={1.5} />,
  "settings-api-keys": <Key size={16} strokeWidth={1.5} />,
  "settings-service-accounts": <UserCircle2 size={16} strokeWidth={1.5} />,
  "settings-mcp-servers": <Plug size={16} strokeWidth={1.5} />,
  "settings-audit": <Shield size={16} strokeWidth={1.5} />,
  "settings-egress-audit": <Network size={16} strokeWidth={1.5} />,
  "settings-usage": <Gauge size={16} strokeWidth={1.5} />,
  "settings-tenants": <Building size={16} strokeWidth={1.5} />,
  "settings-platform-users": <ShieldCheck size={16} strokeWidth={1.5} />,
  "settings-platform": <KeyRound size={16} strokeWidth={1.5} />,
  "settings-mcp-catalog": <Boxes size={16} strokeWidth={1.5} />,
  "settings-agent-templates": <LayoutTemplate size={16} strokeWidth={1.5} />,
  "settings-platform-skills": <Sparkles size={16} strokeWidth={1.5} />,
  "settings-rate-card": <Banknote size={16} strokeWidth={1.5} />,
  "settings-chargeback": <Receipt size={16} strokeWidth={1.5} />,
  "settings-observability": <LineChart size={16} strokeWidth={1.5} />,
  "settings-keycloak": <Fingerprint size={16} strokeWidth={1.5} />,
  "platform-members-all": <Users size={16} strokeWidth={1.5} />,
};

const GROUP_TITLE_KEY: Record<NavGroup, string> = {
  workspace: "nav.group_workspace",
  "tenant-settings": "nav.group_tenant_settings",
  platform: "nav.group_platform",
};

const GROUP_ITEMS: Record<NavGroup, readonly NavEntry[]> = {
  workspace: WORKSPACE_ITEMS,
  "tenant-settings": TENANT_SETTINGS_ITEMS,
  platform: PLATFORM_ITEMS,
};

export function Sidebar() {
  const nav = useNavigate();
  const location = useLocation();
  const { t } = useTranslation();
  const { identity } = useAuth();
  const { scope } = useTenantScope();

  const isSystemAdmin = identity?.isSystemAdmin ?? false;
  const groups = visibleGroups(scope, isSystemAdmin);
  const shownEntries = groups.flatMap((g) => GROUP_ITEMS[g]);

  const selectedKey = (() => {
    const path = location.pathname;
    const top = shownEntries.find((i) => path === i.path || path.startsWith(`${i.path}/`));
    return top?.key ?? shownEntries[0]?.key ?? "agents";
  })();

  const labelFor = (entry: NavEntry) =>
    entry.badge ? (
      <ApprovalPendingBadge>{t(entry.labelKey)}</ApprovalPendingBadge>
    ) : (
      t(entry.labelKey)
    );

  const menuItems = groups.flatMap((g, gi) => {
    const groupItems = GROUP_ITEMS[g].map((entry) => ({
      key: entry.key,
      label: labelFor(entry),
      icon: ICONS[entry.key],
    }));
    const groupNode = {
      key: `${g}-group`,
      label: t(GROUP_TITLE_KEY[g]),
      type: "group" as const,
      children: groupItems,
    };
    // Divider between groups (but not before the first).
    return gi === 0 ? [groupNode] : [{ type: "divider" as const }, groupNode];
  });

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div
        style={{
          height: 48,
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "0 16px",
          borderBottom: "1px solid var(--ew-border-subtle)",
          fontWeight: 600,
          fontSize: 16,
          letterSpacing: "-0.02em",
        }}
      >
        <BrandGlyph size={20} style={{ color: "var(--ew-color-brand-500)" }} />
        <span>Expert Work</span>
      </div>
      <Menu
        className="ew-sidebar-menu"
        mode="inline"
        selectedKeys={[selectedKey]}
        onClick={({ key }) => {
          const item = ALL_NAV_ENTRIES.find((i) => i.key === key);
          if (item) nav(item.path);
        }}
        style={{
          flex: 1,
          background: "transparent",
          borderRight: "none",
          padding: "12px 8px",
        }}
        items={menuItems}
      />
    </div>
  );
}
