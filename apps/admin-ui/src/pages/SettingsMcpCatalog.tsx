/**
 * Settings — MCP Catalog page (Stream W, system_admin only).
 *
 * Platform-level (NOT tenant-scoped) management of the curated MCP connector
 * catalog. system_admin only (mirrors the backend ``is_system_admin`` gate);
 * non-admins see a notice. Lists connector *types* with name / display_name /
 * category / transport / required-tier badge / enabled toggle, plus a "New
 * connector" action and a create/edit drawer. Delete is guarded server-side
 * (409 ``CATALOG_IN_USE`` when instantiated by tenants).
 *
 * Mirrors ``SettingsPlatformConfig`` gating + layout (PageHeader + admin gate
 * + antd Table + ``ApiError`` → ``${code}: ${message}`` toasts).
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  App,
  Button,
  Popconfirm,
  Switch,
  Table,
  Tag,
  Typography,
} from "antd";
import type { TableColumnsType } from "antd";
import { Boxes, RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";

import { PageHeader } from "../components/PageHeader";
import {
  deletePlatformCatalogEntry,
  listPlatformCatalog,
  updatePlatformCatalogEntry,
  type McpCatalogEntry,
  type McpRequiredTier,
} from "../api/mcp-catalog";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { CatalogEntryDrawer } from "../components/mcp_catalog/CatalogEntryDrawer";

const { Text } = Typography;

const TIER_COLOR: Record<McpRequiredTier, string> = {
  free: "default",
  pro: "blue",
  enterprise: "gold",
};

export function SettingsMcpCatalog() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const auth = useAuth();
  const isSystemAdmin = auth.identity?.isSystemAdmin ?? false;

  const [rows, setRows] = useState<McpCatalogEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editing, setEditing] = useState<McpCatalogEntry | null>(null);

  const errText = useCallback(
    (err: unknown): string =>
      err instanceof ApiError
        ? `${err.code}: ${err.message}`
        : err instanceof Error
          ? err.message
          : "unknown error",
    [],
  );

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setRows(await listPlatformCatalog());
    } catch (err) {
      setError(err instanceof Error ? err.message : "unknown error");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (isSystemAdmin) {
      void refresh();
    }
  }, [isSystemAdmin, refresh]);

  const openCreate = useCallback(() => {
    setEditing(null);
    setDrawerOpen(true);
  }, []);

  const openEdit = useCallback((row: McpCatalogEntry) => {
    setEditing(row);
    setDrawerOpen(true);
  }, []);

  const closeDrawer = useCallback(() => {
    setDrawerOpen(false);
    setEditing(null);
  }, []);

  const onToggle = useCallback(
    async (row: McpCatalogEntry, enabled: boolean) => {
      try {
        await updatePlatformCatalogEntry(row.id, { enabled });
        void refresh();
      } catch (err) {
        message.error(errText(err));
      }
    },
    [errText, message, refresh],
  );

  const onDelete = useCallback(
    async (row: McpCatalogEntry) => {
      try {
        await deletePlatformCatalogEntry(row.id);
        message.success(t("mcp_catalog.deleted"));
        void refresh();
      } catch (err) {
        if (err instanceof ApiError && err.code === "CATALOG_IN_USE") {
          message.error(t("mcp_catalog.delete_in_use"));
          return;
        }
        message.error(errText(err));
      }
    },
    [errText, message, refresh, t],
  );

  const columns: TableColumnsType<McpCatalogEntry> = useMemo(
    () => [
      {
        title: t("mcp_catalog.col_name"),
        key: "name",
        render: (_v, row) => (
          <div>
            <Text strong>{row.display_name}</Text>
            <div>
              <Text type="secondary" style={{ fontSize: 12 }}>
                {row.name}
              </Text>
            </div>
          </div>
        ),
      },
      {
        title: t("mcp_catalog.col_category"),
        dataIndex: "category",
        key: "category",
        width: 140,
        render: (category: string) =>
          category ? <Tag>{category}</Tag> : <Text type="secondary">—</Text>,
      },
      {
        title: t("mcp_catalog.col_transport"),
        dataIndex: "transport",
        key: "transport",
        width: 150,
        render: (transport: string) => (
          <Tag>{transport === "streamable_http" ? "Streamable HTTP" : "SSE"}</Tag>
        ),
      },
      {
        title: t("mcp_catalog.col_tier"),
        dataIndex: "required_tier",
        key: "required_tier",
        width: 120,
        render: (tier: McpRequiredTier) => (
          <Tag color={TIER_COLOR[tier]}>{t(`mcp_catalog.tier_${tier}`)}</Tag>
        ),
      },
      {
        title: t("mcp_catalog.col_enabled"),
        dataIndex: "enabled",
        key: "enabled",
        width: 100,
        render: (enabled: boolean, row) => (
          <Switch
            size="small"
            checked={enabled}
            onChange={(checked) => onToggle(row, checked)}
            aria-label={`${row.name} ${t("mcp_catalog.col_enabled")}`}
            data-testid={`cat-toggle-${row.name}`}
          />
        ),
      },
      {
        title: t("mcp_catalog.col_actions"),
        key: "actions",
        width: 170,
        render: (_v, row) => (
          <div style={{ display: "flex", gap: 6 }}>
            <Button size="small" onClick={() => openEdit(row)} data-testid={`cat-edit-${row.name}`}>
              {t("common.edit")}
            </Button>
            <Popconfirm
              title={t("mcp_catalog.delete_confirm", { name: row.display_name })}
              okType="danger"
              okText={t("common.delete")}
              cancelText={t("common.cancel")}
              onConfirm={() => onDelete(row)}
            >
              <Button size="small" danger data-testid={`cat-delete-${row.name}`}>
                {t("common.delete")}
              </Button>
            </Popconfirm>
          </div>
        ),
      },
    ],
    [t, onToggle, onDelete, openEdit],
  );

  const emptyText = (
    <div style={{ textAlign: "center", padding: "32px 0" }} data-testid="cat-empty">
      <Boxes size={32} strokeWidth={1.25} style={{ opacity: 0.35, marginBottom: 8 }} />
      <div style={{ fontWeight: 600, marginBottom: 4 }}>{t("mcp_catalog.empty_title")}</div>
      <div
        style={{
          color: "var(--hx-text-tertiary, #666)",
          maxWidth: 360,
          margin: "0 auto 16px",
        }}
      >
        {t("mcp_catalog.empty_hint")}
      </div>
      <Button type="primary" onClick={openCreate}>
        {t("mcp_catalog.add")}
      </Button>
    </div>
  );

  return (
    <div data-testid="cat-root">
      <PageHeader
        icon={<Boxes size={18} strokeWidth={1.5} />}
        title={t("mcp_catalog.page_title")}
        subtitle={t("mcp_catalog.subtitle")}
        actions={
          isSystemAdmin && (
            <div style={{ display: "flex", gap: 8 }}>
              <Button
                onClick={() => void refresh()}
                loading={loading}
                icon={<RefreshCw size={14} strokeWidth={1.5} />}
              >
                {t("common.refresh")}
              </Button>
              <Button type="primary" onClick={openCreate} data-testid="cat-add">
                {t("mcp_catalog.add")}
              </Button>
            </div>
          )
        }
      />

      {!isSystemAdmin ? (
        <Alert
          type="warning"
          showIcon
          message={t("mcp_catalog.not_admin_title")}
          description={t("mcp_catalog.not_admin_body")}
          data-testid="cat-not-admin"
        />
      ) : (
        <>
          {error !== null && (
            <Alert
              type="error"
              showIcon
              message={t("mcp_catalog.failed_to_load")}
              description={error}
              style={{ marginBottom: 12 }}
              data-testid="cat-error"
            />
          )}
          <Table<McpCatalogEntry>
            columns={columns}
            dataSource={rows}
            rowKey={(r) => r.id}
            loading={loading}
            pagination={false}
            locale={{ emptyText }}
            data-testid="cat-table"
          />
        </>
      )}

      <CatalogEntryDrawer
        open={drawerOpen}
        onClose={closeDrawer}
        onSaved={() => {
          closeDrawer();
          void refresh();
        }}
        editing={editing}
      />
    </div>
  );
}
