/**
 * Agents list page — Stream H.1b PR 1 (read) + H.2 PR 2 (create).
 *
 * Hooks straight into the live ``/v1/agents`` endpoint and threads the
 * current :ref:`TenantScopeContext` through so a system_admin's
 * "All tenants" choice flips to the cross-tenant aggregate without
 * extra plumbing.
 *
 * H.2 PR 2 adds the **Create** button + ``CreateAgentModal`` (Monaco
 * YAML); on success the list refreshes and the new agent's detail page
 * loads.
 *
 * Product-grade pass: rows open the detail page, status is localised, the
 * owner (``created_by``) shows, the raw tenant column appears only in the
 * cross-tenant view, and a name search + status filter + per-row quick
 * actions (playground / edit / runs) make the list usable at scale.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Dropdown,
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
import {
  Activity,
  Bot,
  Globe2,
  MoreHorizontal,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  Store,
} from "lucide-react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { listAgents, type AgentRecord, type AgentList } from "../api/agents";
import { ApiError } from "../api/client";
import { useTenantScope } from "../tenant/TenantScopeContext";
import { CreateAgentModal } from "../components/CreateAgentModal";
import { PageHeader } from "../components/PageHeader";

const { Text } = Typography;

const STATUS_COLOR: Record<string, string> = {
  active: "success",
  draft: "warning",
  archived: "default",
  deleted: "error",
};

//: Statuses offered in the filter — the closed set the backend assigns.
const STATUS_OPTIONS = ["active", "draft", "archived", "deleted"] as const;

function agentPath(record: AgentRecord, tab: string): string {
  return `/agents/${encodeURIComponent(record.name)}/${encodeURIComponent(record.version)}/${tab}`;
}

export function AgentsList() {
  const { t } = useTranslation();
  const { scope, apiTenantScope } = useTenantScope();
  const navigate = useNavigate();
  const [data, setData] = useState<AgentList | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [nameFilter, setNameFilter] = useState<string>("");
  const [statusFilter, setStatusFilter] = useState<string | undefined>(undefined);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await listAgents({
        tenantScope: apiTenantScope,
        name: nameFilter.trim() || undefined,
        status: statusFilter,
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
  }, [apiTenantScope, nameFilter, statusFilter]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const isCrossTenant = data?.cross_tenant ?? false;

  const statusLabel = useCallback(
    (status: string) => t(`agents_page.status_${status}`, { defaultValue: status }),
    [t],
  );

  const columns: TableColumnsType<AgentRecord> = useMemo(() => {
    const cols: TableColumnsType<AgentRecord> = [
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
          <Tag color={STATUS_COLOR[status] ?? "default"}>{statusLabel(status)}</Tag>
        ),
      },
      {
        title: t("agents_page.column_owner"),
        dataIndex: "created_by",
        key: "created_by",
        width: 200,
        render: (owner: string) =>
          owner ? (
            <Text style={{ fontSize: 13 }}>{owner}</Text>
          ) : (
            <Text type="secondary">—</Text>
          ),
      },
    ];

    // Raw tenant UUID is noise inside a single tenant (every row is the same);
    // only a system_admin's cross-tenant aggregate needs it to tell rows apart.
    if (isCrossTenant) {
      cols.push({
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
      });
    }

    cols.push(
      {
        title: t("agents_page.column_created"),
        dataIndex: "created_at",
        key: "created_at",
        width: 190,
        render: (iso: string) => (
          <Text type="secondary" style={{ fontSize: 12 }}>
            {new Date(iso).toLocaleString()}
          </Text>
        ),
      },
      {
        title: t("agents_page.column_actions"),
        key: "actions",
        width: 64,
        align: "right",
        render: (_: unknown, record) => (
          // Stop the cell click bubbling to the row's navigate-to-overview.
          <span onClick={(e) => e.stopPropagation()}>
            <Dropdown
              trigger={["click"]}
              menu={{
                items: [
                  {
                    key: "playground",
                    icon: <Play size={14} strokeWidth={1.5} />,
                    label: t("agents_page.action_playground"),
                  },
                  {
                    key: "manifest",
                    icon: <Pencil size={14} strokeWidth={1.5} />,
                    label: t("agents_page.action_edit"),
                  },
                  {
                    key: "runs",
                    icon: <Activity size={14} strokeWidth={1.5} />,
                    label: t("agents_page.action_runs"),
                  },
                ],
                onClick: ({ key }) => navigate(agentPath(record, key)),
              }}
            >
              <Button
                type="text"
                size="small"
                aria-label={t("agents_page.column_actions")}
                icon={<MoreHorizontal size={16} strokeWidth={1.5} />}
              />
            </Dropdown>
          </span>
        ),
      },
    );

    return cols;
  }, [t, statusLabel, isCrossTenant, navigate]);

  return (
    <div>
      <PageHeader
        icon={<Bot size={18} strokeWidth={1.5} />}
        title={t("agents_page.page_title")}
        actions={
          <>
            {isCrossTenant && (
              <Tag
                icon={<Globe2 size={12} strokeWidth={1.5} />}
                color="purple"
                data-testid="cross-tenant-banner"
              >
                {t("agents_page.cross_tenant_banner")}
              </Tag>
            )}
            <Input.Search
              allowClear
              placeholder={t("agents_page.search_placeholder")}
              aria-label={t("agents_page.search_placeholder")}
              data-testid="agents-search"
              onSearch={(value) => setNameFilter(value)}
              style={{ width: 200 }}
            />
            <Select<string>
              value={statusFilter ?? "all"}
              onChange={(v) => setStatusFilter(v === "all" ? undefined : v)}
              style={{ width: 140 }}
              aria-label={t("agents_page.filter_status")}
              data-testid="agents-status-filter"
              options={[
                { value: "all", label: t("agents_page.filter_status_all") },
                ...STATUS_OPTIONS.map((s) => ({ value: s, label: statusLabel(s) })),
              ]}
            />
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
              onClick={() => navigate("/agent-template-marketplace")}
              data-testid="agents-from-template"
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
                cursor: "pointer",
              }}
            >
              <Store size={14} strokeWidth={1.5} />
              {t("agents_page.from_template")}
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
          </>
        }
      />

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
        onRow={(record) => ({
          onClick: () => navigate(agentPath(record, "overview")),
          style: { cursor: "pointer" },
        })}
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

      <CreateAgentModal
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
