import { Button, Dropdown, Tooltip } from "antd";
import { Bell, ChevronDown, Moon, Search, Sun, UserCircle2 } from "lucide-react";
import { useTheme } from "../theme/ThemeContext";
import { useCommandPalette } from "./CommandPalette";

export function Topbar() {
  const { mode, toggle } = useTheme();
  const { open } = useCommandPalette();

  return (
    <>
      {/* Tenant switcher (mock) */}
      <Dropdown
        menu={{
          items: [
            { key: "acme", label: "acme-corp / leyi@acme" },
            { key: "demo", label: "demo-tenant / demo@helix" },
          ],
        }}
        trigger={["click"]}
      >
        <button
          type="button"
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 8,
            padding: "4px 12px",
            border: "1px solid var(--hx-border-default)",
            borderRadius: 6,
            background: "var(--hx-surface-raised)",
            color: "var(--hx-text-primary)",
            fontSize: 13,
            cursor: "pointer",
          }}
        >
          <strong>acme-corp</strong>
          <span style={{ color: "var(--hx-text-tertiary)" }}>/ leyi@acme</span>
          <ChevronDown size={12} strokeWidth={1.5} />
        </button>
      </Dropdown>

      {/* Cmd+K palette opener */}
      <button
        type="button"
        onClick={open}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 8,
          padding: "4px 12px",
          border: "1px solid var(--hx-border-subtle)",
          borderRadius: 6,
          background: "var(--hx-surface-raised)",
          color: "var(--hx-text-tertiary)",
          fontSize: 13,
          minWidth: 240,
          maxWidth: 320,
          cursor: "pointer",
        }}
      >
        <Search size={14} strokeWidth={1.5} />
        <span>搜索或跳转</span>
        <span style={{ flex: 1 }} />
        <span className="hx-kbd">⌘K</span>
      </button>

      <div style={{ flex: 1 }} />

      <Tooltip title={mode === "dark" ? "切到 Light" : "切到 Dark"}>
        <Button type="text" size="small" onClick={toggle} icon={
          mode === "dark" ? <Sun size={16} strokeWidth={1.5} /> : <Moon size={16} strokeWidth={1.5} />
        } aria-label="切换主题" />
      </Tooltip>

      <Tooltip title="通知">
        <Button type="text" size="small" icon={<Bell size={16} strokeWidth={1.5} />} aria-label="通知" />
      </Tooltip>

      <Tooltip title="用户菜单">
        <Button type="text" size="small" icon={<UserCircle2 size={16} strokeWidth={1.5} />} aria-label="用户菜单" />
      </Tooltip>
    </>
  );
}
