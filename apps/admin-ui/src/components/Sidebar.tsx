import { useNavigate, useLocation } from "react-router-dom";
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
  UserCircle2,
  Users,
  Building2,
  Shield,
} from "lucide-react";
import { BrandGlyph } from "../icons/BrandGlyph";
import { ApprovalPendingBadge } from "./ApprovalPendingBadge";

interface NavItem {
  key: string;
  label: React.ReactNode;
  icon: React.ReactNode;
  path: string;
}

const NAV_ITEMS: NavItem[] = [
  { key: "agents", label: "Agents", icon: <Bot size={16} strokeWidth={1.5} />, path: "/agents" },
  {
    key: "runs",
    label: <ApprovalPendingBadge>Runs</ApprovalPendingBadge>,
    icon: <Activity size={16} strokeWidth={1.5} />,
    path: "/runs",
  },
  { key: "curation", label: "Curation+Eval", icon: <CheckSquare size={16} strokeWidth={1.5} />, path: "/curation" },
  { key: "memory", label: "Memory", icon: <Brain size={16} strokeWidth={1.5} />, path: "/memory" },
  { key: "skills", label: "Skills", icon: <FileText size={16} strokeWidth={1.5} />, path: "/skills" },
  { key: "triggers", label: "Triggers", icon: <Clock size={16} strokeWidth={1.5} />, path: "/triggers" },
];

const SETTINGS_ITEMS: NavItem[] = [
  { key: "settings-create-tenant", label: "Create Tenant", icon: <Building2 size={16} strokeWidth={1.5} />, path: "/settings/create-tenant" },
  { key: "settings-platform", label: "Platform Credentials", icon: <KeyRound size={16} strokeWidth={1.5} />, path: "/settings/platform" },
  { key: "settings-api-keys", label: "API Keys", icon: <Key size={16} strokeWidth={1.5} />, path: "/settings/api-keys" },
  { key: "settings-credentials", label: "Credentials", icon: <KeyRound size={16} strokeWidth={1.5} />, path: "/settings/credentials" },
  { key: "settings-service-accounts", label: "Service Accounts", icon: <UserCircle2 size={16} strokeWidth={1.5} />, path: "/settings/service-accounts" },
  { key: "settings-members", label: "Members", icon: <Users size={16} strokeWidth={1.5} />, path: "/settings/members" },
  { key: "settings-audit", label: "Audit", icon: <Shield size={16} strokeWidth={1.5} />, path: "/settings/audit" },
];

export function Sidebar() {
  const nav = useNavigate();
  const location = useLocation();

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
          ...NAV_ITEMS.map((i) => ({ key: i.key, label: i.label, icon: i.icon })),
          { type: "divider" as const },
          {
            key: "settings-group",
            label: "Settings",
            type: "group" as const,
            children: SETTINGS_ITEMS.map((i) => ({
              key: i.key,
              label: i.label,
              icon: i.icon,
            })),
          },
        ]}
      />
    </div>
  );
}
