/**
 * Settings — Role Bindings page (Stream H.4 PR 7).
 *
 * Subject (user / service_account) → role mappings. The
 * ``platform_scope`` checkbox in the Create drawer is only visible to
 * system_admins (mirrors backend gate at ``role_bindings.py:66``);
 * checking it requires type-to-confirm ``CONFIRM PLATFORM ROLE`` to
 * prevent accidental cross-tenant elevation.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  App,
  Button,
  Checkbox,
  Drawer,
  Empty,
  Form,
  Input,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import type { TableColumnsType } from "antd";
import { Globe2, RefreshCw, Shield, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { PageHeader } from "../components/PageHeader";
import {
  createRoleBinding,
  deleteRoleBinding,
  listRoleBindings,
  type BindingConditions,
  type CreateRoleBindingBody,
  type RoleBindingList,
  type RoleBindingRecord,
  type RoleName,
  type SubjectType,
} from "../api/role_bindings";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { useTenantScope } from "../tenant/TenantScopeContext";

const { Text } = Typography;

const SUBJECT_OPTIONS: SubjectType[] = ["user", "service_account"];
const ROLE_OPTIONS: RoleName[] = [
  "tenant_admin",
  "developer",
  "viewer",
  "system_admin",
];
const CONFIRM_PHRASE = "CONFIRM PLATFORM ROLE";

interface CreateForm {
  subject_type: SubjectType;
  subject_id: string;
  role: RoleName;
  platform_scope: boolean;
  confirm: string;
  // Stream 8.5 — ABAC conditions (tenant-scope only).
  resource_ids?: string[];
  labels_raw?: string;
  owner_only?: boolean;
}

/** Parse a ``k=v, k2=v2`` string into a labels map (blank ⇒ ``{}``). */
export function parseLabels(raw: string | undefined): Record<string, string> {
  const out: Record<string, string> = {};
  for (const pair of (raw ?? "").split(",")) {
    const idx = pair.indexOf("=");
    if (idx <= 0) continue;
    const key = pair.slice(0, idx).trim();
    const value = pair.slice(idx + 1).trim();
    if (key) out[key] = value;
  }
  return out;
}

/** Build conditions from form values; ``undefined`` when no predicate is set. */
export function buildConditions(values: {
  resource_ids?: string[];
  labels_raw?: string;
  owner_only?: boolean;
}): BindingConditions | undefined {
  const resourceIds = (values.resource_ids ?? []).filter((s) => s.trim() !== "");
  const labels = parseLabels(values.labels_raw);
  const ownerOnly = values.owner_only ?? false;
  if (resourceIds.length === 0 && Object.keys(labels).length === 0 && !ownerOnly) {
    return undefined;
  }
  return {
    ...(resourceIds.length > 0 ? { resource_ids: resourceIds } : {}),
    ...(Object.keys(labels).length > 0 ? { labels } : {}),
    ...(ownerOnly ? { owner_only: true } : {}),
  };
}

/** Compact one-line summary of a binding's conditions for the table. */
export function summariseConditions(c: BindingConditions | null | undefined): string | null {
  if (!c) return null;
  const parts: string[] = [];
  if (c.resource_ids && c.resource_ids.length > 0) parts.push(`ids:${c.resource_ids.length}`);
  if (c.labels && Object.keys(c.labels).length > 0) {
    parts.push(Object.entries(c.labels).map(([k, v]) => `${k}=${v}`).join(" "));
  }
  if (c.owner_only) parts.push("owner");
  return parts.length > 0 ? parts.join(" · ") : null;
}

