/**
 * Settings — Platform Credentials page (Stream P, Mini-ADR P-11/P-12).
 *
 * Platform-level (NOT tenant-scoped) management of provider/tool credential
 * refs — the runtime DB overlay over the env seed. system_admin only (mirrors
 * the backend ``is_system_admin`` gate); non-admins see a notice. Shows the
 * full catalog with source (env/db/unset), effective ref, enabled, and the
 * cross-tenant used-by-agents count. Edits submit a ref (kms:// / secret://),
 * never a plaintext key; "disable" is the soft path and delete is guarded
 * server-side (409 when env-defined or in use).
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  App,
  Button,
  Drawer,
  Form,
  Input,
  Modal,
  Popconfirm,
  Segmented,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import type { TableColumnsType } from "antd";
import { Building2, KeyRound, RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";

import { PageHeader } from "../components/PageHeader";
import {
  deletePlatformProvider,
  deletePlatformTool,
  deleteTenantProviderOverride,
  deleteTenantToolOverride,
  getPlatformCredentials,
  getTenantCredentials,
  upsertPlatformProvider,
  upsertPlatformTool,
  upsertTenantProviderOverride,
  upsertTenantToolOverride,
  type PlatformCredentialsView,
  type PlatformProviderRow,
  type PlatformSecretSource,
  type PlatformSecretUpsertBody,
  type PlatformToolRow,
  type TenantCredentialsView,
  type TenantEffectiveSource,
  type TenantProviderEntry,
  type TenantToolEntry,
} from "../api/platform_config";
import { listTenants, type TenantSummary } from "../api/tenants";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { PlatformBillingSection } from "./settings_platform/PlatformBillingSection";
import { PlatformEmbeddingSection } from "./settings_platform/PlatformEmbeddingSection";
import { PlatformJudgeSection } from "./settings_platform/PlatformJudgeSection";

const { Text } = Typography;

type Kind = "provider" | "tool";

interface EditTarget {
  kind: Kind;
  key: string;
  secret_ref: string;
  /** Set when editing a per-tenant override (Stream HX-8) rather than the platform row. */
  tenantId?: string;
}

function sourceTag(source: PlatformSecretSource, t: (k: string) => string) {
  const color =
    source === "db" ? "cyan" : source === "env" ? "default" : undefined;
  return <Tag color={color}>{t(`settings_platform.source_${source}`)}</Tag>;
}

