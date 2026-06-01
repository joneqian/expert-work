/**
 * Settings — Members page (Stream R W2).
 *
 * Tenant members and their invitation lifecycle. An admin invites users
 * by email (batch, max 50), then resends invites or removes members.
 * ``DELETE`` is lifecycle-aware: an ``invited`` row is revoked, an
 * ``active`` row is suspended. Invitation is a batch call that returns
 * per-item results, so a partial failure (Keycloak conflict / down)
 * surfaces the failing emails without aborting the whole batch.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  App,
  Breadcrumb,
  Button,
  Drawer,
  Empty,
  Form,
  Input,
  Popconfirm,
  Segmented,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import type { TableColumnsType } from "antd";
import { ChevronRight, RefreshCw, Send, Trash2, UserPlus, Users } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  inviteMembers,
  listMembers,
  resendMember,
  revokeMember,
  type InvitationItem,
  type MemberList,
  type MemberRole,
  type MemberStatus,
  type TenantMember,
} from "../api/members";
import { ApiError } from "../api/client";
import { useTenantScope } from "../tenant/TenantScopeContext";

const { Text } = Typography;

const ROLE_OPTIONS: MemberRole[] = ["admin", "operator", "viewer"];

type StatusFilter = "all" | MemberStatus;

const STATUS_FILTERS: StatusFilter[] = [
  "all",
  "invited",
  "active",
  "suspended",
  "revoked",
];

const STATUS_TAG_COLOR: Record<MemberStatus, string> = {
  invited: "purple",
  active: "cyan",
  suspended: "gold",
  revoked: "default",
};

interface InviteForm {
  email: string;
  role: MemberRole;
  display_name?: string;
}

export function SettingsMembers() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { scope } = useTenantScope();

  const [data, setData] = useState<MemberList | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");

  const [inviteOpen, setInviteOpen] = useState(false);
  const [inviteSubmitting, setInviteSubmitting] = useState(false);
  const [inviteForm] = Form.useForm<InviteForm>();

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await listMembers({
        status: statusFilter === "all" ? undefined : statusFilter,
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
  }, [statusFilter]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const onInvite = useCallback(async () => {
    const values = await inviteForm.validateFields();
    const invitation: InvitationItem = {
      email: values.email.trim(),
      role: values.role,
      ...(values.display_name?.trim()
        ? { display_name: values.display_name.trim() }
        : {}),
    };
    setInviteSubmitting(true);
    try {
      const result = await inviteMembers([invitation]);
      const failed = result.results.filter((r) => r.error_code !== null);
      if (failed.length > 0) {
        const detail = failed
          .map((r) => `${r.email} (${r.error_code})`)
          .join(", ");
        message.warning(t("settings_members.invite_partial_fail", { detail }));
      } else {
        message.success(t("settings_members.invite_success"));
      }
      setInviteOpen(false);
      inviteForm.resetFields();
      refresh();
    } catch (err) {
      message.error(err instanceof Error ? err.message : "failed");
    } finally {
      setInviteSubmitting(false);
    }
  }, [inviteForm, message, refresh, t]);

  const onResend = useCallback(
    async (id: string) => {
      try {
        await resendMember(id);
        message.success(t("settings_members.resent"));
        refresh();
      } catch (err) {
        message.error(err instanceof Error ? err.message : "failed");
      }
    },
    [message, refresh, t],
  );

  const onRemove = useCallback(
    async (id: string) => {
      try {
        await revokeMember(id);
        message.success(t("settings_members.removed"));
        refresh();
      } catch (err) {
        message.error(err instanceof Error ? err.message : "failed");
      }
    },
    [message, refresh, t],
  );

  const columns: TableColumnsType<TenantMember> = useMemo(
    () => [
      {
        title: t("settings_members.col_email"),
        dataIndex: "email",
        key: "email",
        render: (email: string) => (
          <Tooltip title={email}>
            <Text code style={{ fontSize: 11 }}>
              {email}
            </Text>
          </Tooltip>
        ),
      },
      {
        title: t("settings_members.col_name"),
        dataIndex: "display_name",
        key: "display_name",
        width: 180,
        render: (name: string | null) =>
          name === null || name === "" ? (
            <Text type="secondary" style={{ fontSize: 12 }}>
              —
            </Text>
          ) : (
            <Text style={{ fontSize: 13 }}>{name}</Text>
          ),
      },
      {
        title: t("settings_members.col_role"),
        dataIndex: "role",
        key: "role",
        width: 120,
        render: (r: MemberRole) => (
          <Tag color={r === "admin" ? "blue" : "default"}>{r}</Tag>
        ),
      },
      {
        title: t("settings_members.col_status"),
        dataIndex: "status",
        key: "status",
        width: 130,
        render: (s: MemberStatus) => (
          <Tag color={STATUS_TAG_COLOR[s]}>{t(`settings_members.status_${s}`)}</Tag>
        ),
      },
      {
        title: t("settings_members.col_invited_at"),
        dataIndex: "invited_at",
        key: "invited_at",
        width: 180,
        render: (iso: string | null) =>
          iso === null ? (
            <Text type="secondary" style={{ fontSize: 12 }}>
              —
            </Text>
          ) : (
            <Text type="secondary" style={{ fontSize: 12 }}>
              {new Date(iso).toLocaleString()}
            </Text>
          ),
      },
      {
        title: t("settings_members.col_actions"),
        key: "actions",
        width: 200,
        render: (_, record) => {
          const removable =
            record.status === "invited" || record.status === "active";
          return (
            <Space size={6}>
              {record.status === "invited" && (
                <Button
                  size="small"
                  icon={<Send size={12} strokeWidth={1.75} />}
                  onClick={() => onResend(record.id)}
                  data-testid={`members-resend-${record.id}`}
                >
                  {t("settings_members.resend")}
                </Button>
              )}
              {removable && (
                <Popconfirm
                  title={
                    record.status === "invited"
                      ? t("settings_members.revoke_confirm_title")
                      : t("settings_members.suspend_confirm_title")
                  }
                  description={
                    record.status === "invited"
                      ? t("settings_members.revoke_confirm_body")
                      : t("settings_members.suspend_confirm_body")
                  }
                  okType="danger"
                  okText={t("common.delete")}
                  cancelText={t("common.cancel")}
                  onConfirm={() => onRemove(record.id)}
                >
                  <Button
                    size="small"
                    danger
                    icon={<Trash2 size={12} strokeWidth={1.75} />}
                    data-testid={`members-remove-${record.id}`}
                  >
                    {t("settings_members.remove")}
                  </Button>
                </Popconfirm>
              )}
            </Space>
          );
        },
      },
    ],
    [t, onResend, onRemove],
  );

  return (
    <div data-testid="members-root">
      <div className="hx-page-header">
        <Breadcrumb
          separator={<ChevronRight size={12} strokeWidth={1.5} />}
          items={[
            { title: t("common.home") },
            { title: t("settings_members.page_title") },
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
          <Users size={20} strokeWidth={1.5} />
          <h1 style={{ margin: 0 }}>{t("settings_members.page_title")}</h1>
          <span style={{ flex: 1 }} />
          <Segmented<StatusFilter>
            value={statusFilter}
            onChange={(value) => setStatusFilter(value)}
            options={STATUS_FILTERS.map((s) => ({
              value: s,
              label:
                s === "all"
                  ? t("settings_members.filter_all")
                  : t(`settings_members.status_${s}`),
            }))}
            data-testid="members-status-filter"
          />
          <Button
            onClick={refresh}
            loading={loading}
            icon={<RefreshCw size={14} strokeWidth={1.5} />}
          >
            {t("common.refresh")}
          </Button>
          <Button
            type="primary"
            icon={<UserPlus size={14} strokeWidth={1.5} />}
            onClick={() => setInviteOpen(true)}
            data-testid="members-invite-btn"
          >
            {t("settings_members.invite")}
          </Button>
        </div>
        <p
          style={{
            color: "var(--hx-text-secondary)",
            fontSize: 13,
            margin: "0 0 12px",
          }}
        >
          {t("settings_members.subtitle")}
        </p>
      </div>

      {error !== null && (
        <Alert
          type="error"
          showIcon
          message={t("settings_members.failed_to_load")}
          description={error}
          style={{ marginBottom: 12 }}
          data-testid="members-error"
        />
      )}

      <Table<TenantMember>
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
                  ? t("settings_members.empty_cross")
                  : t("settings_members.empty")
              }
            />
          ),
        }}
        data-testid="members-table"
      />

      <Drawer
        title={t("settings_members.invite_drawer_title")}
        open={inviteOpen}
        onClose={() => setInviteOpen(false)}
        width={520}
        data-testid="members-invite-drawer"
        extra={
          <Space>
            <Button onClick={() => setInviteOpen(false)}>
              {t("common.cancel")}
            </Button>
            <Button
              type="primary"
              loading={inviteSubmitting}
              onClick={onInvite}
              data-testid="members-invite-submit"
            >
              {t("settings_members.invite_submit")}
            </Button>
          </Space>
        }
      >
        <Form
          form={inviteForm}
          layout="vertical"
          initialValues={{ role: "viewer" }}
        >
          <Form.Item
            name="email"
            label={t("settings_members.field_email")}
            rules={[
              {
                required: true,
                message: t("settings_members.email_required"),
              },
              { type: "email", message: t("settings_members.email_invalid") },
            ]}
          >
            <Input
              data-testid="members-invite-email"
              placeholder="user@example.com"
              autoComplete="off"
            />
          </Form.Item>
          <Form.Item
            name="role"
            label={t("settings_members.field_role")}
            rules={[{ required: true, message: t("settings_members.role_required") }]}
          >
            <Select<MemberRole>
              data-testid="members-invite-role"
              options={ROLE_OPTIONS.map((r) => ({ value: r, label: r }))}
            />
          </Form.Item>
          <Form.Item
            name="display_name"
            label={t("settings_members.field_display_name")}
          >
            <Input
              data-testid="members-invite-display-name"
              placeholder={t("settings_members.field_display_name_placeholder")}
              autoComplete="off"
            />
          </Form.Item>
        </Form>
      </Drawer>
    </div>
  );
}