export function SettingsRoleBindings() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { scope, apiTenantScope } = useTenantScope();
  const auth = useAuth();
  const isSystemAdmin = auth.identity?.isSystemAdmin ?? false;

  const [data, setData] = useState<RoleBindingList | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [platformScopeFilter, setPlatformScopeFilter] = useState(false);

  const [createOpen, setCreateOpen] = useState(false);
  const [createSubmitting, setCreateSubmitting] = useState(false);
  const [createForm] = Form.useForm<CreateForm>();
  const platformScopeChecked = Form.useWatch("platform_scope", createForm) ?? false;
  const roleSelected = Form.useWatch("role", createForm);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await listRoleBindings({
        tenantScope: apiTenantScope,
        platformScope: platformScopeFilter,
      });
      setData(result);
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
  }, [apiTenantScope, platformScopeFilter]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const onCreate = useCallback(async () => {
    const values = await createForm.validateFields();
    if (values.platform_scope && values.confirm !== CONFIRM_PHRASE) {
      message.error(t("settings_iam.rb_confirm_required"));
      return;
    }
    const body: CreateRoleBindingBody = {
      subject_type: values.subject_type,
      subject_id: values.subject_id,
      role: values.role,
      platform_scope: values.platform_scope,
      // Stream 8.5 — conditions only apply to tenant-scope bindings.
      ...(values.platform_scope ? {} : { conditions: buildConditions(values) ?? null }),
    };
    setCreateSubmitting(true);
    try {
      await createRoleBinding(body);
      message.success(t("settings_iam.rb_created"));
      setCreateOpen(false);
      createForm.resetFields();
      refresh();
    } catch (err) {
      message.error(err instanceof Error ? err.message : "failed");
    } finally {
      setCreateSubmitting(false);
    }
  }, [createForm, message, refresh, t]);

  const onDelete = useCallback(
    async (id: string) => {
      try {
        await deleteRoleBinding(id);
        message.success(t("settings_iam.rb_deleted"));
        refresh();
      } catch (err) {
        message.error(err instanceof Error ? err.message : "failed");
      }
    },
    [message, refresh, t],
  );

  const columns: TableColumnsType<RoleBindingRecord> = useMemo(() => [
    {
      title: t("settings_iam.col_subject_type"),
      dataIndex: "subject_type",
      key: "subject_type",
      width: 140,
      render: (v: SubjectType) => <Tag>{v}</Tag>,
    },
    {
      title: t("settings_iam.col_subject_id"),
      dataIndex: "subject_id",
      key: "subject_id",
      render: (uid: string) => (
        <Tooltip title={uid}>
          <Text code style={{ fontSize: 11 }}>{uid}</Text>
        </Tooltip>
      ),
    },
    {
      title: t("settings_iam.col_role"),
      dataIndex: "role",
      key: "role",
      width: 160,
      render: (r: RoleName, record) => (
        <Space size={6}>
          <Tag color={r === "system_admin" ? "magenta" : "blue"}>{r}</Tag>
          {record.platform_scope && (
            <Tag color="purple" icon={<Globe2 size={11} strokeWidth={1.75} />} bordered={false}>
              platform
            </Tag>
          )}
        </Space>
      ),
    },
    {
      title: t("settings_iam.col_tenant"),
      dataIndex: "tenant_id",
      key: "tenant",
      width: 140,
      render: (tid: string | null) => (
        tid === null ? (
          <Text type="secondary" style={{ fontSize: 12 }}>—</Text>
        ) : (
          <Text code style={{ fontSize: 11 }}>{tid.slice(0, 8)}…</Text>
        )
      ),
    },
    {
      title: t("settings_iam.col_conditions"),
      key: "conditions",
      width: 200,
      render: (_, record) => {
        const summary = summariseConditions(record.conditions);
        return summary === null ? (
          <Text type="secondary" style={{ fontSize: 12 }}>
            {t("settings_iam.conditions_none")}
          </Text>
        ) : (
          <Tag color="cyan" data-testid={`rb-conditions-${record.id}`}>
            <Text code style={{ fontSize: 11 }}>{summary}</Text>
          </Tag>
        );
      },
    },
    {
      title: t("settings_iam.col_granted_at"),
      dataIndex: "granted_at",
      key: "granted_at",
      width: 180,
      render: (iso: string) => (
        <Text type="secondary" style={{ fontSize: 12 }}>
          {new Date(iso).toLocaleString()}
        </Text>
      ),
    },
    {
      title: t("settings_iam.col_actions"),
      key: "actions",
      width: 120,
      render: (_, record) => (
        <Popconfirm
          title={t("settings_iam.rb_delete_confirm_title")}
          description={
            record.platform_scope
              ? t("settings_iam.rb_delete_platform_warn")
              : t("settings_iam.rb_delete_confirm_body")
          }
          okType="danger"
          okText={t("common.delete")}
          cancelText={t("common.cancel")}
          onConfirm={() => onDelete(record.id)}
        >
          <Button
            size="small"
            danger
            icon={<Trash2 size={12} strokeWidth={1.75} />}
            data-testid={`rb-delete-${record.id}`}
          >
            {t("common.delete")}
          </Button>
        </Popconfirm>
      ),
    },
  ], [t, onDelete]);

  const isCrossTenant = data?.cross_tenant ?? false;

  return (
    <div data-testid="rb-root">
      <PageHeader
        icon={<Shield size={18} strokeWidth={1.5} />}
        title={t("settings_iam.rb_page_title")}
        subtitle={t("settings_iam.rb_subtitle")}
        actions={
          <>
            {isCrossTenant && (
              <Tag icon={<Globe2 size={12} strokeWidth={1.5} />} color="purple" data-testid="rb-cross-banner">
                {t("settings_iam.cross_tenant_banner")}
              </Tag>
            )}
            {isSystemAdmin && (
              <Checkbox
                checked={platformScopeFilter}
                onChange={(e) => setPlatformScopeFilter(e.target.checked)}
                data-testid="rb-platform-scope-filter"
              >
                {t("settings_iam.rb_filter_platform_scope")}
              </Checkbox>
            )}
            <Button onClick={refresh} loading={loading} icon={<RefreshCw size={14} strokeWidth={1.5} />}>
              {t("common.refresh")}
            </Button>
            <Button
              type="primary"
              onClick={() => setCreateOpen(true)}
              data-testid="rb-create-btn"
            >
              {t("settings_iam.rb_create")}
            </Button>
          </>
        }
      />

      {error !== null && (
        <Alert
          type="error"
          showIcon
          message={t("settings_iam.failed_to_load")}
          description={error}
          style={{ marginBottom: 12 }}
          data-testid="rb-error"
        />
      )}

      <Table<RoleBindingRecord>
        columns={columns}
        dataSource={data?.items ?? []}
        rowKey={(r) => r.id}
        loading={loading}
        pagination={{ pageSize: 50, showSizeChanger: false, total: data?.total ?? 0 }}
        locale={{
          emptyText: (
            <Empty
              description={
                scope === "*"
                  ? t("settings_iam.rb_empty_cross")
                  : platformScopeFilter
                    ? t("settings_iam.rb_empty_platform")
                    : t("settings_iam.rb_empty_home")
              }
            />
          ),
        }}
        data-testid="rb-table"
      />

      <Drawer
        title={t("settings_iam.rb_create_drawer_title")}
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        width={520}
        data-testid="rb-create-drawer"
        extra={
          <Space>
            <Button onClick={() => setCreateOpen(false)}>{t("common.cancel")}</Button>
            <Button
              type={platformScopeChecked ? "primary" : "primary"}
              danger={platformScopeChecked}
              loading={createSubmitting}
              onClick={onCreate}
              data-testid="rb-create-submit"
            >
              {platformScopeChecked
                ? t("settings_iam.rb_create_platform_submit")
                : t("settings_iam.rb_create_submit")}
            </Button>
          </Space>
        }
      >
        <Form form={createForm} layout="vertical" initialValues={{ subject_type: "user", platform_scope: false }}>
          <Form.Item
            name="subject_type"
            label={t("settings_iam.field_subject_type")}
            rules={[{ required: true }]}
          >
            <Select<SubjectType>
              data-testid="rb-subject-type-select"
              options={SUBJECT_OPTIONS.map((s) => ({ value: s, label: s }))}
            />
          </Form.Item>
          <Form.Item
            name="subject_id"
            label={t("settings_iam.field_subject_id")}
            rules={[{ required: true, message: t("settings_iam.subject_id_required") }]}
          >
            <Input data-testid="rb-subject-id-input" placeholder="UUID" />
          </Form.Item>
          <Form.Item
            name="role"
            label={t("settings_iam.field_role")}
            rules={[{ required: true, message: t("settings_iam.role_required") }]}
          >
            <Select<RoleName>
              data-testid="rb-role-select"
              options={ROLE_OPTIONS.map((r) => ({ value: r, label: r }))}
            />
          </Form.Item>
          {isSystemAdmin && (
            <Form.Item
              name="platform_scope"
              valuePropName="checked"
              extra={t("settings_iam.platform_scope_hint")}
            >
              <Checkbox data-testid="rb-platform-scope-checkbox">
                {t("settings_iam.field_platform_scope")}
              </Checkbox>
            </Form.Item>
          )}
          {platformScopeChecked && (
            <>
              <Alert
                type="warning"
                showIcon
                message={t("settings_iam.platform_scope_warn_title")}
                description={t("settings_iam.platform_scope_warn_body")}
                style={{ marginBottom: 12 }}
              />
              <Form.Item
                name="confirm"
                label={t("settings_iam.confirm_phrase_label", { phrase: CONFIRM_PHRASE })}
                rules={[
                  {
                    validator: (_, value) =>
                      value === CONFIRM_PHRASE
                        ? Promise.resolve()
                        : Promise.reject(new Error(t("settings_iam.rb_confirm_required"))),
                  },
                ]}
              >
                <Input
                  placeholder={CONFIRM_PHRASE}
                  style={{ fontFamily: "var(--hx-font-mono)" }}
                  data-testid="rb-confirm-input"
                />
              </Form.Item>
            </>
          )}
          {roleSelected === "system_admin" && !platformScopeChecked && (
            <Alert
              type="info"
              showIcon
              message={t("settings_iam.role_requires_platform_scope")}
            />
          )}
          {!platformScopeChecked && (
            <div data-testid="rb-conditions-section">
              <Text strong style={{ display: "block", marginBottom: 8 }}>
                {t("settings_iam.section_conditions")}
              </Text>
              <Text type="secondary" style={{ display: "block", marginBottom: 12, fontSize: 12 }}>
                {t("settings_iam.conditions_hint")}
              </Text>
              <Form.Item
                name="resource_ids"
                label={t("settings_iam.field_resource_ids")}
                extra={t("settings_iam.resource_ids_hint")}
              >
                <Select<string[]>
                  mode="tags"
                  open={false}
                  data-testid="rb-resource-ids-select"
                  placeholder={t("settings_iam.resource_ids_placeholder")}
                  tokenSeparators={[",", " "]}
                />
              </Form.Item>
              <Form.Item
                name="labels_raw"
                label={t("settings_iam.field_labels")}
                extra={t("settings_iam.labels_hint")}
              >
                <Input data-testid="rb-labels-input" placeholder="team=支持, env=dev" />
              </Form.Item>
              <Form.Item name="owner_only" valuePropName="checked">
                <Checkbox data-testid="rb-owner-only-checkbox">
                  {t("settings_iam.field_owner_only")}
                </Checkbox>
              </Form.Item>
            </div>
          )}
        </Form>
      </Drawer>
    </div>
  );
}
