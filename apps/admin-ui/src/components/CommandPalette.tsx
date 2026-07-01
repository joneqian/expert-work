/**
 * Command palette (Cmd+K) — Stream H.1b PR 2a.
 *
 * Wired to the live Agents API (drops the demo's ``mockAgents``).
 * Agents are loaded once when the palette opens; we keep a short
 * client-side TTL so re-opening it inside the same tab feels instant
 * but a freshly created Agent shows up after at most a few seconds.
 * Tenant scope flows from :ref:`TenantScopeContext` so a system_admin
 * in "All tenants" mode sees Agents across every tenant they can
 * reach.
 *
 * Static jumps (Runs / Curation / Memory / Skills / Triggers /
 * Settings · API Keys) point at the real router paths already wired
 * by the H.1b scaffold — entries here are the single source of truth
 * for the keyboard-first navigation surface.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { Modal, Input, type InputRef } from "antd";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  Bot,
  Activity,
  CheckSquare,
  FlaskConical,
  Brain,
  BookOpen,
  FileText,
  Clock,
  Key,
  ListChecks,
  Package,
  Plus,
  ArrowRight,
  Cog,
  Network,
  LineChart,
  ShieldCheck,
  Store,
  Webhook,
} from "lucide-react";

import { listAgents, type AgentRecord } from "../api/agents";
import { useAuth } from "../auth/AuthContext";
import { useTenantScope } from "../tenant/TenantScopeContext";
import { groupForPath, visibleGroups } from "./navModel";

interface CmdItem {
  group: string;
  key: string;
  label: ReactNode;
  searchText: string;
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

/** Re-fetch the Agents list at most every ``AGENT_TTL_MS`` ms — same
 *  tab + same tenant scope reuses the cached list, avoiding a blank
 *  flash on every Cmd+K press. */
const AGENT_TTL_MS = 60_000;

