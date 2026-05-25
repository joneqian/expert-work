/**
 * Agents list page — Stream H.1b PR 1 (read) + H.2 PR 2 (create).
 *
 * Hooks straight into the live ``/v1/agents`` endpoint and threads the
 * current :ref:`TenantScopeContext` through so a system_admin's
 * "All tenants" choice flips to the cross-tenant aggregate without
 * extra plumbing.
 *
 * H.2 PR 2 adds the **Create** button + ``CreateAgentDrawer`` (Monaco
 * YAML); on success the list refreshes and the new agent's detail page
 * loads. Cmd+K real routes + manifest upload are deferred follow-ups.
 */
import { useCallback, useEffect, useState } from "react";
import { Alert, Breadcrumb, Empty, Space, Table, Tag, Tooltip, Typography } from "antd";
import type { TableColumnsType } from "antd";
import { Bot, ChevronRight, Globe2, Plus, RefreshCw } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { listAgents, type AgentRecord, type AgentList } from "../api/agents";
import { ApiError } from "../api/client";
import { useTenantScope } from "../tenant/TenantScopeContext";
import { CreateAgentDrawer } from "../components/CreateAgentDrawer";

const { Text } = Typography;

const STATUS_COLOR: Record<string, string> = {
  active: "success",
  draft: "warning",
  archived: "default",
  deleted: "error",
};

export function AgentsList() {
  const { t } = useTranslation();
  const { scope, apiTenantScope } = useTenantScope();
  const navigate = useNavigate();
  const [data, setData] = useState<AgentList | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);

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
      title: t("agents_page.column_name"),
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
      title: t("agents_page.column_status"),
      dataIndex: "status",
      key: "status",
      width: 120,
      render: (status: string) => (
        <Tag color={STATUS_COLOR[status] ?? "default"}>{status}</Tag>
      ),
    },
    {
      title: t("agents_page.column_tenant"),
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
      title: t("agents_page.column_created"),
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
          items={[{ title: t("common.home") }, { title: t("agents_page.page_title") }]}
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
          <h1 style={{ margin: 0 }}>{t("agents_page.page_title")}</h1>
          {isCrossTenant && (
            <Tag
              icon={<Globe2 size={12} strokeWidth={1.5} />}
              color="purple"
              data-testid="cross-tenant-banner"
            >
              {t("agents_page.cross_tenant_banner")}
            </Tag>
          )}
          <span style={{ flex: 1 }} />
          <button
            type="button"
            onClick={refresh}
            disabled={loading}
            aria-label={t("common.refresh")}
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
            {loading ? t("common.loading") : t("common.refresh")}
          </button>
          <button
            type="button"
            onClick={() => setCreateOpen(true)}
            data-testid="agents-create"
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              padding: "4px 10px",
              border: "1px solid var(--hx-color-brand-500)",
              borderRadius: 6,
              background: "var(--hx-color-brand-500)",
              color: "var(--hx-on-brand)",
              fontSize: 13,
              cursor: "pointer",
            }}
          >
            <Plus size={14} strokeWidth={1.75} />
            {t("agents_page.create")}
          </button>
        </div>
      </div>

      {error && (
        <Alert
          type="error"
          showIcon
          message={t("agents_page.failed_to_load")}
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
                  ? t("agents_page.empty_cross")
                  : t("agents_page.empty_home")
              }
            />
          ),
        }}
        data-testid="agents-table"
      />

      <CreateAgentDrawer
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={(created) => {
          setCreateOpen(false);
          const { name, version } = created.record;
          navigate(
            `/agents/${encodeURIComponent(name)}/${encodeURIComponent(version)}/overview`,
          );
        }}
      />
    </div>
  );
}
