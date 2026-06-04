import { useNavigate, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Menu } from "antd";
import {
  Bot,
  Activity,
  CheckSquare,
  Brain,
  FileText,
  Clock,
  Key,
  KeyRound,
  Boxes,
  Plug,
  UserCircle2,
  Users,
  Building,
  Shield,
} from "lucide-react";
import { BrandGlyph } from "../icons/BrandGlyph";
import { ApprovalPendingBadge } from "./ApprovalPendingBadge";

interface NavItem {
  key: string;
  /** i18n key under ``nav.*`` for the menu label. */
  labelKey: string;
  icon: React.ReactNode;
  path: string;
  /** Wrap the label in the pending-approval badge (Runs only). */
  badge?: boolean;
}

const NAV_ITEMS: NavItem[] = [
  { key: "agents", labelKey: "nav.agents", icon: <Bot size={16} strokeWidth={1.5} />, path: "/agents" },
  { key: "runs", labelKey: "nav.runs", icon: <Activity size={16} strokeWidth={1.5} />, path: "/runs", badge: true },
  { key: "curation", labelKey: "nav.curation", icon: <CheckSquare size={16} strokeWidth={1.5} />, path: "/curation" },
  { key: "memory", labelKey: "nav.memory", icon: <Brain size={16} strokeWidth={1.5} />, path: "/memory" },
  { key: "skills", labelKey: "nav.skills", icon: <FileText size={16} strokeWidth={1.5} />, path: "/skills" },
  { key: "triggers", labelKey: "nav.triggers", icon: <Clock size={16} strokeWidth={1.5} />, path: "/triggers" },
];

const SETTINGS_ITEMS: NavItem[] = [
  { key: "settings-tenants", labelKey: "nav.tenants", icon: <Building size={16} strokeWidth={1.5} />, path: "/settings/tenants" },
  { key: "settings-platform", labelKey: "nav.platform_credentials", icon: <KeyRound size={16} strokeWidth={1.5} />, path: "/settings/platform" },
  { key: "settings-mcp-catalog", labelKey: "nav.mcp_catalog", icon: <Boxes size={16} strokeWidth={1.5} />, path: "/settings/mcp-catalog" },
  { key: "settings-api-keys", labelKey: "nav.api_keys", icon: <Key size={16} strokeWidth={1.5} />, path: "/settings/api-keys" },
  { key: "settings-credentials", labelKey: "nav.credentials", icon: <KeyRound size={16} strokeWidth={1.5} />, path: "/settings/credentials" },
  { key: "settings-service-accounts", labelKey: "nav.service_accounts", icon: <UserCircle2 size={16} strokeWidth={1.5} />, path: "/settings/service-accounts" },
  { key: "settings-members", labelKey: "nav.members", icon: <Users size={16} strokeWidth={1.5} />, path: "/settings/members" },
  { key: "settings-audit", labelKey: "nav.audit", icon: <Shield size={16} strokeWidth={1.5} />, path: "/settings/audit" },
  { key: "settings-mcp-servers", labelKey: "nav.mcp_servers", icon: <Plug size={16} strokeWidth={1.5} />, path: "/settings/mcp-servers" },
];

export function Sidebar() {
  const nav = useNavigate();
  const location = useLocation();
  const { t } = useTranslation();

  const selectedKey = (() => {
    const path = location.pathname;
    const top = [...NAV_ITEMS, ...SETTINGS_ITEMS].find((i) => path.startsWith(i.path));
    return top?.key ?? "agents";
  })();

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div
        style={{
          height: 48,
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "0 16px",
          borderBottom: "1px solid var(--hx-border-subtle)",
          fontWeight: 600,
          fontSize: 16,
          letterSpacing: "-0.02em",
        }}
      >
        <BrandGlyph size={20} style={{ color: "var(--hx-color-brand-500)" }} />
        <span>helix</span>
        <span
          className="hx-kbd"
          style={{ marginLeft: "auto", fontSize: 10, padding: "1px 4px" }}
        >
          demo
        </span>
      </div>
      <Menu
        className="hx-sidebar-menu"
        mode="inline"
        selectedKeys={[selectedKey]}
        onClick={({ key }) => {
          const all = [...NAV_ITEMS, ...SETTINGS_ITEMS];
          const item = all.find((i) => i.key === key);
          if (item) nav(item.path);
        }}
        style={{
          flex: 1,
          background: "transparent",
          borderRight: "none",
          padding: "12px 8px",
        }}
        items={[
          ...NAV_ITEMS.map((i) => ({
            key: i.key,
            label: i.badge ? (
              <ApprovalPendingBadge>{t(i.labelKey)}</ApprovalPendingBadge>
            ) : (
              t(i.labelKey)
            ),
            icon: i.icon,
          })),
          { type: "divider" as const },
          {
            key: "settings-group",
            label: t("nav.settings_group"),
            type: "group" as const,
            children: SETTINGS_ITEMS.map((i) => ({
              key: i.key,
              label: t(i.labelKey),
              icon: i.icon,
            })),
          },
        ]}
      />
    </div>
  );
}
