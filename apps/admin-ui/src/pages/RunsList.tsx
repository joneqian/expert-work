/**
 * Runs list page — Stream H.3 PR 1.
 *
 * Cross-thread index backed by the new ``GET /v1/runs`` endpoint
 * (Mini-ADR H-6). Drops the previous ``ComingSoon`` placeholder under
 * ``/runs``. Mirrors the ``AgentsList`` shell so cross-tenant banner,
 * empty state, error Alert, and refresh button stay visually
 * consistent.
 *
 * Filters in M0: ``status`` (Antd Select). Search-by-agent_name moves
 * to a follow-up — Mini-ADR J-41 didn't index agent_name on agent_run,
 * so the server-side JOIN already pays a per-row cost; adding text
 * search requires a SQL JOIN in M1 (see § 6.5.5 (b)).
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
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
import { Activity, AlertTriangle, Globe2, RefreshCw, Search } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { listRuns, type RunList, type RunListItem, type RunStatus } from "../api/runs";
import { ApiError } from "../api/client";
import { useTenantScope } from "../tenant/TenantScopeContext";
import { PageHeader } from "../components/PageHeader";

const { Text } = Typography;

type TFn = (key: string, opts?: Record<string, unknown>) => string;

/** Compact wall-clock duration from a run's created→finished span. A run
 *  with no ``finished_at`` is still in flight → localized "running". */
