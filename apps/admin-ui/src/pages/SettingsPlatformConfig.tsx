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
  Breadcrumb,
  Button,
  Form,
  Input,
  Modal,
  Popconfirm,
  Segmented,
  Space,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import type { TableColumnsType } from "antd";
import { ChevronRight, KeyRound, RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  deletePlatformProvider,
  deletePlatformTool,
  getPlatformCredentials,
  upsertPlatformProvider,
  upsertPlatformTool,
  type PlatformCredentialsView,
  type PlatformProviderRow,
  type PlatformSecretSource,
  type PlatformSecretUpsertBody,
  type PlatformToolRow,
} from "../api/platform_config";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { PlatformEmbeddingSection } from "./settings_platform/PlatformEmbeddingSection";

const { Text } = Typography;

type Kind = "provider" | "tool";

interface EditTarget {
  kind: Kind;
  key: string;
  secret_ref: string;
}

function sourceTag(source: PlatformSecretSource, t: (k: string) => string) {
  const color = source === "db" ? "cyan" : source === "env" ? "default" : undefined;
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
  const [editForm] = Form.useForm<{ secret_ref?: string; value?: string; enabled: boolean }>();
  const [editMode, setEditMode] = useState<"value" | "ref">("value");
  const [saving, setSaving] = useState(false);

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

  useEffect(() => {
    if (isSystemAdmin) {
      refresh();
    }
  }, [isSystemAdmin, refresh]);

  const errText = useCallback(
    (err: unknown): string =>
      err instanceof ApiError ? `${err.code}: ${err.message}` : err instanceof Error ? err.message : "failed",
    [],
  );

  const doUpsert = useCallback(
    async (kind: Kind, key: string, body: PlatformSecretUpsertBody) => {
      if (kind === "provider") {
        await upsertPlatformProvider(key, body);
      } else {
        await upsertPlatformTool(key, body);
      }
    },
    [],
  );

  const onToggle = useCallback(
    async (kind: Kind, key: string, secret_ref: string | null, enabled: boolean) => {
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
      await doUpsert(edit.kind, edit.key, body);
      message.success(t("settings_platform.saved"));
      setEdit(null);
      refresh();
    } catch (err) {
      message.error(errText(err));
    } finally {
      setSaving(false);
    }
  }, [edit, editMode, editForm, doUpsert, errText, message, refresh, t]);

  const makeColumns = useCallback(
    (kind: Kind): TableColumnsType<PlatformProviderRow | PlatformToolRow> => [
      {
        title: t("settings_platform.col_name"),
        key: "name",
        render: (_v, row) => (
          <Text strong>{"provider" in row ? row.provider : (row as PlatformToolRow).tool}</Text>
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
          const key = "provider" in row ? row.provider : (row as PlatformToolRow).tool;
          return (
            <Switch
              size="small"
              checked={enabled}
              disabled={row.secret_ref === null}
              onChange={(checked) => onToggle(kind, key, row.secret_ref, checked)}
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
        title: t("settings_platform.col_actions"),
        key: "actions",
        width: 180,
        render: (_v, row) => {
          const key = "provider" in row ? row.provider : (row as PlatformToolRow).tool;
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

  useEffect(() => {
    if (edit !== null) {
      // Default to the paste-a-key flow; clear any prior pasted value.
      setEditMode("value");
      editForm.setFieldsValue({ secret_ref: edit.secret_ref, value: "", enabled: true });
    }
  }, [edit, editForm]);

  return (
    <div data-testid="pc-root">
      <div className="hx-page-header">
        <Breadcrumb
          separator={<ChevronRight size={12} strokeWidth={1.5} />}
          items={[{ title: t("common.home") }, { title: t("settings_platform.page_title") }]}
        />
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 8, marginBottom: 16 }}>
          <KeyRound size={20} strokeWidth={1.5} />
          <h1 style={{ margin: 0 }}>{t("settings_platform.page_title")}</h1>
          <span style={{ flex: 1 }} />
          {isSystemAdmin && (
            <Button onClick={refresh} loading={loading} icon={<RefreshCw size={14} strokeWidth={1.5} />}>
              {t("common.refresh")}
            </Button>
          )}
        </div>
        <p style={{ color: "var(--hx-text-secondary)", fontSize: 13, margin: "0 0 12px" }}>
          {t("settings_platform.subtitle")}
        </p>
      </div>

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
          <h2 style={{ fontSize: 15, margin: "8px 0" }}>{t("settings_platform.providers_heading")}</h2>
          <Table<PlatformProviderRow>
            columns={providerColumns as TableColumnsType<PlatformProviderRow>}
            dataSource={view?.providers ?? []}
            rowKey={(r) => r.provider}
            loading={loading}
            pagination={false}
            size="small"
            data-testid="pc-providers-table"
          />
          <h2 style={{ fontSize: 15, margin: "20px 0 8px" }}>{t("settings_platform.tools_heading")}</h2>
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
        </>
      )}

      <Modal
        title={t("settings_platform.edit_modal_title", { key: edit?.key ?? "" })}
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
                      : Promise.reject(new Error(t("settings_platform.value_required"))),
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
                    typeof value === "string" && /^(secret|kms):\/\//.test(value)
                      ? Promise.resolve()
                      : Promise.reject(new Error(t("settings_platform.secret_ref_hint"))),
                },
              ]}
            >
              <Input placeholder="kms://platform/anthropic-key" data-testid="pc-edit-ref" />
            </Form.Item>
          )}
          <Form.Item name="enabled" label={t("settings_platform.enabled_label")} valuePropName="checked">
            <Switch aria-label={t("settings_platform.enabled_label")} data-testid="pc-edit-enabled" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
