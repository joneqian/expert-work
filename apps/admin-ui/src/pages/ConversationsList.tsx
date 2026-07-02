/**
 * Global conversations browser — the top-level operations entry
 * (``docs/design/conversation-centric-ia.md`` §3 primitive ③).
 *
 * Replaces the flat cross-agent ``/runs`` list: a conversation
 * (``thread_meta`` + its ``agent_run`` rollup) is the operational unit,
 * so the browser lists conversations across agents with status / user /
 * free-text filters and drills into ``/conversations/:threadId`` — which
 * then drills into the per-run detail. Mirrors the previous ``RunsList``
 * shell (cross-tenant banner, URL-owned ``?user_id=`` filter, debounced
 * search) so the operational UX carries over.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Checkbox,
  Empty,
  Input,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import type { TableColumnsType } from "antd";
import { AlertTriangle, Globe2, MessagesSquare, RefreshCw, Search } from "lucide-react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { listAgents } from "../api/agents";
import {
  listConversations,
  type ConversationList,
  type ConversationListItem,
  type ConversationStatus,
} from "../api/conversations";
import { ApiError } from "../api/client";
import { useTenantScope } from "../tenant/TenantScopeContext";
import { PageHeader } from "../components/PageHeader";
import { formatCompact } from "../utils/runFormat";

const { Text } = Typography;

const STATUS_COLOR: Record<string, string> = {
  active: "processing",
  paused: "warning",
  completed: "success",
  failed: "error",
  cancelled: "default",
  archived: "default",
};

const STATUS_OPTIONS: ConversationStatus[] = [
  "active",
  "paused",
  "completed",
  "failed",
  "cancelled",
  "archived",
];

export function ConversationsList() {
  const { t } = useTranslation();
  const { scope, apiTenantScope } = useTenantScope();
  const navigate = useNavigate();
  const [data, setData] = useState<ConversationList | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<ConversationStatus | undefined>(undefined);
  // Cross-agent monitoring filters — narrow to one agent, or to
  // conversations carrying ≥1 failed run ("what broke today").
  const [agentFilter, setAgentFilter] = useState<string | undefined>(undefined);
  const [errorsOnly, setErrorsOnly] = useState(false);
  const [agentOptions, setAgentOptions] = useState<string[]>([]);
  const [search, setSearch] = useState("");
  const [q, setQ] = useState<string | undefined>(undefined);
  // ``?user_id=`` drives the "member's conversations" filter — URL-owned so
  // a member page can deep-link into it and the filter survives refresh.
  const [searchParams, setSearchParams] = useSearchParams();
  const userFilter = searchParams.get("user_id") ?? undefined;

  const setUserFilter = useCallback(
    (id: string) => {
      setSearchParams(
        (prev) => {
          prev.set("user_id", id);
          return prev;
        },
        { replace: false },
      );
    },
    [setSearchParams],
  );
  const clearUserFilter = useCallback(() => {
    setSearchParams(
      (prev) => {
        prev.delete("user_id");
        return prev;
      },
      { replace: false },
    );
  }, [setSearchParams]);

  // Debounce the search box into the server ``q`` param (substring match on
  // the conversation title — server-side so it spans all pages).
  useEffect(() => {
    const handle = setTimeout(() => setQ(search.trim() || undefined), 300);
    return () => clearTimeout(handle);
  }, [search]);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await listConversations({
        tenantScope: apiTenantScope,
        status: statusFilter,
        agentName: agentFilter,
        hasError: errorsOnly,
        q,
        userId: userFilter,
      });
      setData(result);
    } catch (err) {
      const message =
        err instanceof ApiError
          ? `${err.code}: ${err.message}`
          : err instanceof Error
            ? err.message
            : "unknown error";
      setError(message);
    } finally {
      setLoading(false);
    }
  }, [apiTenantScope, statusFilter, agentFilter, errorsOnly, q, userFilter]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Agent-filter options — best-effort; a failure just leaves the
  // dropdown empty (the list itself is unaffected).
  useEffect(() => {
    let cancelled = false;
    listAgents({ tenantScope: apiTenantScope, limit: 100 })
      .then((result) => {
        if (cancelled) return;
        setAgentOptions([...new Set(result.items.map((a) => a.name))].sort());
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [apiTenantScope]);

  const isCrossTenant = data?.cross_tenant ?? false;

  const columns: TableColumnsType<ConversationListItem> = useMemo(
    () => [
      {
        title: t("conversations_page.column_conversation"),
        key: "conversation",
        render: (_: unknown, record) => (
          <Space direction="vertical" size={0}>
            <Text strong>{record.title ?? t("conversations_page.untitled")}</Text>
            <Tooltip title={record.thread_id}>
              <Text code style={{ fontSize: 11 }}>
                {record.thread_id.slice(0, 8)}…
              </Text>
            </Tooltip>
          </Space>
        ),
      },
      {
        title: t("conversations_page.column_agent"),
        dataIndex: "agent_name",
        key: "agent",
        width: 180,
        render: (name: string | null, record) => {
          if (name === null) {
            return <Text type="secondary">—</Text>;
          }
          return (
            <Space size={6}>
              <Text strong>{name}</Text>
              {record.agent_version && (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  v{record.agent_version}
                </Text>
              )}
            </Space>
          );
        },
      },
      {
        title: t("conversations_page.column_user"),
        dataIndex: "user_id",
        key: "user",
        width: 130,
        render: (uid: string | null) => {
          if (!uid) return <Text type="secondary">—</Text>;
          // Click filters the list to this user (URL ?user_id=…).
          // stopPropagation so it doesn't also trigger the row navigation.
          return (
            <Tooltip title={t("conversations_page.filter_user_tip")}>
              <span
                role="button"
                tabIndex={0}
                data-testid={`conversation-user-${uid}`}
                onClick={(e) => {
                  e.stopPropagation();
                  setUserFilter(uid);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.stopPropagation();
                    setUserFilter(uid);
                  }
                }}
                style={{ cursor: "pointer" }}
              >
                <Text code style={{ fontSize: 12, color: "var(--hx-accent-cyan, #13c2c2)" }}>
                  {uid.slice(0, 8)}…
                </Text>
              </span>
            </Tooltip>
          );
        },
      },
      {
        title: t("conversations_page.column_status"),
        dataIndex: "status",
        key: "status",
        width: 120,
        render: (status: string) => (
          <Tag color={STATUS_COLOR[status] ?? "default"}>{status}</Tag>
        ),
      },
      {
        title: t("conversations_page.column_runs"),
        key: "runs",
        width: 110,
        render: (_: unknown, record) => (
          <Space size={6}>
            <Text>{record.run_count}</Text>
            {record.error_count > 0 && (
              <Tooltip title={t("conversations_page.error_count", { count: record.error_count })}>
                <Space size={2} data-testid={`conversations-page-error-${record.thread_id}`}>
                  <AlertTriangle
                    size={13}
                    strokeWidth={1.5}
                    color="var(--hx-status-error, #f5222d)"
                  />
                </Space>
              </Tooltip>
            )}
            {record.pending_count > 0 && (
              <Tooltip
                title={t("conversations_page.pending_count", { count: record.pending_count })}
              >
                <Tag color="warning" style={{ marginInlineEnd: 0 }}>
                  {record.pending_count}
                </Tag>
              </Tooltip>
            )}
          </Space>
        ),
      },
      {
        title: t("conversations_page.column_tokens"),
        key: "tokens",
        width: 90,
        render: (_: unknown, record) => {
          const tk = record.tokens;
          if (!tk || tk.total_tokens === 0) return <Text type="secondary">—</Text>;
          return (
            <Tooltip
              title={t("runs_page.tokens_tip", {
                input: tk.input_tokens,
                output: tk.output_tokens,
                calls: tk.llm_calls,
              })}
            >
              <Text style={{ fontSize: 12 }} data-testid={`conversation-tokens-${record.thread_id}`}>
                {formatCompact(tk.total_tokens)}
              </Text>
            </Tooltip>
          );
        },
      },
      {
        title: t("conversations_page.column_last_active"),
        dataIndex: "last_run_at",
        key: "last_run_at",
        width: 190,
        render: (iso: string | null) =>
          iso ? (
            <Text type="secondary" style={{ fontSize: 12 }}>
              {new Date(iso).toLocaleString()}
            </Text>
          ) : (
            <Text type="secondary">—</Text>
          ),
      },
    ],
    [t, setUserFilter],
  );

  return (
    <div>
      <PageHeader
        icon={<MessagesSquare size={18} strokeWidth={1.5} />}
        title={t("conversations_page.page_title")}
        actions={
          <>
            {isCrossTenant && (
              <Tag
                icon={<Globe2 size={12} strokeWidth={1.5} />}
                color="purple"
                data-testid="cross-tenant-banner"
              >
                {t("conversations_page.cross_tenant_banner")}
              </Tag>
            )}
            {userFilter && (
              <Tag
                closable
                onClose={clearUserFilter}
                color="cyan"
                data-testid="conversations-user-filter-chip"
              >
                {t("conversations_page.filter_user_active", { user: userFilter.slice(0, 8) })}
              </Tag>
            )}
            <Checkbox
              checked={errorsOnly}
              onChange={(e) => setErrorsOnly(e.target.checked)}
              data-testid="conversations-errors-only"
            >
              {t("conversations_page.filter_errors_only")}
            </Checkbox>
            <Input
              allowClear
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={t("conversations_page.search_placeholder")}
              aria-label={t("conversations_page.search_placeholder")}
              prefix={<Search size={14} strokeWidth={1.5} />}
              style={{ width: 220 }}
              data-testid="conversations-search"
            />
            <Select<string>
              allowClear
              showSearch
              value={agentFilter}
              onChange={(v) => setAgentFilter(v || undefined)}
              placeholder={t("conversations_page.filter_agent")}
              aria-label={t("conversations_page.filter_agent")}
              style={{ width: 190 }}
              data-testid="conversations-agent-filter"
              options={agentOptions.map((name) => ({ value: name, label: name }))}
            />
            <Select<ConversationStatus | "all">
              value={statusFilter ?? "all"}
              onChange={(v) => setStatusFilter(v === "all" ? undefined : (v as ConversationStatus))}
              style={{ width: 160 }}
              aria-label={t("conversations_page.filter_status")}
              data-testid="conversations-status-filter"
              options={[
                { value: "all", label: t("conversations_page.filter_status_all") },
                ...STATUS_OPTIONS.map((s) => ({ value: s, label: s })),
              ]}
            />
            <button
              type="button"
              onClick={refresh}
              disabled={loading}
              aria-label={t("common.refresh")}
              data-testid="conversations-refresh"
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
                padding: "4px 10px",
                border: "1px solid var(--hx-border-default)",
                borderRadius: 6,
                background: "var(--hx-surface-raised)",
                color: "var(--hx-text-primary)",
                fontSize: 13,
                cursor: loading ? "wait" : "pointer",
              }}
            >
              <RefreshCw size={14} strokeWidth={1.5} />
              {loading ? t("common.loading") : t("common.refresh")}
            </button>
          </>
        }
      />

      {error !== null && (
        <Alert
          type="error"
          showIcon
          message={t("conversations_page.failed_to_load")}
          description={error}
          style={{ marginBottom: 16 }}
          data-testid="conversations-error"
        />
      )}

      <Table<ConversationListItem>
        columns={columns}
        dataSource={data?.items ?? []}
        rowKey={(record) => record.thread_id}
        loading={loading}
        pagination={{
          total: data?.total ?? 0,
          showSizeChanger: false,
          pageSize: 50,
        }}
        onRow={(record) => ({
          onClick: () => navigate(`/conversations/${encodeURIComponent(record.thread_id)}`),
          style: { cursor: "pointer" },
        })}
        locale={{
          emptyText: (
            <Empty
              description={
                scope === "*"
                  ? t("conversations_page.empty_cross")
                  : t("conversations_page.empty_home")
              }
            />
          ),
        }}
        data-testid="conversations-table"
      />
    </div>
  );
}