function formatDuration(t: TFn, createdIso: string, finishedIso: string | null): string {
  if (!finishedIso) return t("runs_page.duration_running");
  const seconds = Math.max(
    0,
    Math.round((new Date(finishedIso).getTime() - new Date(createdIso).getTime()) / 1000),
  );
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${seconds % 60}s`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

/** 1234 → "1.2k", 2_000_000 → "2.0M" — keeps the token column narrow. */
function formatCompact(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}

const STATUS_COLOR: Record<string, string> = {
  pending: "default",
  running: "processing",
  paused: "warning",
  success: "success",
  error: "error",
  timeout: "error",
  interrupted: "default",
};

const STATUS_OPTIONS: RunStatus[] = [
  "running",
  "paused",
  "success",
  "error",
  "timeout",
  "interrupted",
  "pending",
];

export function RunsList() {
  const { t } = useTranslation();
  const { scope, apiTenantScope } = useTenantScope();
  const navigate = useNavigate();
  const [data, setData] = useState<RunList | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<RunStatus | undefined>(undefined);
  const [search, setSearch] = useState("");
  const [q, setQ] = useState<string | undefined>(undefined);

  // Debounce the search box into the server ``q`` param (substring match on
  // run_id / thread_id — server-side so it spans all pages, not just the one
  // loaded).
  useEffect(() => {
    const handle = setTimeout(() => setQ(search.trim() || undefined), 300);
    return () => clearTimeout(handle);
  }, [search]);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await listRuns({ tenantScope: apiTenantScope, status: statusFilter, q });
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
  }, [apiTenantScope, statusFilter, q]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const isCrossTenant = data?.cross_tenant ?? false;

  const columns: TableColumnsType<RunListItem> = useMemo(
    () => [
      {
        title: t("runs_page.column_run_id"),
        dataIndex: "run_id",
        key: "run_id",
        width: 200,
        render: (id: string) => (
          <Tooltip title={id}>
            <Text code style={{ fontSize: 12 }}>
              {id.slice(0, 8)}…
            </Text>
          </Tooltip>
        ),
      },
      {
        title: t("runs_page.column_status"),
        dataIndex: "status",
        key: "status",
        width: 130,
        render: (status: string, record) => {
          // Tag colour + literal text so colour is not the only signal (axe).
          const tag = <Tag color={STATUS_COLOR[status] ?? "default"}>{status}</Tag>;
          if (!record.error) return tag;
          // Surface the failure reason inline — the list was previously silent
          // about *why* a run errored.
          return (
            <Tooltip title={record.error}>
              <Space size={4} data-testid={`run-error-${record.run_id}`}>
                {tag}
                <AlertTriangle size={13} strokeWidth={1.5} color="var(--hx-status-error, #f5222d)" />
              </Space>
            </Tooltip>
          );
        },
      },
      {
        title: t("runs_page.column_agent"),
        dataIndex: "agent_name",
        key: "agent",
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
        title: t("runs_page.column_duration"),
        key: "duration",
        width: 100,
        render: (_: unknown, record) => (
          <Text type="secondary" style={{ fontSize: 12 }}>
            {formatDuration(t, record.created_at, record.finished_at)}
          </Text>
        ),
      },
      {
        title: t("runs_page.column_tokens"),
        key: "tokens",
        width: 100,
        render: (_: unknown, record) => {
          const tk = record.tokens;
          if (!tk || tk.total_tokens === 0) {
            return <Text type="secondary">—</Text>;
          }
          return (
            <Tooltip
              title={t("runs_page.tokens_tip", {
                input: tk.input_tokens,
                output: tk.output_tokens,
                calls: tk.llm_calls,
              })}
            >
              <Text style={{ fontSize: 12 }} data-testid={`run-tokens-${record.run_id}`}>
                {formatCompact(tk.total_tokens)}
              </Text>
            </Tooltip>
          );
        },
      },
      {
        title: t("runs_page.column_thread"),
        dataIndex: "thread_id",
        key: "thread",
        width: 140,
        render: (id: string) => (
          <Tooltip title={id}>
            <Text code style={{ fontSize: 12 }}>
              {id.slice(0, 8)}…
            </Text>
          </Tooltip>
        ),
      },
      {
        title: t("runs_page.column_created"),
        dataIndex: "created_at",
        key: "created_at",
        width: 200,
        render: (iso: string) => (
          <Text type="secondary" style={{ fontSize: 12 }}>
            {new Date(iso).toLocaleString()}
          </Text>
        ),
      },
    ],
    [t],
  );

  return (
    <div>
      <PageHeader
        icon={<Activity size={18} strokeWidth={1.5} />}
        title={t("runs_page.page_title")}
        actions={
          <>
            {isCrossTenant && (
              <Tag
                icon={<Globe2 size={12} strokeWidth={1.5} />}
                color="purple"
                data-testid="cross-tenant-banner"
              >
                {t("runs_page.cross_tenant_banner")}
              </Tag>
            )}
            <Input
              allowClear
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={t("runs_page.search_placeholder")}
              aria-label={t("runs_page.search_placeholder")}
              prefix={<Search size={14} strokeWidth={1.5} />}
              style={{ width: 220 }}
              data-testid="runs-search"
            />
            <Select<RunStatus | "all">
              value={statusFilter ?? "all"}
              onChange={(v) => setStatusFilter(v === "all" ? undefined : (v as RunStatus))}
              style={{ width: 160 }}
              aria-label={t("runs_page.filter_status")}
              data-testid="runs-status-filter"
              options={[
                { value: "all", label: t("runs_page.filter_status_all") },
                ...STATUS_OPTIONS.map((s) => ({ value: s, label: s })),
              ]}
            />
            <button
              type="button"
              onClick={refresh}
              disabled={loading}
              aria-label={t("common.refresh")}
              data-testid="runs-refresh"
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
          message={t("runs_page.failed_to_load")}
          description={error}
          style={{ marginBottom: 16 }}
          data-testid="runs-error"
        />
      )}

      <Table<RunListItem>
        columns={columns}
        dataSource={data?.items ?? []}
        rowKey={(record) => record.run_id}
        loading={loading}
        pagination={{
          total: data?.total ?? 0,
          showSizeChanger: false,
          pageSize: 50,
        }}
        onRow={(record) => ({
          onClick: () =>
            navigate(
              `/runs/${encodeURIComponent(record.thread_id)}/${encodeURIComponent(record.run_id)}`,
            ),
          style: { cursor: "pointer" },
        })}
        locale={{
          emptyText: (
            <Empty
              description={
                scope === "*"
                  ? t("runs_page.empty_cross")
                  : t("runs_page.empty_home")
              }
            />
          ),
        }}
        data-testid="runs-table"
      />

      <p style={{ marginTop: 16, fontSize: 12, color: "var(--hx-text-tertiary)" }}>
        {t("runs_page.detail_hint")}{" "}
        <Link to="/agents">{t("runs_page.detail_hint_link")}</Link>
      </p>
    </div>
  );
}
