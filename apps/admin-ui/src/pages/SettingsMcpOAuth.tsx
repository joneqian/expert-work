/**
 * Settings — My MCP connections (Stream MCP-OAUTH).
 *
 * Lists the **current user's** per-user OAuth connections to ``oauth2`` catalog
 * connectors (``GET /v1/mcp-oauth/connections``). Each row shows the connector
 * (display name joined from the tenant catalog by ``catalog_id``), connection
 * status, granted scopes, token expiry, and per-row actions (re-authorize /
 * disconnect).
 *
 * Per-user (not tenant-admin): every member manages their OWN connections — the
 * backend scopes by ``subject_id`` and the ``mcp_oauth`` RBAC resource grants
 * operators read/write/delete on their own.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  App,
  Button,
  Popconfirm,
  Space,
  Spin,
  Table,
  Tag,
  Tooltip,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { KeyRound } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  disconnectOAuth,
  initiateMcpOAuth,
  listOAuthConnections,
  type McpOAuthConnection,
  type McpOAuthStatus,
} from "../api/mcp-oauth";
import { listTenantCatalog, type TenantCatalogEntry } from "../api/mcp-catalog";
import { ApiError } from "../api/client";
import { PageHeader } from "../components/PageHeader";

const STATUS_COLOR: Record<McpOAuthStatus, string> = {
  pending: "gold",
  connected: "green",
  expired: "orange",
  revoked: "default",
  error: "red",
};

export function SettingsMcpOAuth() {
  const { t } = useTranslation();
  const { message } = App.useApp();

  const [rows, setRows] = useState<McpOAuthConnection[]>([]);
  const [catalog, setCatalog] = useState<TenantCatalogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [conns, cat] = await Promise.all([
        listOAuthConnections(),
        listTenantCatalog(),
      ]);
      setRows(conns);
      setCatalog(cat);
    } catch (err) {
      setError(err instanceof Error ? err.message : "unknown error");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const catalogById = useMemo(() => {
    const map = new Map<string, TenantCatalogEntry>();
    for (const e of catalog) map.set(e.id, e);
    return map;
  }, [catalog]);

  const handleReauthorize = useCallback(
    async (conn: McpOAuthConnection) => {
      setBusy(conn.id);
      try {
        const result = await initiateMcpOAuth(conn.catalog_id);
        window.location.assign(result.authorize_url);
      } catch (err) {
        setBusy(null);
        const msg =
          err instanceof ApiError
            ? `${err.code}: ${err.message}`
            : t("mcp_oauth.reauthorize_failed");
        message.error(msg);
      }
    },
    [message, t],
  );

  const handleDisconnect = useCallback(
    async (conn: McpOAuthConnection) => {
      setBusy(conn.id);
      try {
        await disconnectOAuth(conn.id);
        message.success(t("mcp_oauth.disconnected"));
        await reload();
      } catch (err) {
        const msg =
          err instanceof ApiError
            ? `${err.code}: ${err.message}`
            : t("mcp_oauth.disconnect_failed");
        message.error(msg);
      } finally {
        setBusy(null);
      }
    },
    [message, reload, t],
  );

  const columns: ColumnsType<McpOAuthConnection> = [
    {
      title: t("mcp_oauth.col_connector"),
      dataIndex: "name",
      key: "connector",
      render: (_name: string, row) => {
        const entry = catalogById.get(row.catalog_id);
        return (
          <span data-testid={`mo-name-${row.name}`}>
            {entry?.display_name ?? row.name}
          </span>
        );
      },
    },
    {
      title: t("mcp_oauth.col_status"),
      dataIndex: "status",
      key: "status",
      render: (status: McpOAuthStatus, row) => (
        <Space size={4}>
          <Tag
            color={STATUS_COLOR[status]}
            data-testid={`mo-status-${row.name}`}
          >
            {t(`mcp_oauth.status_${status}`)}
          </Tag>
          {row.last_error && (
            <Tooltip title={row.last_error}>
              <Tag color="red">{t("mcp_oauth.has_error")}</Tag>
            </Tooltip>
          )}
        </Space>
      ),
    },
    {
      title: t("mcp_oauth.col_scopes"),
      dataIndex: "scopes",
      key: "scopes",
      render: (scopes: string) =>
        scopes || <span style={{ color: "#999" }}>—</span>,
    },
    {
      title: t("mcp_oauth.col_expires"),
      dataIndex: "token_expires_at",
      key: "expires",
      render: (ts: string | null) =>
        ts ? (
          new Date(ts).toLocaleString()
        ) : (
          <span style={{ color: "#999" }}>—</span>
        ),
    },
    {
      title: t("mcp_oauth.col_actions"),
      key: "actions",
      render: (_v, row) => (
        <Space>
          <Button
            size="small"
            loading={busy === row.id}
            onClick={() => handleReauthorize(row)}
            data-testid={`mo-reauth-${row.name}`}
          >
            {t("mcp_oauth.reauthorize")}
          </Button>
          <Popconfirm
            title={t("mcp_oauth.disconnect_confirm")}
            onConfirm={() => handleDisconnect(row)}
            okText={t("common.confirm")}
            cancelText={t("common.cancel")}
          >
            <Button
              size="small"
              danger
              data-testid={`mo-disconnect-${row.name}`}
            >
              {t("mcp_oauth.disconnect")}
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <PageHeader
        title={t("mcp_oauth.page_title")}
        icon={<KeyRound size={20} />}
        subtitle={t("mcp_oauth.page_subtitle")}
      />
      {error !== null ? (
        <Alert
          type="error"
          showIcon
          message={t("mcp_oauth.failed_to_load")}
          description={error}
        />
      ) : loading ? (
        <div style={{ textAlign: "center", padding: "48px 0" }}>
          <Spin />
        </div>
      ) : (
        <Table<McpOAuthConnection>
          data-testid="mo-table"
          rowKey="id"
          columns={columns}
          dataSource={rows}
          pagination={false}
          locale={{ emptyText: t("mcp_oauth.empty") }}
        />
      )}
    </div>
  );
}
