/**
 * Settings — Tenant Credentials page (Stream O PR 2b, Mini-ADR O-7 / O-13).
 *
 * Three surfaces over ``/v1/tenants/{tid}/config/credentials`` (+ the
 * dry-run preview and ``PUT /config`` for writes):
 *   1. credentials-mode switcher (platform ↔ tenant) gated by a dry-run
 *      preview that lists any provider/tool still missing a credential;
 *   2. provider-credential table (provider × platform-configured × tenant
 *      secret_ref × used-by-agents);
 *   3. tool-credential table (same shape).
 *
 * Tenant-scoped like the other Settings pages: cross-tenant view
 * (``scope === "*"``) is read-only and shows a banner.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  App,
  Breadcrumb,
  Button,
  Card,
  Empty,
  Form,
  Input,
  Modal,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import type { TableColumnsType } from "antd";
import { ChevronRight, KeyRound, Pencil, RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";

import { ApiError } from "../api/client";
import {
  dryRunCredentialsMode,
  getCredentialsView,
  upsertTenantConfig,
  type CredentialRow,
  type CredentialsMode,
  type CredentialsView,
  type DryRunResult,
} from "../api/tenant_config";
import { useAuth } from "../auth/AuthContext";
import { useTenantScope } from "../tenant/TenantScopeContext";

const { Text } = Typography;

/** Assemble the tenant's current credential maps from the view rows so a
 *  single-cell edit can PUT the full (merged) map (the backend patch field
 *  replaces, not merges). */
function tenantCredsFromView(view: CredentialsView): {
  providers: Record<string, string>;
  tools: Record<string, string>;
} {
  const providers: Record<string, string> = {};
  for (const row of view.providers) {
    if (row.provider && row.tenant_secret_ref) providers[row.provider] = row.tenant_secret_ref;
  }
  const tools: Record<string, string> = {};
  for (const row of view.tools) {
    if (row.tool && row.tenant_secret_ref) tools[row.tool] = row.tenant_secret_ref;
  }
  return { providers, tools };
}

interface EditTarget {
  kind: "provider" | "tool";
  key: string;
  currentRef: string | null;
}

