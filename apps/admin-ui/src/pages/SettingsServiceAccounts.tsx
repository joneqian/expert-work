/**
 * Settings — Service Accounts page (Stream H.4 PR 7).
 *
 * Non-human identity registry. SA holds zero, one, or many API keys
 * (managed on the ``/settings/api-keys`` page). Create here is a thin
 * name + description form; rotation / revoke of the SA's keys happens
 * on the API Keys page.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  App,
  Button,
  Empty,
  Form,
  Input,
  Modal,
  Popconfirm,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import type { TableColumnsType } from "antd";
import { Globe2, KeyRound, Plus, RefreshCw, Trash2, UserCog } from "lucide-react";
import { useTranslation } from "react-i18next";

import { PageHeader } from "../components/PageHeader";
import {
  createServiceAccount,
  deleteServiceAccount,
  listServiceAccounts,
  type ServiceAccountList,
  type ServiceAccountRecord,
} from "../api/service_accounts";
import { ApiError } from "../api/client";
import { useTenantScope } from "../tenant/TenantScopeContext";

const { Text } = Typography;

export function SettingsServiceAccounts() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { scope, apiTenantScope } = useTenantScope();

  const [data, setData] = useState<ServiceAccountList | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [createOpen, setCreateOpen] = useState(false);
  const [createSubmitting, setCreateSubmitting] = useState(false);
  const [createForm] = Form.useForm<{ name: string; description: string }>();

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await listServiceAccounts({ tenantScope: apiTenantScope });
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
  }, [apiTenantScope]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const onCreate = useCallback(async () => {
    const values = await createForm.validateFields();
    setCreateSubmitting(true);
    try {
      await createServiceAccount(values);
      message.success(t("settings_iam.sa_created"));
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
        await deleteServiceAccount(id);
        message.success(t("settings_iam.sa_deleted"));
        refresh();
      } catch (err) {
        message.error(err instanceof Error ? err.message : "failed");
      }
    },
    [message, refresh, t],
  );

  const columns: TableColumnsType<ServiceAccountRecord> = useMemo(() => [
    {
      title: t("settings_iam.col_name"),
      dataIndex: "name",
      key: "name",
      render: (v: string) => <Text strong>{v}</Text>,
    },
    {
      title: t("settings_iam.col_description"),
      dataIndex: "description",
      key: "description",
      ellipsis: true,
      render: (text: string) => (
        <Tooltip title={text} mouseEnterDelay={0.4}>
          <Text style={{ fontSize: 12 }}>
            {text.length === 0 ? "—" : text}
          </Text>
        </Tooltip>
      ),
    },
    {
      title: t("settings_iam.col_active"),
      dataIndex: "is_active",
      key: "is_active",
      width: 120,
      render: (v: boolean) =>
        v ? <Tag color="success">active</Tag> : <Tag>inactive</Tag>,
    },
    {
      title: t("settings_iam.col_created"),
      dataIndex: "created_at",
      key: "created_at",
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
          title={t("settings_iam.sa_delete_confirm_title")}
          description={t("settings_iam.sa_delete_confirm_body")}
          okType="danger"
          okText={t("common.delete")}
          cancelText={t("common.cancel")}
          onConfirm={() => onDelete(record.id)}
        >
          <Button
            size="small"
            danger
            icon={<Trash2 size={12} strokeWidth={1.75} />}
            data-testid={`sa-delete-${record.id}`}
          >
            {t("common.delete")}
          </Button>
        </Popconfirm>
      ),
    },
  ], [t, onDelete]);

  const isCrossTenant = data?.cross_tenant ?? false;

  return (
    <div data-testid="sa-root">
      <PageHeader
        icon={<UserCog size={18} strokeWidth={1.5} />}
        title={t("settings_iam.sa_page_title")}
        subtitle={t("settings_iam.sa_subtitle")}
        actions={
          <>
            {isCrossTenant && (
              <Tag icon={<Globe2 size={12} strokeWidth={1.5} />} color="purple" data-testid="sa-cross-banner">
                {t("settings_iam.cross_tenant_banner")}
              </Tag>
            )}
            <Button onClick={refresh} loading={loading} icon={<RefreshCw size={14} strokeWidth={1.5} />}>
              {t("common.refresh")}
            </Button>
            <Button
              type="primary"
              icon={<Plus size={14} strokeWidth={1.75} />}
              onClick={() => setCreateOpen(true)}
              data-testid="sa-create-btn"
            >
              {t("settings_iam.sa_create")}
            </Button>
          </>
        }
      />
      <p style={{ fontSize: 12, color: "var(--ew-text-tertiary)", margin: "0 0 12px" }}>
        <Space size={6}>
          <KeyRound size={12} strokeWidth={1.75} />
          {t("settings_iam.sa_api_keys_hint")}
        </Space>
      </p>

      {error !== null && (
        <Alert
          type="error"
          showIcon
          message={t("settings_iam.failed_to_load")}
          description={error}
          style={{ marginBottom: 12 }}
          data-testid="sa-error"
        />
      )}

      <Table<ServiceAccountRecord>
        columns={columns}
        dataSource={data?.items ?? []}
        rowKey={(r) => r.id}
        loading={loading}
        pagination={{ pageSize: 50, showSizeChanger: false, total: data?.total ?? 0 }}
        locale={{
          emptyText: (
            <Empty description={scope === "*" ? t("settings_iam.sa_empty_cross") : t("settings_iam.sa_empty_home")} />
          ),
        }}
        data-testid="sa-table"
      />

      <Modal
        title={t("settings_iam.sa_create_modal_title")}
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={onCreate}
        confirmLoading={createSubmitting}
        data-testid="sa-create-modal"
      >
        <Form form={createForm} layout="vertical">
          <Form.Item
            name="name"
            label={t("settings_iam.field_name")}
            rules={[{ required: true, message: t("settings_iam.name_required") }]}
          >
            <Input data-testid="sa-name-input" maxLength={128} placeholder="sa_data_pipeline" />
          </Form.Item>
          <Form.Item name="description" label={t("settings_iam.field_description")}>
            <Input.TextArea data-testid="sa-description-input" rows={3} maxLength={512} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
