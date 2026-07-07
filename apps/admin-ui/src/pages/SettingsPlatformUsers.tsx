/**
 * Settings — Platform Admins page (Stream N self-service).
 *
 * Platform-level (NOT tenant-scoped) management of ``system_admin``
 * grants — the role-bindings overlay with ``platform_scope=true``.
 * system_admin only (mirrors the backend ``is_system_admin`` gate); a
 * non-admin sees a notice. Lists existing platform admins and lets one
 * admin grant the role to another by Keycloak subject UUID, or revoke
 * an existing grant.
 *
 * This is the smooth bootstrap path: the very first admin is created by
 * the backend on first login of ``EXPERT_WORK_BOOTSTRAP_ADMIN_EMAIL``;
 * from there admins self-serve more admins here, no script needed.
 */
import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  App,
  Button,
  Form,
  Input,
  Popconfirm,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import type { TableColumnsType } from "antd";
import { RefreshCw, ShieldCheck, UserPlus } from "lucide-react";
import { useTranslation } from "react-i18next";

import { PageHeader } from "../components/PageHeader";
import {
  createRoleBinding,
  deleteRoleBinding,
  listRoleBindings,
  type RoleBindingRecord,
} from "../api/role_bindings";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";

const { Text } = Typography;

const UUID_RE =
  /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;

interface GrantForm {
  subject_id: string;
}

export function SettingsPlatformUsers() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const auth = useAuth();
  const isSystemAdmin = auth.identity?.isSystemAdmin ?? false;
  const selfSubject = auth.identity?.subject ?? null;

  const [items, setItems] = useState<RoleBindingRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [granting, setGranting] = useState(false);
  const [form] = Form.useForm<GrantForm>();

  const errText = useCallback(
    (err: unknown): string =>
      err instanceof ApiError
        ? `${err.code}: ${err.message}`
        : err instanceof Error
          ? err.message
          : "failed",
    [],
  );

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await listRoleBindings({ platformScope: true });
      setItems(result.items);
    } catch (err) {
      setError(errText(err));
    } finally {
      setLoading(false);
    }
  }, [errText]);

  useEffect(() => {
    if (isSystemAdmin) {
      refresh();
    }
  }, [isSystemAdmin, refresh]);

  const onGrant = useCallback(async () => {
    const values = await form.validateFields();
    setGranting(true);
    try {
      await createRoleBinding({
        subject_type: "user",
        subject_id: values.subject_id.trim(),
        role: "system_admin",
        platform_scope: true,
      });
      message.success(t("settings_platform_users.granted"));
      form.resetFields();
      refresh();
    } catch (err) {
      message.error(errText(err));
    } finally {
      setGranting(false);
    }
  }, [form, errText, message, refresh, t]);

  const onRevoke = useCallback(
    async (id: string) => {
      try {
        await deleteRoleBinding(id);
        message.success(t("settings_platform_users.revoked"));
        refresh();
      } catch (err) {
        message.error(errText(err));
      }
    },
    [errText, message, refresh, t],
  );

  const columns: TableColumnsType<RoleBindingRecord> = [
    {
      title: t("settings_platform_users.col_subject"),
      dataIndex: "subject_id",
      key: "subject_id",
      render: (subjectId: string) => (
        <Space size={6}>
          <Tooltip title={subjectId}>
            <Text code style={{ fontSize: 11 }}>
              {subjectId}
            </Text>
          </Tooltip>
          {subjectId === selfSubject && (
            <Tag color="cyan">{t("settings_platform_users.you")}</Tag>
          )}
        </Space>
      ),
    },
    {
      title: t("settings_platform_users.col_role"),
      dataIndex: "role",
      key: "role",
      width: 150,
      render: (role: string) => <Tag color="blue">{role}</Tag>,
    },
    {
      title: t("settings_platform_users.col_granted_by"),
      dataIndex: "granted_by",
      key: "granted_by",
      width: 200,
      render: (by: string) => (
        <Text type="secondary" style={{ fontSize: 12 }}>
          {by}
        </Text>
      ),
    },
    {
      title: t("settings_platform_users.col_granted_at"),
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
      title: t("settings_platform_users.col_actions"),
      key: "actions",
      width: 120,
      render: (_v, row) => (
        <Popconfirm
          title={t("settings_platform_users.revoke_confirm")}
          description={
            row.subject_id === selfSubject
              ? t("settings_platform_users.revoke_self_warning")
              : undefined
          }
          okType="danger"
          okText={t("common.delete")}
          cancelText={t("common.cancel")}
          onConfirm={() => onRevoke(row.id)}
        >
          <Button size="small" danger data-testid={`pu-revoke-${row.id}`}>
            {t("settings_platform_users.revoke")}
          </Button>
        </Popconfirm>
      ),
    },
  ];

  return (
    <div data-testid="pu-root">
      <PageHeader
        icon={<ShieldCheck size={18} strokeWidth={1.5} />}
        title={t("settings_platform_users.page_title")}
        subtitle={t("settings_platform_users.subtitle")}
        actions={
          isSystemAdmin && (
            <Button
              onClick={refresh}
              loading={loading}
              icon={<RefreshCw size={14} strokeWidth={1.5} />}
            >
              {t("common.refresh")}
            </Button>
          )
        }
      />

      {!isSystemAdmin ? (
        <Alert
          type="warning"
          showIcon
          message={t("settings_platform_users.not_admin_title")}
          description={t("settings_platform_users.not_admin_body")}
          data-testid="pu-not-admin"
        />
      ) : (
        <>
          {error !== null && (
            <Alert
              type="error"
              showIcon
              message={t("settings_platform_users.failed_to_load")}
              description={error}
              style={{ marginBottom: 12 }}
              data-testid="pu-error"
            />
          )}

          <Form
            form={form}
            layout="inline"
            onFinish={onGrant}
            style={{ marginBottom: 16, rowGap: 8 }}
            data-testid="pu-grant-form"
          >
            <Form.Item
              name="subject_id"
              label={t("settings_platform_users.subject_label")}
              extra={t("settings_platform_users.subject_hint")}
              rules={[
                {
                  validator: (_r, value: string) =>
                    typeof value === "string" && UUID_RE.test(value.trim())
                      ? Promise.resolve()
                      : Promise.reject(
                          new Error(t("settings_platform_users.subject_invalid")),
                        ),
                },
              ]}
            >
              <Input
                placeholder="00000000-0000-0000-0000-000000000000"
                style={{ width: 360 }}
                autoComplete="off"
                data-testid="pu-grant-subject"
              />
            </Form.Item>
            <Form.Item>
              <Button
                type="primary"
                htmlType="submit"
                loading={granting}
                icon={<UserPlus size={14} strokeWidth={1.5} />}
                data-testid="pu-grant-submit"
              >
                {t("settings_platform_users.grant")}
              </Button>
            </Form.Item>
          </Form>

          <Table<RoleBindingRecord>
            columns={columns}
            dataSource={items}
            rowKey={(r) => r.id}
            loading={loading}
            pagination={false}
            size="small"
            data-testid="pu-table"
          />
        </>
      )}
    </div>
  );
}
