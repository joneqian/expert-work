import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Modal, Input, type InputRef } from "antd";
import { useNavigate } from "react-router-dom";
import { Bot, Activity, CheckSquare, Brain, FileText, Clock, Key, Plus, ArrowRight, Cog } from "lucide-react";
import { mockAgents } from "../mock/agents";

interface CmdItem {
  group: string;
  key: string;
  label: ReactNode;
  subtitle?: string;
  icon?: ReactNode;
  shortcut?: string[];
  action: () => void;
}

interface CommandPaletteContextValue {
  open: () => void;
  close: () => void;
}

const Ctx = createContext<CommandPaletteContextValue | null>(null);

export function useCommandPalette() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useCommandPalette must be used inside <CommandPaletteProvider>");
  return ctx;
}

export function CommandPaletteProvider({ children }: { children: ReactNode }) {
  const [isOpen, setIsOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<InputRef>(null);
  const nav = useNavigate();

  const open = useCallback(() => {
    setIsOpen(true);
    setQuery("");
    setActiveIndex(0);
  }, []);
  const close = useCallback(() => setIsOpen(false), []);

  // Global Cmd+K
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        setIsOpen((v) => !v);
      }
      if (e.key === "Escape") setIsOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Focus input on open
  useEffect(() => {
    if (isOpen) {
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [isOpen]);

  const allItems: CmdItem[] = useMemo(() => {
    const items: CmdItem[] = [];

    // Agents
    mockAgents.forEach((a) => {
      items.push({
        group: "Agents",
        key: `agent-${a.id}`,
        label: a.name,
        subtitle: `${a.version} · ${a.status}`,
        icon: <Bot size={16} strokeWidth={1.5} />,
        action: () => {
          nav(`/agents/${a.id}/overview`);
          close();
        },
      });
    });

    // 顶级跳转
    [
      { key: "go-agents", label: "Agents", path: "/agents", icon: <Bot size={16} strokeWidth={1.5} />, sc: ["g", "a"] },
      { key: "go-runs", label: "Runs(跨 agent)", path: "/runs", icon: <Activity size={16} strokeWidth={1.5} />, sc: ["g", "r"] },
      { key: "go-curation", label: "Curation 评审", path: "/curation", icon: <CheckSquare size={16} strokeWidth={1.5} />, sc: ["g", "c"] },
      { key: "go-memory", label: "Memory", path: "/memory", icon: <Brain size={16} strokeWidth={1.5} />, sc: ["g", "m"] },
      { key: "go-skills", label: "Skills", path: "/skills", icon: <FileText size={16} strokeWidth={1.5} />, sc: ["g", "s"] },
      { key: "go-triggers", label: "Triggers", path: "/triggers", icon: <Clock size={16} strokeWidth={1.5} />, sc: ["g", "t"] },
      { key: "go-api-keys", label: "Settings · API Keys", path: "/settings/api-keys", icon: <Key size={16} strokeWidth={1.5} />, sc: [] },
    ].forEach((g) => {
      items.push({
        group: "跳转",
        key: g.key,
        label: g.label,
        icon: g.icon,
        shortcut: g.sc,
        action: () => {
          nav(g.path);
          close();
        },
      });
    });

    // 动作
    items.push({
      group: "动作",
      key: "create-agent",
      label: "创建新 Agent…",
      icon: <Plus size={16} strokeWidth={1.5} />,
      shortcut: ["N"],
      action: () => {
        nav("/agents?action=create");
        close();
      },
    });
    items.push({
      group: "动作",
      key: "create-api-key",
      label: "创建新 API Key…",
      icon: <Key size={16} strokeWidth={1.5} />,
      action: () => {
        nav("/settings/api-keys?action=create");
        close();
      },
    });
    items.push({
      group: "动作",
      key: "open-settings",
      label: "打开 Settings",
      icon: <Cog size={16} strokeWidth={1.5} />,
      action: () => {
        nav("/settings/api-keys");
        close();
      },
    });

    return items;
  }, [nav, close]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return allItems.slice(0, 20);
    return allItems
      .filter((i) => {
        const labelStr = typeof i.label === "string" ? i.label : "";
        return labelStr.toLowerCase().includes(q) || (i.subtitle?.toLowerCase().includes(q) ?? false);
      })
      .slice(0, 30);
  }, [allItems, query]);

  // Reset active index when filtered list changes
  useEffect(() => {
    setActiveIndex(0);
  }, [query]);

  const groups = useMemo(() => {
    const out: Record<string, CmdItem[]> = {};
    filtered.forEach((i) => {
      (out[i.group] ??= []).push(i);
    });
    return out;
  }, [filtered]);

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex((i) => Math.min(i + 1, filtered.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const item = filtered[activeIndex];
      if (item) item.action();
    }
  };

  return (
    <Ctx.Provider value={{ open, close }}>
      {children}
      <Modal
        open={isOpen}
        onCancel={close}
        footer={null}
        closable={false}
        width={640}
        styles={{
          body: { padding: 0, background: "var(--hx-surface-base)" },
          content: { padding: 0, background: "var(--hx-surface-base)" },
          mask: { backdropFilter: "blur(4px)" },
        }}
        destroyOnHidden
        centered={false}
        style={{ top: 80 }}
      >
        <Input
          ref={inputRef}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="搜索或跳转 — 输入命令、agent 名、run ID …"
          variant="borderless"
          style={{ fontSize: 16, padding: "16px 20px", borderBottom: "1px solid var(--hx-border-subtle)" }}
          aria-label="命令面板搜索"
        />
        <div style={{ maxHeight: 420, overflowY: "auto", padding: "8px 0" }} role="listbox">
          {filtered.length === 0 && (
            <div style={{ padding: 24, textAlign: "center", color: "var(--hx-text-tertiary)" }}>没有匹配项</div>
          )}
          {Object.entries(groups).map(([groupName, items]) => (
            <div key={groupName}>
              <div
                style={{
                  padding: "8px 20px 4px",
                  fontSize: 11,
                  fontWeight: 500,
                  textTransform: "uppercase",
                  letterSpacing: "0.05em",
                  color: "var(--hx-text-tertiary)",
                }}
              >
                {groupName}
              </div>
              {items.map((item) => {
                const globalIdx = filtered.indexOf(item);
                const active = globalIdx === activeIndex;
                return (
                  <div
                    key={item.key}
                    role="option"
                    aria-selected={active}
                    onClick={item.action}
                    onMouseEnter={() => setActiveIndex(globalIdx)}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 12,
                      padding: "8px 20px",
                      cursor: "pointer",
                      fontSize: 13,
                      borderLeft: active ? "2px solid var(--hx-color-brand-500)" : "2px solid transparent",
                      background: active ? "var(--hx-surface-selected)" : "transparent",
                    }}
                  >
                    {item.icon && <span style={{ color: "var(--hx-text-secondary)" }}>{item.icon}</span>}
                    <span style={{ flex: 1 }}>{item.label}</span>
                    {item.subtitle && (
                      <span style={{ fontSize: 11, color: "var(--hx-text-tertiary)" }}>{item.subtitle}</span>
                    )}
                    {item.shortcut && item.shortcut.length > 0 && (
                      <span style={{ display: "flex", gap: 2 }}>
                        {item.shortcut.map((s) => (
                          <span key={s} className="hx-kbd">
                            {s}
                          </span>
                        ))}
                      </span>
                    )}
                  </div>
                );
              })}
            </div>
          ))}
        </div>
        <div
          style={{
            display: "flex",
            gap: 12,
            padding: "8px 20px",
            borderTop: "1px solid var(--hx-border-subtle)",
            fontSize: 11,
            color: "var(--hx-text-tertiary)",
          }}
        >
          <span style={{ display: "flex", gap: 4, alignItems: "center" }}><span className="hx-kbd">↑↓</span> 选择</span>
          <span style={{ display: "flex", gap: 4, alignItems: "center" }}><span className="hx-kbd">↵</span> 跳转</span>
          <span style={{ display: "flex", gap: 4, alignItems: "center" }}><span className="hx-kbd">Esc</span> 关闭</span>
          <span style={{ marginLeft: "auto", display: "flex", gap: 4, alignItems: "center" }}>
            <ArrowRight size={10} strokeWidth={1.5} /> 输入 ? 查看快捷键
          </span>
        </div>
      </Modal>
    </Ctx.Provider>
  );
}