export function SettingsTenantCredentials() {
  const { t } = useTranslation();
  const { message } = App.useApp();
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

  // Mode-switch modal.
  const [switchOpen, setSwitchOpen] = useState(false);
  const [switchTarget, setSwitchTarget] = useState<CredentialsMode>("tenant");
  const [dryRun, setDryRun] = useState<DryRunResult | null>(null);
  const [dryRunLoading, setDryRunLoading] = useState(false);
  const [switching, setSwitching] = useState(false);

  // Credential edit modal.
  const [editing, setEditing] = useState<EditTarget | null>(null);
  const [editForm] = Form.useForm<{ secret_ref: string }>();
  const [editSubmitting, setEditSubmitting] = useState(false);

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

  const openSwitch = useCallback(
    async (target: CredentialsMode) => {
      if (effectiveTenantId === null || view === null) return;
      setSwitchTarget(target);
      setDryRun(null);
      setSwitchOpen(true);
      // Platform mode is always satisfiable (platform creds exist); only the
      // tenant switch needs a coverage preview.
      if (target === "tenant") {
        setDryRunLoading(true);
        try {
          const creds = tenantCredsFromView(view);
          setDryRun(
            await dryRunCredentialsMode(effectiveTenantId, {
              model_credentials_ref: creds.providers,
              tool_credentials: creds.tools,
            }),
          );
        } catch (err) {
          message.error(err instanceof Error ? err.message : "dry-run failed");
          setSwitchOpen(false);
        } finally {
          setDryRunLoading(false);
        }
      }
    },
    [effectiveTenantId, view, message],
  );

  const confirmSwitch = useCallback(async () => {
    if (effectiveTenantId === null) return;
    setSwitching(true);
    try {
      await upsertTenantConfig(effectiveTenantId, { credentials_mode: switchTarget });
      message.success(t("settings_credentials.mode_switched"));
      setSwitchOpen(false);
      refresh();
    } catch (err) {
      message.error(err instanceof Error ? err.message : "switch failed");
    } finally {
      setSwitching(false);
    }
  }, [effectiveTenantId, switchTarget, message, refresh, t]);

  const openEdit = useCallback(
    (target: EditTarget) => {
      setEditing(target);
      editForm.setFieldsValue({ secret_ref: target.currentRef ?? "" });
    },
    [editForm],
  );

  const saveEdit = useCallback(async () => {
    if (effectiveTenantId === null || view === null || editing === null) return;
    const { secret_ref } = await editForm.validateFields();
    const trimmed = secret_ref.trim();
    const creds = tenantCredsFromView(view);
    const map = editing.kind === "provider" ? creds.providers : creds.tools;
    if (trimmed === "") {
      delete map[editing.key];
    } else {
      map[editing.key] = trimmed;
    }
    setEditSubmitting(true);
    try {
      await upsertTenantConfig(
        effectiveTenantId,
        editing.kind === "provider"
          ? { model_credentials_ref: creds.providers }
          : { tool_credentials: creds.tools },
      );
      message.success(t("settings_credentials.cred_saved"));
      setEditing(null);
      refresh();
    } catch (err) {
      message.error(err instanceof Error ? err.message : "save failed");
    } finally {
      setEditSubmitting(false);
    }
  }, [effectiveTenantId, view, editing, editForm, message, refresh, t]);

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
        title: t("settings_credentials.col_tenant_ref"),
        dataIndex: "tenant_secret_ref",
        key: "tenant_secret_ref",
        render: (ref: string | null) =>
          ref === null ? (
            <Text type="secondary" style={{ fontSize: 12 }}>
              {t("settings_credentials.not_set")}
            </Text>
          ) : (
            <Text code style={{ fontSize: 11 }}>
              {ref}
            </Text>
          ),
      },
      {
        title: t("settings_credentials.col_used_by"),
        dataIndex: "used_by_agents",
        key: "used_by_agents",
        width: 120,
        render: (n: number) => <Text>{n}</Text>,
      },
      {
        title: t("settings_credentials.col_actions"),
        key: "actions",
        width: 110,
        render: (_, row) => {
          const key = row.provider ?? row.tool ?? "";
          return (
            <Button
              size="small"
              icon={<Pencil size={12} strokeWidth={1.75} />}
              onClick={() => openEdit({ kind, key, currentRef: row.tenant_secret_ref })}
              disabled={effectiveTenantId === null}
              data-testid={`cred-edit-${kind}-${key}`}
            >
              {t("common.edit")}
            </Button>
          );
        },
      },
    ],
    [t, openEdit, effectiveTenantId],
  );

  const providerColumns = useMemo(() => makeColumns("provider"), [makeColumns]);
  const toolColumns = useMemo(() => makeColumns("tool"), [makeColumns]);

  const mode = view?.mode ?? "platform";
  const otherMode: CredentialsMode = mode === "platform" ? "tenant" : "platform";

  return (
    <div data-testid="credentials-root">
      <div className="hx-page-header">
        <Breadcrumb
          separator={<ChevronRight size={12} strokeWidth={1.5} />}
          items={[
            { title: t("common.home") },
            { title: t("settings_credentials.page_title") },
          ]}
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
          <KeyRound size={20} strokeWidth={1.5} />
          <h1 style={{ margin: 0 }}>{t("settings_credentials.page_title")}</h1>
          {effectiveTenantId !== null && (
            <Tag color="default" data-testid="credentials-tenant-tag">
              tenant:{" "}
              <Text code style={{ fontSize: 11 }}>
                {effectiveTenantId.slice(0, 8)}…
              </Text>
            </Tag>
          )}
          <span style={{ flex: 1 }} />
          <Button
            onClick={refresh}
            loading={loading}
            icon={<RefreshCw size={14} strokeWidth={1.5} />}
            disabled={effectiveTenantId === null}
          >
            {t("common.refresh")}
          </Button>
        </div>
        <p style={{ color: "var(--hx-text-secondary)", fontSize: 13, margin: "0 0 12px" }}>
          {t("settings_credentials.subtitle")}
        </p>
      </div>

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
              <Tag
                color={mode === "tenant" ? "purple" : "cyan"}
                data-testid="credentials-mode-current"
              >
                {t(`settings_credentials.mode_${mode}`)}
              </Tag>
              <span style={{ flex: 1 }} />
              <Button
                onClick={() => openSwitch(otherMode)}
                data-testid="credentials-mode-switch-btn"
              >
                {t("settings_credentials.switch_to", {
                  mode: t(`settings_credentials.mode_${otherMode}`),
                })}
              </Button>
            </Space>
            <p
              style={{
                color: "var(--hx-text-secondary)",
                fontSize: 12,
                margin: "8px 0 0",
              }}
            >
              {t(`settings_credentials.mode_help_${mode}`)}
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

      <Modal
        title={t("settings_credentials.switch_modal_title", {
          mode: t(`settings_credentials.mode_${switchTarget}`),
        })}
        open={switchOpen}
        onCancel={() => setSwitchOpen(false)}
        onOk={confirmSwitch}
        confirmLoading={switching}
        okButtonProps={{
          disabled: switchTarget === "tenant" && (dryRunLoading || dryRun?.ok !== true),
          "data-testid": "credentials-switch-confirm",
        }}
        data-testid="credentials-switch-modal"
      >
        {switchTarget === "platform" && (
          <Alert
            type="info"
            showIcon
            message={t("settings_credentials.switch_to_platform_note")}
          />
        )}
        {switchTarget === "tenant" && dryRunLoading && (
          <Text type="secondary">{t("settings_credentials.dry_run_running")}</Text>
        )}
        {switchTarget === "tenant" && dryRun !== null && dryRun.ok && (
          <Alert
            type="success"
            showIcon
            message={t("settings_credentials.dry_run_ok")}
            data-testid="credentials-dry-run-ok"
          />
        )}
        {switchTarget === "tenant" && dryRun !== null && !dryRun.ok && (
          <Alert
            type="error"
            showIcon
            message={t("settings_credentials.dry_run_incomplete")}
            description={
              <div data-testid="credentials-dry-run-missing">
                {dryRun.missing_providers.length > 0 && (
                  <div>
                    {t("settings_credentials.missing_providers")}:{" "}
                    {dryRun.missing_providers.join(", ")}
                  </div>
                )}
                {dryRun.missing_tools.length > 0 && (
                  <div>
                    {t("settings_credentials.missing_tools")}:{" "}
                    {dryRun.missing_tools.join(", ")}
                  </div>
                )}
              </div>
            }
          />
        )}
      </Modal>

      <Modal
        title={
          editing === null
            ? ""
            : t("settings_credentials.edit_modal_title", { key: editing.key })
        }
        open={editing !== null}
        onCancel={() => setEditing(null)}
        onOk={saveEdit}
        confirmLoading={editSubmitting}
        okButtonProps={{ "data-testid": "cred-edit-save" }}
        data-testid="cred-edit-modal"
      >
        <Form form={editForm} layout="vertical">
          <Form.Item
            name="secret_ref"
            label={t("settings_credentials.secret_ref_label")}
            extra={t("settings_credentials.secret_ref_hint")}
          >
            <Input placeholder="kms://tenant/provider-key" data-testid="cred-edit-input" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
