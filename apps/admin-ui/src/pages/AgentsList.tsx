/**
 * Agents list page — Stream H.1b PR 1.
 *
 * Hooks straight into the live ``/v1/agents`` endpoint and threads the
 * current :ref:`TenantScopeContext` through so a system_admin's
 * "All tenants" choice flips to the cross-tenant aggregate without
 * extra plumbing.
 *
 * H.2 will flesh out the rest of the Agents IA (create modal, manifest
 * upload, Cmd+K hooks, real-time status). For now the page is purely
 * read-only — but the read path is end-to-end real, which is the H.1b
 * exit criterion.
 */
import { useCallback, useEffect, useState } from "react";
import { Alert, Breadcrumb, Empty, Space, Table, Tag, Tooltip, Typography } from "antd";
import type { TableColumnsType } from "antd";
import { Bot, ChevronRight, Globe2, RefreshCw } from "lucide-react";

import { listAgents, type AgentRecord, type AgentList } from "../api/agents";
import { ApiError } from "../api/client";
import { useTenantScope } from "../tenant/TenantScopeContext";

const { Text } = Typography;

const STATUS_COLOR: Record<string, string> = {
  active: "success",
  draft: "warning",
  archived: "default",
  deleted: "error",
};

export function AgentsList() {
  const { scope, apiTenantScope } = useTenantScope();
  const [data, setData] = useState<AgentList | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await listAgents({ tenantScope: apiTenantScope });
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
  }, [apiTenantScope]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const columns: TableColumnsType<AgentRecord> = [
    {
      title: "Name",
      dataIndex: "name",
      key: "name",
      render: (name: string, record) => (
        <Space size={6}>
          <Bot size={14} strokeWidth={1.5} />
          <strong>{name}</strong>
          <Text type="secondary" style={{ fontSize: 12 }}>
            v{record.version}
          </Text>
        </Space>
      ),
    },
    {
      title: "Status",
      dataIndex: "status",
      key: "status",
      width: 120,
      render: (status: string) => (
        <Tag color={STATUS_COLOR[status] ?? "default"}>{status}</Tag>
      ),
    },
    {
      title: "Tenant",
      dataIndex: "tenant_id",
      key: "tenant_id",
      width: 160,
      render: (tenantId: string) => (
        <Tooltip title={tenantId}>
          <Text code style={{ fontSize: 12 }}>
            {tenantId.slice(0, 8)}…
          </Text>
        </Tooltip>
      ),
    },
    {
      title: "Created",
      dataIndex: "created_at",
      key: "created_at",
      width: 200,
      render: (iso: string) => (
        <Text type="secondary" style={{ fontSize: 12 }}>
          {new Date(iso).toLocaleString()}
        </Text>
      ),
    },
  ];

  const isCrossTenant = data?.cross_tenant ?? false;

  return (
    <div>
      <div className="hx-page-header">
        <Breadcrumb
          separator={<ChevronRight size={12} strokeWidth={1.5} />}
          items={[{ title: "Home" }, { title: "Agents" }]}
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
          <h1 style={{ margin: 0 }}>Agents</h1>
          {isCrossTenant && (
            <Tag
              icon={<Globe2 size={12} strokeWidth={1.5} />}
              color="purple"
              data-testid="cross-tenant-banner"
            >
              cross-tenant view
            </Tag>
          )}
          <span style={{ flex: 1 }} />
          <button
            type="button"
            onClick={refresh}
            disabled={loading}
            aria-label="Refresh"
            data-testid="agents-refresh"
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
            {loading ? "Loading…" : "Refresh"}
          </button>
        </div>
      </div>

      {error && (
        <Alert
          type="error"
          showIcon
          message="Failed to load agents"
          description={error}
          style={{ marginBottom: 16 }}
          data-testid="agents-error"
        />
      )}

      <Table<AgentRecord>
        columns={columns}
        dataSource={data?.items ?? []}
        rowKey={(record) => `${record.tenant_id}/${record.id}`}
        loading={loading}
        pagination={{
          total: data?.total ?? 0,
          showSizeChanger: false,
          pageSize: 50,
        }}
        locale={{
          emptyText: (
            <Empty
              description={
                scope === "*"
                  ? "No agents across all tenants yet."
                  : "No agents in this tenant. Use POST /v1/agents to create one."
              }
            />
          ),
        }}
        data-testid="agents-table"
      />
    </div>
  );
}
