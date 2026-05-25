import { Button, Dropdown, Tooltip } from "antd";
import { Bell, LogOut, Moon, Search, Sun, UserCircle2 } from "lucide-react";

import { useAuth } from "../auth/AuthContext";
import { useTheme } from "../theme/ThemeContext";
import { TenantSwitcher } from "./TenantSwitcher";
import { useCommandPalette } from "./CommandPalette";

export function Topbar() {
  const { mode, toggle } = useTheme();
  const { open } = useCommandPalette();
  const { identity, logout } = useAuth();

  return (
    <>
      <TenantSwitcher />

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

      <Dropdown
        menu={{
          items: [
            {
              key: "identity",
              disabled: true,
              label: (
                <span style={{ fontSize: 12, color: "var(--hx-text-tertiary)" }}>
                  {identity?.displayName ?? "anonymous"}
                  {identity?.isSystemAdmin && (
                    <span
                      style={{
                        marginLeft: 8,
                        padding: "1px 6px",
                        background: "var(--hx-accent-violet, #a855f7)",
                        color: "white",
                        borderRadius: 4,
                        fontSize: 10,
                      }}
                    >
                      sys
                    </span>
                  )}
                </span>
              ),
            },
            { type: "divider" },
            {
              key: "logout",
              label: "Sign out",
              icon: <LogOut size={14} strokeWidth={1.5} />,
              onClick: logout,
            },
          ],
        }}
        trigger={["click"]}
        placement="bottomRight"
      >
        <Tooltip title={identity?.displayName ?? "用户菜单"}>
          <Button
            type="text"
            size="small"
            icon={<UserCircle2 size={16} strokeWidth={1.5} />}
            aria-label="用户菜单"
            data-testid="user-menu"
          />
        </Tooltip>
      </Dropdown>
    </>
  );
}
