/**
 * Settings — Tenant Credentials page (Stream O Mini-ADR O-13, trimmed by
 * Stream Y-1).
 *
 * Stream Y-1 made LLM credentials platform-exclusive: the former tenant
 * BYOK mode-switch + dry-run preview + per-tenant secret_ref editing were
 * removed. What remains is a read-only view over
 * ``/v1/tenants/{tid}/config/credentials`` showing, per platform
 * provider/tool: whether the platform has it configured and how many of the
 * tenant's agents reference it.
 *
 * Tenant-scoped like the other Settings pages: cross-tenant view
 * (``scope === "*"``) is read-only and shows a banner.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Alert, Button, Card, Empty, Space, Table, Tag, Typography } from "antd";
import type { TableColumnsType } from "antd";
import { KeyRound, RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";

import { ApiError } from "../api/client";
import { PageHeader } from "../components/PageHeader";
import {
  getCredentialsView,
  type CredentialRow,
  type CredentialsView,
} from "../api/tenant_config";
import { useAuth } from "../auth/AuthContext";
import { useTenantScope } from "../tenant/TenantScopeContext";

const { Text } = Typography;

export function SettingsTenantCredentials() {
  const { t } = useTranslation();
  const { scope, apiTenantScope } = useTenantScope();
  const auth = useAuth();
  const homeTenantId = auth.identity?.homeTenantId ?? null;

  const effectiveTenantId =
    scope === "*"
      ? null
      : typeof apiTenantScope === "string" && apiTenantScope !== "*"
        ? apiTenantScope
        : homeTenantId;

  const [view, setView] = useState<CredentialsView | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (effectiveTenantId === null) {
      setView(null);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      setView(await getCredentialsView(effectiveTenantId));
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? `${err.code}: ${err.message}`
          : err instanceof Error
            ? err.message
            : "unknown error";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [effectiveTenantId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const makeColumns = useCallback(
    (kind: "provider" | "tool"): TableColumnsType<CredentialRow> => [
      {
        title: t(`settings_credentials.col_${kind}`),
        key: "name",
        width: 200,
        render: (_, row) => <Tag color="cyan">{row.provider ?? row.tool}</Tag>,
      },
      {
        title: t("settings_credentials.col_platform_status"),
        dataIndex: "platform_configured",
        key: "platform_configured",
        width: 180,
        render: (configured: boolean) =>
          configured ? (
            <Tag color="green">{t("settings_credentials.status_configured")}</Tag>
          ) : (
            <Tag color="default">{t("settings_credentials.status_not_set")}</Tag>
          ),
      },
      {
        title: t("settings_credentials.col_used_by"),
        dataIndex: "used_by_agents",
        key: "used_by_agents",
        width: 120,
        render: (n: number) => <Text>{n}</Text>,
      },
    ],
    [t],
  );

  const providerColumns = useMemo(() => makeColumns("provider"), [makeColumns]);
  const toolColumns = useMemo(() => makeColumns("tool"), [makeColumns]);

  return (
    <div data-testid="credentials-root">
      <PageHeader
        icon={<KeyRound size={18} strokeWidth={1.5} />}
        title={t("settings_credentials.page_title")}
        subtitle={t("settings_credentials.subtitle")}
        actions={
          <>
            {effectiveTenantId !== null && (
              <Tag color="default" data-testid="credentials-tenant-tag">
                tenant:{" "}
                <Text code style={{ fontSize: 11 }}>
                  {effectiveTenantId.slice(0, 8)}…
                </Text>
              </Tag>
            )}
            <Button
              onClick={refresh}
              loading={loading}
              icon={<RefreshCw size={14} strokeWidth={1.5} />}
              disabled={effectiveTenantId === null}
            >
              {t("common.refresh")}
            </Button>
          </>
        }
      />

      {effectiveTenantId === null && (
        <Alert
          type="info"
          showIcon
          message={t("settings_ops.cross_tenant_blocked_title")}
          description={t("settings_ops.cross_tenant_blocked_body")}
          data-testid="credentials-cross-tenant-block"
        />
      )}

      {error !== null && (
        <Alert
          type="error"
          showIcon
          message={t("settings_credentials.failed_to_load")}
          description={error}
          style={{ marginBottom: 12 }}
          data-testid="credentials-error"
        />
      )}

      {effectiveTenantId !== null && view !== null && (
        <Space direction="vertical" size={16} style={{ width: "100%" }}>
          <Card size="small" data-testid="credentials-mode-card">
            <Space align="center" wrap>
              <Text strong>{t("settings_credentials.mode_label")}</Text>
              <Tag color="cyan" data-testid="credentials-mode-current">
                {t("settings_credentials.mode_platform")}
              </Tag>
            </Space>
            <p
              style={{
                color: "var(--ew-text-secondary)",
                fontSize: 12,
                margin: "8px 0 0",
              }}
            >
              {t("settings_credentials.mode_help_platform")}
            </p>
          </Card>

          <div>
            <Text strong style={{ display: "block", marginBottom: 8 }}>
              {t("settings_credentials.providers_heading")}
            </Text>
            <Table<CredentialRow>
              columns={providerColumns}
              dataSource={view.providers}
              rowKey={(r) => r.provider ?? ""}
              pagination={false}
              locale={{ emptyText: <Empty description={t("settings_credentials.empty")} /> }}
              data-testid="provider-creds-table"
            />
          </div>

          <div>
            <Text strong style={{ display: "block", marginBottom: 8 }}>
              {t("settings_credentials.tools_heading")}
            </Text>
            <Table<CredentialRow>
              columns={toolColumns}
              dataSource={view.tools}
              rowKey={(r) => r.tool ?? ""}
              pagination={false}
              locale={{ emptyText: <Empty description={t("settings_credentials.empty")} /> }}
              data-testid="tool-creds-table"
            />
          </div>
        </Space>
      )}
    </div>
  );
}
