/**
 * Settings — Tenants page (Stream U, PR D).
 *
 * Lists every tenant on the platform (``GET /v1/tenants``). Platform-level
 * read — only system_admins see the table (mirrors the backend gate). Each
 * row's "Manage" action switches the current tenant scope into that tenant
 * (persisted by :func:`TenantScopeProvider`) and jumps to its per-tenant
 * config page, where config / quotas / credentials are edited.
 */
import { useCallback, useEffect, useState } from "react";
import { Alert, App, Breadcrumb, Button, Popconfirm, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { Building, ChevronRight } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";

import {
  activateTenant,
  deactivateTenant,
  listTenants,
  type TenantSummary,
} from "../api/tenants";
import { useAuth } from "../auth/AuthContext";
import { useTenantScope } from "../tenant/TenantScopeContext";
import { CreateTenantDrawer } from "../components/CreateTenantDrawer";

export function SettingsTenants() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const auth = useAuth();
  const isSystemAdmin = auth.identity?.isSystemAdmin ?? false;
  const { setScope } = useTenantScope();
  const navigate = useNavigate();

  const [rows, setRows] = useState<TenantSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [createOpen, setCreateOpen] = useState(false);

  const reload = useCallback(() => {
    setLoading(true);
    listTenants().then(
      (data) => {
        setRows(data);
        setLoading(false);
      },
      (err: unknown) => {
        setError(err instanceof Error ? err.message : "unknown error");
        setLoading(false);
      },
    );
  }, []);

  useEffect(() => {
    if (!isSystemAdmin) {
      setLoading(false);
      return;
    }
    reload();
  }, [isSystemAdmin, reload]);

  const changeStatus = useCallback(
    async (id: string, kind: "deactivate" | "activate") => {
      try {
        if (kind === "deactivate") {
          await deactivateTenant(id);
        } else {
          await activateTenant(id);
        }
        message.success(t("settings_tenants.status_changed"));
        reload();
      } catch {
        message.error(t("settings_tenants.status_change_failed"));
      }
    },
    [message, t, reload],
  );

  const manage = useCallback(
    (id: string) => {
      setScope(id);
      navigate("/settings/tenant-config");
    },
    [setScope, navigate],
  );

  const columns: ColumnsType<TenantSummary> = [
    { title: t("settings_tenants.col_display_name"), dataIndex: "display_name", key: "display_name" },
    { title: t("settings_tenants.col_plan"), dataIndex: "plan", key: "plan" },
    {
      title: t("settings_tenants.col_tenant_id"),
      dataIndex: "tenant_id",
      key: "tenant_id",
      render: (id: string) => (
        <Typography.Text code copyable>
          {id}
        </Typography.Text>
      ),
    },
    {
      title: t("settings_tenants.col_created"),
      dataIndex: "created_at",
      key: "created_at",
      render: (v: string) => new Date(v).toLocaleString(),
    },
    {
      title: t("settings_tenants.col_status"),
      key: "status",
      render: (_: unknown, r: TenantSummary) => (
        <Tag
          color={r.status === "suspended" ? "red" : "green"}
          data-testid={`st-status-${r.tenant_id}`}
        >
          {r.status === "suspended"
            ? t("settings_tenants.st_suspended")
            : t("settings_tenants.st_active")}
        </Tag>
      ),
    },
    {
      title: t("settings_tenants.col_actions"),
      key: "actions",
      render: (_: unknown, r: TenantSummary) => (
        <span style={{ display: "inline-flex", gap: 8 }}>
          <Button
            size="small"
            data-testid={`st-manage-${r.tenant_id}`}
            onClick={() => manage(r.tenant_id)}
          >
            {t("settings_tenants.manage")}
          </Button>
          {r.status === "active" ? (
            <Popconfirm
              title={t("settings_tenants.deactivate_confirm")}
              onConfirm={() => changeStatus(r.tenant_id, "deactivate")}
            >
              <Button size="small" danger data-testid={`st-deactivate-${r.tenant_id}`}>
                {t("settings_tenants.deactivate")}
              </Button>
            </Popconfirm>
          ) : (
            <Button
              size="small"
              data-testid={`st-activate-${r.tenant_id}`}
              onClick={() => changeStatus(r.tenant_id, "activate")}
            >
              {t("settings_tenants.activate")}
            </Button>
          )}
        </span>
      ),
    },
  ];

  return (
    <div data-testid="st-root">
      <div className="hx-page-header">
        <Breadcrumb
          separator={<ChevronRight size={12} strokeWidth={1.5} />}
          items={[{ title: t("common.home") }, { title: t("settings_tenants.page_title") }]}
        />
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 8, marginBottom: 16 }}>
          <Building size={20} strokeWidth={1.5} />
          <h1 style={{ margin: 0 }}>{t("settings_tenants.page_title")}</h1>
          {isSystemAdmin && (
            <Button
              type="primary"
              data-testid="tenants-create"
              style={{ marginLeft: "auto" }}
              onClick={() => setCreateOpen(true)}
            >
              {t("settings_tenants.create")}
            </Button>
          )}
        </div>
        <p style={{ color: "var(--hx-text-secondary)", fontSize: 13, margin: "0 0 12px" }}>
          {t("settings_tenants.subtitle")}
        </p>
      </div>

      {!isSystemAdmin ? (
        <Alert
          type="warning"
          showIcon
          message={t("settings_tenants.not_admin_title")}
          description={t("settings_tenants.not_admin_body")}
          data-testid="st-not-admin"
        />
      ) : (
        <>
          {error !== null && (
            <Alert
              type="error"
              showIcon
              data-testid="st-error"
              message={t("settings_tenants.failed_to_load")}
              description={error}
              style={{ marginBottom: 16 }}
            />
          )}
          <Table<TenantSummary>
            data-testid="st-table"
            rowKey="tenant_id"
            loading={loading}
            dataSource={rows}
            pagination={false}
            locale={{ emptyText: t("settings_tenants.empty") }}
            columns={columns}
          />
          <CreateTenantDrawer
            open={createOpen}
            onClose={() => setCreateOpen(false)}
            onCreated={() => {
              setCreateOpen(false);
              reload();
            }}
          />
        </>
      )}
    </div>
  );
}
