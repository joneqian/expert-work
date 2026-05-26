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
  Breadcrumb,
  Empty,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import type { TableColumnsType } from "antd";
import { Activity, ChevronRight, Globe2, RefreshCw } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { listRuns, type RunList, type RunListItem, type RunStatus } from "../api/runs";
import { ApiError } from "../api/client";
import { useTenantScope } from "../tenant/TenantScopeContext";

const { Text } = Typography;

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

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await listRuns({ tenantScope: apiTenantScope, status: statusFilter });
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
  }, [apiTenantScope, statusFilter]);

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
        render: (status: string) => (
          // Tag colour + literal text so colour is not the only signal (axe).
          <Tag color={STATUS_COLOR[status] ?? "default"}>{status}</Tag>
        ),
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
      <div className="hx-page-header">
        <Breadcrumb
          separator={<ChevronRight size={12} strokeWidth={1.5} />}
          items={[{ title: t("common.home") }, { title: t("runs_page.page_title") }]}
        />
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            marginTop: 8,
            marginBottom: 16,
          }}
        >
          <Activity size={20} strokeWidth={1.5} />
          <h1 style={{ margin: 0 }}>{t("runs_page.page_title")}</h1>
          {isCrossTenant && (
            <Tag
              icon={<Globe2 size={12} strokeWidth={1.5} />}
              color="purple"
              data-testid="cross-tenant-banner"
            >
              {t("runs_page.cross_tenant_banner")}
            </Tag>
          )}
          <span style={{ flex: 1 }} />
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
        </div>
      </div>

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