export function SettingsPlatformConfig() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const auth = useAuth();
  const isSystemAdmin = auth.identity?.isSystemAdmin ?? false;

  const [view, setView] = useState<PlatformCredentialsView | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [edit, setEdit] = useState<EditTarget | null>(null);
  const [editForm] = Form.useForm<{
    secret_ref?: string;
    value?: string;
    enabled: boolean;
  }>();
  const [editMode, setEditMode] = useState<"value" | "ref">("value");
  const [saving, setSaving] = useState(false);

  // Per-tenant override drawer (Stream HX-8).
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [tenants, setTenants] = useState<TenantSummary[]>([]);
  const [tenantId, setTenantId] = useState<string | null>(null);
  const [tenantView, setTenantView] = useState<TenantCredentialsView | null>(
    null,
  );
  const [tenantLoading, setTenantLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setView(await getPlatformCredentials());
    } catch (err) {
      setError(err instanceof Error ? err.message : "unknown error");
    } finally {
      setLoading(false);
    }
  }, []);

  const refreshTenantView = useCallback(async (tid: string) => {
    setTenantLoading(true);
    try {
      setTenantView(await getTenantCredentials(tid));
    } catch {
      setTenantView(null);
    } finally {
      setTenantLoading(false);
    }
  }, []);

  const openDrawer = useCallback(async () => {
    setDrawerOpen(true);
    try {
      setTenants(await listTenants(200));
    } catch {
      setTenants([]);
    }
  }, []);

  const onPickTenant = useCallback(
    (tid: string) => {
      setTenantId(tid);
      refreshTenantView(tid);
    },
    [refreshTenantView],
  );

  useEffect(() => {
    if (isSystemAdmin) {
      refresh();
    }
  }, [isSystemAdmin, refresh]);

  const errText = useCallback(
    (err: unknown): string =>
      err instanceof ApiError
        ? `${err.code}: ${err.message}`
        : err instanceof Error
          ? err.message
          : "failed",
    [],
  );

  const doUpsert = useCallback(
    async (
      kind: Kind,
      key: string,
      body: PlatformSecretUpsertBody,
      tid?: string,
    ) => {
      if (tid !== undefined) {
        if (kind === "provider") {
          await upsertTenantProviderOverride(tid, key, body);
        } else {
          await upsertTenantToolOverride(tid, key, body);
        }
      } else if (kind === "provider") {
        await upsertPlatformProvider(key, body);
      } else {
        await upsertPlatformTool(key, body);
      }
    },
    [],
  );

  const onDeleteOverride = useCallback(
    async (kind: Kind, key: string, tid: string) => {
      try {
        if (kind === "provider") {
          await deleteTenantProviderOverride(tid, key);
        } else {
          await deleteTenantToolOverride(tid, key);
        }
        message.success(t("settings_platform.deleted"));
        refreshTenantView(tid);
        refresh();
      } catch (err) {
        message.error(errText(err));
      }
    },
    [errText, message, refresh, refreshTenantView, t],
  );

  const onToggle = useCallback(
    async (
      kind: Kind,
      key: string,
      secret_ref: string | null,
      enabled: boolean,
    ) => {
      if (secret_ref === null) {
        return;
      }
      try {
        // Flipping enabled re-upserts the existing ref (never a raw value).
        await doUpsert(kind, key, { secret_ref, enabled });
        message.success(t("settings_platform.saved"));
        refresh();
      } catch (err) {
        message.error(errText(err));
      }
    },
    [doUpsert, errText, message, refresh, t],
  );

  const onDelete = useCallback(
    async (kind: Kind, key: string) => {
      try {
        if (kind === "provider") {
          await deletePlatformProvider(key);
        } else {
          await deletePlatformTool(key);
        }
        message.success(t("settings_platform.deleted"));
        refresh();
      } catch (err) {
        message.error(errText(err));
      }
    },
    [errText, message, refresh, t],
  );

  const onSaveEdit = useCallback(async () => {
    if (edit === null) {
      return;
    }
    const values = await editForm.validateFields();
    const body: PlatformSecretUpsertBody =
      editMode === "value"
        ? { value: values.value, enabled: values.enabled }
        : { secret_ref: values.secret_ref, enabled: values.enabled };
    setSaving(true);
    try {
      await doUpsert(edit.kind, edit.key, body, edit.tenantId);
      message.success(t("settings_platform.saved"));
      const editedTenant = edit.tenantId;
      setEdit(null);
      refresh();
      if (editedTenant !== undefined) {
        refreshTenantView(editedTenant);
      }
    } catch (err) {
      message.error(errText(err));
    } finally {
      setSaving(false);
    }
  }, [
    edit,
    editMode,
    editForm,
    doUpsert,
    errText,
    message,
    refresh,
    refreshTenantView,
    t,
  ]);

  const makeColumns = useCallback(
    (kind: Kind): TableColumnsType<PlatformProviderRow | PlatformToolRow> => [
      {
        title: t("settings_platform.col_name"),
        key: "name",
        render: (_v, row) => (
          <Text strong>
            {"provider" in row ? row.provider : (row as PlatformToolRow).tool}
          </Text>
        ),
      },
      {
        title: t("settings_platform.col_source"),
        dataIndex: "source",
        key: "source",
        width: 110,
        render: (s: PlatformSecretSource) => sourceTag(s, t),
      },
      {
        title: t("settings_platform.col_secret_ref"),
        dataIndex: "secret_ref",
        key: "secret_ref",
        render: (ref: string | null) =>
          ref ? (
            <Tooltip title={ref}>
              <Text code style={{ fontSize: 11 }}>
                {ref}
              </Text>
            </Tooltip>
          ) : (
            <Text type="secondary" style={{ fontSize: 12 }}>
              {t("settings_platform.unset_ref")}
            </Text>
          ),
      },
      {
        title: t("settings_platform.col_enabled"),
        dataIndex: "enabled",
        key: "enabled",
        width: 100,
        render: (enabled: boolean, row) => {
          const key =
            "provider" in row ? row.provider : (row as PlatformToolRow).tool;
          return (
            <Switch
              size="small"
              checked={enabled}
              disabled={row.secret_ref === null}
              onChange={(checked) =>
                onToggle(kind, key, row.secret_ref, checked)
              }
              aria-label={`${key} ${t("settings_platform.col_enabled")}`}
              data-testid={`pc-toggle-${key}`}
            />
          );
        },
      },
      {
        title: t("settings_platform.col_used_by"),
        dataIndex: "used_by_agents",
        key: "used_by_agents",
        width: 110,
        render: (n: number) => <Text>{n}</Text>,
      },
      {
        title: t("settings_platform.col_tenant_overrides"),
        dataIndex: "tenant_override_count",
        key: "tenant_override_count",
        width: 130,
        render: (n: number | undefined) =>
          n ? <Tag color="purple">{n}</Tag> : <Text type="secondary">0</Text>,
      },
      {
        title: t("settings_platform.col_actions"),
        key: "actions",
        width: 180,
        render: (_v, row) => {
          const key =
            "provider" in row ? row.provider : (row as PlatformToolRow).tool;
          return (
            <Space size={6}>
              <Button
                size="small"
                onClick={() =>
                  setEdit({ kind, key, secret_ref: row.secret_ref ?? "" })
                }
                data-testid={`pc-edit-${key}`}
              >
                {t("settings_platform.edit_btn")}
              </Button>
              {row.source === "db" && (
                <Popconfirm
                  title={t("settings_platform.delete_confirm")}
                  okType="danger"
                  okText={t("common.delete")}
                  cancelText={t("common.cancel")}
                  onConfirm={() => onDelete(kind, key)}
                >
                  <Button size="small" danger data-testid={`pc-delete-${key}`}>
                    {t("common.delete")}
                  </Button>
                </Popconfirm>
              )}
            </Space>
          );
        },
      },
    ],
    [t, onToggle, onDelete],
  );

  const providerColumns = useMemo(() => makeColumns("provider"), [makeColumns]);
  const toolColumns = useMemo(() => makeColumns("tool"), [makeColumns]);

  const effectiveSourceTag = useCallback(
    (source: TenantEffectiveSource) => {
      const color =
        source === "tenant"
          ? "purple"
          : source === "suppressed"
            ? "red"
            : source === "db"
              ? "cyan"
              : undefined;
      return (
        <Tag color={color}>
          {t(`settings_platform.tenant_source_${source}`)}
        </Tag>
      );
    },
    [t],
  );

  const makeOverrideColumns = useCallback(
    (kind: Kind): TableColumnsType<TenantProviderEntry | TenantToolEntry> => [
      {
        title: t("settings_platform.col_name"),
        key: "name",
        render: (_v, row) => (
          <Text strong>
            {"provider" in row ? row.provider : (row as TenantToolEntry).tool}
          </Text>
        ),
      },
      {
        title: t("settings_platform.col_effective"),
        key: "effective",
        width: 120,
        render: (_v, row) => effectiveSourceTag(row.effective_source),
      },
      {
        title: t("settings_platform.col_secret_ref"),
        key: "ref",
        render: (_v, row) =>
          row.effective_ref ? (
            <Tooltip title={row.effective_ref}>
              <Text code style={{ fontSize: 11 }}>
                {row.effective_ref}
              </Text>
            </Tooltip>
          ) : (
            <Text type="secondary" style={{ fontSize: 12 }}>
              {t("settings_platform.unset_ref")}
            </Text>
          ),
      },
      {
        title: t("settings_platform.col_actions"),
        key: "actions",
        width: 180,
        render: (_v, row) => {
          const key =
            "provider" in row ? row.provider : (row as TenantToolEntry).tool;
          return (
            <Space size={6}>
              <Button
                size="small"
                onClick={() =>
                  setEdit({
                    kind,
                    key,
                    secret_ref: row.override?.secret_ref ?? "",
                    tenantId: tenantId ?? undefined,
                  })
                }
                data-testid={`pc-tenant-edit-${key}`}
              >
                {row.override !== null
                  ? t("settings_platform.edit_btn")
                  : t("settings_platform.override_btn")}
              </Button>
              {row.override !== null && tenantId !== null && (
                <Popconfirm
                  title={t("settings_platform.delete_override_confirm")}
                  okType="danger"
                  okText={t("common.delete")}
                  cancelText={t("common.cancel")}
                  onConfirm={() => onDeleteOverride(kind, key, tenantId)}
                >
                  <Button
                    size="small"
                    danger
                    data-testid={`pc-tenant-delete-${key}`}
                  >
                    {t("common.delete")}
                  </Button>
                </Popconfirm>
              )}
            </Space>
          );
        },
      },
    ],
    [t, effectiveSourceTag, onDeleteOverride, tenantId],
  );

  const overrideProviderColumns = useMemo(
    () => makeOverrideColumns("provider"),
    [makeOverrideColumns],
  );
  const overrideToolColumns = useMemo(
    () => makeOverrideColumns("tool"),
    [makeOverrideColumns],
  );

  useEffect(() => {
    if (edit !== null) {
      // Default to the paste-a-key flow; clear any prior pasted value.
      setEditMode("value");
      editForm.setFieldsValue({
        secret_ref: edit.secret_ref,
        value: "",
        enabled: true,
      });
    }
  }, [edit, editForm]);

  return (
    <div data-testid="pc-root">
      <PageHeader
        icon={<KeyRound size={18} strokeWidth={1.5} />}
        title={t("settings_platform.page_title")}
        subtitle={t("settings_platform.subtitle")}
        actions={
          isSystemAdmin && (
            <Space>
              <Button
                onClick={openDrawer}
                icon={<Building2 size={14} strokeWidth={1.5} />}
                data-testid="pc-tenant-overrides-btn"
              >
                {t("settings_platform.tenant_overrides_btn")}
              </Button>
              <Button
                onClick={refresh}
                loading={loading}
                icon={<RefreshCw size={14} strokeWidth={1.5} />}
              >
                {t("common.refresh")}
              </Button>
            </Space>
          )
        }
      />

      {!isSystemAdmin ? (
        <Alert
          type="warning"
          showIcon
          message={t("settings_platform.not_admin_title")}
          description={t("settings_platform.not_admin_body")}
          data-testid="pc-not-admin"
        />
      ) : (
        <>
          {error !== null && (
            <Alert
              type="error"
              showIcon
              message={t("settings_platform.failed_to_load")}
              description={error}
              style={{ marginBottom: 12 }}
              data-testid="pc-error"
            />
          )}
          <h2 style={{ fontSize: 15, margin: "8px 0" }}>
            {t("settings_platform.providers_heading")}
          </h2>
          <Table<PlatformProviderRow>
            columns={providerColumns as TableColumnsType<PlatformProviderRow>}
            dataSource={view?.providers ?? []}
            rowKey={(r) => r.provider}
            loading={loading}
            pagination={false}
            size="small"
            data-testid="pc-providers-table"
          />
          <h2 style={{ fontSize: 15, margin: "20px 0 8px" }}>
            {t("settings_platform.tools_heading")}
          </h2>
          <Table<PlatformToolRow>
            columns={toolColumns as TableColumnsType<PlatformToolRow>}
            dataSource={view?.tools ?? []}
            rowKey={(r) => r.tool}
            loading={loading}
            pagination={false}
            size="small"
            data-testid="pc-tools-table"
          />

          <h2 style={{ fontSize: 15, margin: "20px 0 8px" }}>
            {t("settings_platform.embedding_heading")}
          </h2>
          <PlatformEmbeddingSection />

          <h2 style={{ fontSize: 15, margin: "20px 0 8px" }}>
            {t("settings_platform.judge_heading")}
          </h2>
          <PlatformJudgeSection />

          <h2 style={{ fontSize: 15, margin: "20px 0 8px" }}>
            {t("settings_platform.billing_heading")}
          </h2>
          <PlatformBillingSection />
        </>
      )}

      <Drawer
        title={t("settings_platform.tenant_drawer_title")}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        width={720}
        data-testid="pc-tenant-drawer"
      >
        <Form.Item label={t("settings_platform.tenant_select_label")}>
          <Select
            showSearch
            optionFilterProp="label"
            placeholder={t("settings_platform.tenant_select_placeholder")}
            value={tenantId}
            onChange={onPickTenant}
            options={tenants.map((tn) => ({
              value: tn.tenant_id,
              label: `${tn.display_name} (${tn.tenant_id.slice(0, 8)}…)`,
            }))}
            style={{ width: "100%" }}
            data-testid="pc-tenant-select"
          />
        </Form.Item>
        {tenantId !== null && (
          <>
            <Alert
              type="info"
              showIcon
              message={t("settings_platform.tenant_drawer_hint")}
              style={{ marginBottom: 12 }}
            />
            <h3 style={{ fontSize: 14, margin: "8px 0" }}>
              {t("settings_platform.providers_heading")}
            </h3>
            <Table<TenantProviderEntry>
              columns={
                overrideProviderColumns as TableColumnsType<TenantProviderEntry>
              }
              dataSource={tenantView?.providers ?? []}
              rowKey={(r) => r.provider}
              loading={tenantLoading}
              pagination={false}
              size="small"
              data-testid="pc-tenant-providers-table"
            />
            <h3 style={{ fontSize: 14, margin: "16px 0 8px" }}>
              {t("settings_platform.tools_heading")}
            </h3>
            <Table<TenantToolEntry>
              columns={overrideToolColumns as TableColumnsType<TenantToolEntry>}
              dataSource={tenantView?.tools ?? []}
              rowKey={(r) => r.tool}
              loading={tenantLoading}
              pagination={false}
              size="small"
              data-testid="pc-tenant-tools-table"
            />
          </>
        )}
      </Drawer>

      <Modal
        title={
          edit?.tenantId !== undefined
            ? t("settings_platform.edit_override_modal_title", {
                key: edit?.key ?? "",
              })
            : t("settings_platform.edit_modal_title", { key: edit?.key ?? "" })
        }
        open={edit !== null}
        onCancel={() => setEdit(null)}
        onOk={onSaveEdit}
        confirmLoading={saving}
        okText={t("common.save")}
        cancelText={t("common.cancel")}
        data-testid="pc-edit-modal"
      >
        <Form form={editForm} layout="vertical">
          <Form.Item label={t("settings_platform.mode_label")}>
            <Segmented
              value={editMode}
              onChange={(v) => setEditMode(v as "value" | "ref")}
              options={[
                { label: t("settings_platform.mode_value"), value: "value" },
                { label: t("settings_platform.mode_ref"), value: "ref" },
              ]}
              aria-label={t("settings_platform.mode_label")}
              data-testid="pc-edit-mode"
            />
          </Form.Item>
          {editMode === "value" ? (
            <Form.Item
              name="value"
              label={t("settings_platform.value_label")}
              extra={t("settings_platform.value_hint")}
              rules={[
                {
                  validator: (_r, value: string) =>
                    typeof value === "string" && value.length > 0
                      ? Promise.resolve()
                      : Promise.reject(
                          new Error(t("settings_platform.value_required")),
                        ),
                },
              ]}
            >
              <Input.Password
                placeholder="sk-ant-…"
                autoComplete="off"
                data-testid="pc-edit-value"
              />
            </Form.Item>
          ) : (
            <Form.Item
              name="secret_ref"
              label={t("settings_platform.secret_ref_label")}
              extra={t("settings_platform.secret_ref_hint")}
              rules={[
                {
                  validator: (_r, value: string) =>
                    typeof value === "string" &&
                    /^(secret|kms):\/\//.test(value)
                      ? Promise.resolve()
                      : Promise.reject(
                          new Error(t("settings_platform.secret_ref_hint")),
                        ),
                },
              ]}
            >
              <Input
                placeholder="kms://platform/anthropic-key"
                data-testid="pc-edit-ref"
              />
            </Form.Item>
          )}
          <Form.Item
            name="enabled"
            label={t("settings_platform.enabled_label")}
            valuePropName="checked"
          >
            <Switch
              aria-label={t("settings_platform.enabled_label")}
              data-testid="pc-edit-enabled"
            />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