export function CommandPaletteProvider({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const { status, identity } = useAuth();
  const { scope, apiTenantScope } = useTenantScope();
  const isSystemAdmin = identity?.isSystemAdmin ?? false;

  const [isOpen, setIsOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const [agents, setAgents] = useState<AgentRecord[]>([]);
  const lastFetchRef = useRef<{ at: number; scope: string | undefined } | null>(null);
  const inputRef = useRef<InputRef>(null);
  const nav = useNavigate();

  const open = useCallback(() => {
    setIsOpen(true);
    setQuery("");
    setActiveIndex(0);
  }, []);
  const close = useCallback(() => setIsOpen(false), []);

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

  useEffect(() => {
    if (isOpen) {
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [isOpen]);

  // Refresh agents whenever the palette is opened — but only if the
  // cache is stale OR the tenant scope changed since the last fetch.
  useEffect(() => {
    if (!isOpen || status !== "authenticated") return;
    const now = Date.now();
    const last = lastFetchRef.current;
    if (last && last.scope === apiTenantScope && now - last.at < AGENT_TTL_MS) {
      return;
    }
    let cancelled = false;
    void listAgents({ tenantScope: apiTenantScope, limit: 50 })
      .then((result) => {
        if (cancelled) return;
        setAgents(result.items);
        lastFetchRef.current = { at: now, scope: apiTenantScope };
      })
      .catch(() => {
        // Failing silently is fine — the palette still works for static
        // jumps + actions; the Agents-list page will surface real
        // errors to the user.
      });
    return () => {
      cancelled = true;
    };
  }, [isOpen, status, apiTenantScope]);

  const allItems: CmdItem[] = useMemo(() => {
    const items: CmdItem[] = [];

    agents.forEach((a) => {
      const subtitle = `${a.version} · ${a.status}`;
      items.push({
        group: t("cmdk.group_agents"),
        key: `agent-${a.tenant_id}-${a.id}`,
        label: a.name,
        searchText: `${a.name} ${subtitle} ${a.tenant_id}`,
        subtitle,
        icon: <Bot size={16} strokeWidth={1.5} />,
        action: () => {
          nav(
            `/agents/${encodeURIComponent(a.name)}/${encodeURIComponent(a.version)}/overview`,
          );
          close();
        },
      });
    });

    const jumpItems = [
      { key: "go-agents", label: t("agents_page.page_title"), path: "/agents", icon: <Bot size={16} strokeWidth={1.5} />, sc: ["g", "a"] },
      { key: "go-runs", label: t("cmdk.label_runs"), path: "/runs", icon: <Activity size={16} strokeWidth={1.5} />, sc: ["g", "r"] },
      { key: "go-approvals", label: t("cmdk.label_approvals"), path: "/approvals", icon: <ListChecks size={16} strokeWidth={1.5} />, sc: ["g", "p"] },
      { key: "go-curation", label: t("cmdk.label_curation"), path: "/curation", icon: <CheckSquare size={16} strokeWidth={1.5} />, sc: ["g", "c"] },
      { key: "go-eval", label: t("cmdk.label_eval"), path: "/eval-runs", icon: <FlaskConical size={16} strokeWidth={1.5} />, sc: ["g", "e"] },
      { key: "go-memory", label: t("cmdk.label_memory"), path: "/memory", icon: <Brain size={16} strokeWidth={1.5} />, sc: ["g", "m"] },
      { key: "go-artifacts", label: t("cmdk.label_artifacts"), path: "/artifacts", icon: <Package size={16} strokeWidth={1.5} />, sc: ["g", "f"] },
      { key: "go-knowledge", label: t("cmdk.label_knowledge"), path: "/knowledge", icon: <BookOpen size={16} strokeWidth={1.5} />, sc: ["g", "k"] },
      { key: "go-skills", label: t("cmdk.label_skills"), path: "/skills", icon: <FileText size={16} strokeWidth={1.5} />, sc: ["g", "s"] },
      { key: "go-agent-template-marketplace", label: t("cmdk.label_agent_template_marketplace"), path: "/agent-template-marketplace", icon: <Store size={16} strokeWidth={1.5} />, sc: ["g", "b"] },
      { key: "go-triggers", label: t("cmdk.label_triggers"), path: "/triggers", icon: <Clock size={16} strokeWidth={1.5} />, sc: ["g", "t"] },
      { key: "go-webhooks", label: t("cmdk.label_webhooks"), path: "/webhooks", icon: <Webhook size={16} strokeWidth={1.5} />, sc: ["g", "w"] },
      { key: "go-api-keys", label: t("cmdk.label_settings_api_keys"), path: "/settings/api-keys", icon: <Key size={16} strokeWidth={1.5} />, sc: [] as string[] },
      { key: "go-egress-audit", label: t("nav.egress_audit"), path: "/settings/egress-audit", icon: <Network size={16} strokeWidth={1.5} />, sc: [] as string[] },
      { key: "go-platform-users", label: t("cmdk.label_settings_platform_users"), path: "/settings/platform-users", icon: <ShieldCheck size={16} strokeWidth={1.5} />, sc: [] as string[] },
      { key: "go-observability", label: t("nav.observability"), path: "/settings/observability", icon: <LineChart size={16} strokeWidth={1.5} />, sc: [] as string[] },
    ];
    // Same gating as the sidebar (shared ``navModel`` helpers): platform
    // jumps only at the platform level for system_admins, tenant jumps
    // only at a tenant level. Items not in a nav group (none today) stay.
    const visible = visibleGroups(scope, isSystemAdmin);
    jumpItems
      .filter((g) => {
        const group = groupForPath(g.path);
        return group === null || visible.includes(group);
      })
      .forEach((g) => {
      items.push({
        group: t("cmdk.group_jump"),
        key: g.key,
        label: g.label,
        searchText: g.label,
        icon: g.icon,
        shortcut: g.sc,
        action: () => {
          nav(g.path);
          close();
        },
      });
    });

    items.push({
      group: t("cmdk.group_action"),
      key: "create-agent",
      label: t("cmdk.action_create_agent"),
      searchText: t("cmdk.action_create_agent"),
      icon: <Plus size={16} strokeWidth={1.5} />,
      shortcut: ["N"],
      action: () => {
        nav("/agents?action=create");
        close();
      },
    });
    items.push({
      group: t("cmdk.group_action"),
      key: "create-api-key",
      label: t("cmdk.action_create_api_key"),
      searchText: t("cmdk.action_create_api_key"),
      icon: <Key size={16} strokeWidth={1.5} />,
      action: () => {
        nav("/settings/api-keys?action=create");
        close();
      },
    });
    items.push({
      group: t("cmdk.group_action"),
      key: "open-settings",
      label: t("cmdk.action_open_settings"),
      searchText: t("cmdk.action_open_settings"),
      icon: <Cog size={16} strokeWidth={1.5} />,
      action: () => {
        nav("/settings/api-keys");
        close();
      },
    });

    return items;
  }, [agents, nav, close, t, scope, isSystemAdmin]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return allItems.slice(0, 20);
    return allItems
      .filter((i) => i.searchText.toLowerCase().includes(q))
      .slice(0, 30);
  }, [allItems, query]);

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
          placeholder={t("cmdk.placeholder")}
          variant="borderless"
          style={{ fontSize: 16, padding: "16px 20px", borderBottom: "1px solid var(--hx-border-subtle)" }}
          aria-label={t("cmdk.aria_label")}
          data-testid="cmdk-input"
        />
        <div style={{ maxHeight: 420, overflowY: "auto", padding: "8px 0" }} role="listbox">
          {filtered.length === 0 && (
            <div style={{ padding: 24, textAlign: "center", color: "var(--hx-text-tertiary)" }}>
              {t("cmdk.no_matches")}
            </div>
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
          <span style={{ display: "flex", gap: 4, alignItems: "center" }}><span className="hx-kbd">↑↓</span> {t("cmdk.hint_select")}</span>
          <span style={{ display: "flex", gap: 4, alignItems: "center" }}><span className="hx-kbd">↵</span> {t("cmdk.hint_jump")}</span>
          <span style={{ display: "flex", gap: 4, alignItems: "center" }}><span className="hx-kbd">Esc</span> {t("cmdk.hint_close")}</span>
          <span style={{ marginLeft: "auto", display: "flex", gap: 4, alignItems: "center" }}>
            <ArrowRight size={10} strokeWidth={1.5} /> {t("cmdk.hint_shortcuts")}
          </span>
        </div>
      </Modal>
    </Ctx.Provider>
  );
}
