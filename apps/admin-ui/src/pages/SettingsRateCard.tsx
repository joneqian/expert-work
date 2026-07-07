/**
 * Model Pricing page — 模型定价简化.
 *
 * Platform model pricing over ``/v1/platform/rate-card`` (system_admin only; the
 * frontend gate is UX — the real boundary is the backend's ``require("billing",·)``
 * + ``is_system_admin`` double gate).
 *
 * One price per ``(provider, model)``. Provider + model are picked from the
 * model catalog (``/v1/model-catalog`` — already intersected with configured
 * credentials) and are immutable post-create; repricing edits the row in place.
 * Prices are entered in 元 / 百万 tokens (decimals allowed) and converted to the
 * integer micro-元/百万token storage unit — no implicit unit drift.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  App,
  Button,
  Drawer,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Typography,
} from "antd";
import type { TableColumnsType } from "antd";
import { Banknote, Plus, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  createRateCard,
  cnyToMtokMicros,
  deleteRateCard,
  listRateCards,
  mtokMicrosToCny,
  patchRateCard,
  type RateCardPatch,
  type RateCardRecord,
  type RateCardUpsert,
} from "../api/rate_card";
import { fetchModelCatalog, type ProviderModels } from "../api/model_catalog";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { PageHeader } from "../components/PageHeader";

const { Text } = Typography;

function errMessage(err: unknown): string {
  return err instanceof ApiError
    ? `${err.code}: ${err.message}`
    : err instanceof Error
      ? err.message
      : "unknown error";
}

/** Form values are in 元 / 百万 tokens (display unit). */
interface CreateFormValues {
  provider: string;
  model: string;
  input_cny: number;
  output_cny: number;
  cache_creation_cny?: number;
  cache_read_cny?: number;
}

interface EditFormValues {
  input_cny: number;
  output_cny: number;
  cache_creation_cny: number;
  cache_read_cny: number;
}

/** Price InputNumber in 元 / 百万 tokens (decimals allowed). */
function CnyField({ name, label, required }: { name: string; label: string; required?: boolean }) {
  return (
    <Form.Item name={name} label={label} rules={[{ required: required ?? false }]}>
      <InputNumber
        min={0}
        step={0.1}
        style={{ width: "100%" }}
        aria-label={label}
        data-testid={`rc-${name}`}
      />
    </Form.Item>
  );
}

export function SettingsRateCard() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const auth = useAuth();
  const isSystemAdmin = auth.identity?.isSystemAdmin ?? false;

  const [items, setItems] = useState<RateCardRecord[]>([]);
  const [catalog, setCatalog] = useState<ProviderModels[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [providerFilter, setProviderFilter] = useState("");
  const [modelFilter, setModelFilter] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<RateCardRecord | null>(null);
  const [saving, setSaving] = useState(false);
  const [createForm] = Form.useForm<CreateFormValues>();
  const [editForm] = Form.useForm<EditFormValues>();

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await listRateCards({
        provider: providerFilter || undefined,
        model: modelFilter || undefined,
      });
      setItems(result);
    } catch (err) {
      setError(errMessage(err));
    } finally {
      setLoading(false);
    }
  }, [providerFilter, modelFilter]);

  useEffect(() => {
    if (isSystemAdmin) {
      void refresh();
    }
  }, [isSystemAdmin, refresh]);

  // Load the model catalog once for the create dropdowns.
  useEffect(() => {
    if (!isSystemAdmin) return;
    void fetchModelCatalog()
      .then((c) => setCatalog(c.providers))
      .catch(() => setCatalog([]));
  }, [isSystemAdmin]);

  const providerOptions = useMemo(
    () => catalog.map((p) => ({ value: p.provider, label: p.provider })),
    [catalog],
  );
  const modelsByProvider = useMemo(() => {
    const map = new Map<string, string[]>();
    for (const p of catalog) map.set(p.provider, p.models.map((m) => m.name));
    return map;
  }, [catalog]);

  const handleCreate = useCallback(
    async (values: CreateFormValues) => {
      setCreating(true);
      try {
        const body: RateCardUpsert = {
          provider: values.provider,
          model: values.model,
          input_per_mtok_micros: cnyToMtokMicros(values.input_cny),
          output_per_mtok_micros: cnyToMtokMicros(values.output_cny),
          cache_creation_per_mtok_micros: cnyToMtokMicros(values.cache_creation_cny ?? 0),
          cache_read_per_mtok_micros: cnyToMtokMicros(values.cache_read_cny ?? 0),
        };
        await createRateCard(body);
        setCreateOpen(false);
        createForm.resetFields();
        await refresh();
      } catch (err) {
        message.error(errMessage(err));
      } finally {
        setCreating(false);
      }
    },
    [createForm, message, refresh],
  );

  const openEdit = useCallback(
    (record: RateCardRecord) => {
      setEditing(record);
      editForm.setFieldsValue({
        input_cny: mtokMicrosToCny(record.input_per_mtok_micros),
        output_cny: mtokMicrosToCny(record.output_per_mtok_micros),
        cache_creation_cny: mtokMicrosToCny(record.cache_creation_per_mtok_micros),
        cache_read_cny: mtokMicrosToCny(record.cache_read_per_mtok_micros),
      });
    },
    [editForm],
  );

  const handleEdit = useCallback(
    async (values: EditFormValues) => {
      if (editing === null) return;
      setSaving(true);
      try {
        const patch: RateCardPatch = {
          input_per_mtok_micros: cnyToMtokMicros(values.input_cny),
          output_per_mtok_micros: cnyToMtokMicros(values.output_cny),
          cache_creation_per_mtok_micros: cnyToMtokMicros(values.cache_creation_cny),
          cache_read_per_mtok_micros: cnyToMtokMicros(values.cache_read_cny),
        };
        await patchRateCard(editing.id, patch);
        setEditing(null);
        await refresh();
      } catch (err) {
        message.error(errMessage(err));
      } finally {
        setSaving(false);
      }
    },
    [editing, message, refresh],
  );

  const handleDelete = useCallback(
    async (id: string) => {
      try {
        await deleteRateCard(id);
        await refresh();
      } catch (err) {
        message.error(errMessage(err));
      }
    },
    [message, refresh],
  );

  const priceColumn = useCallback(
    (titleKey: string, field: keyof RateCardRecord) => ({
      title: t(titleKey),
      dataIndex: field,
      key: field,
      width: 150,
      render: (v: number) => <Text className="mono">¥{mtokMicrosToCny(v)}</Text>,
    }),
    [t],
  );

  const columns: TableColumnsType<RateCardRecord> = useMemo(
    () => [
      {
        title: t("rate_card_page.col_provider"),
        dataIndex: "provider",
        key: "provider",
        width: 120,
        render: (provider: string) => <Text strong>{provider}</Text>,
      },
      {
        title: t("rate_card_page.col_model"),
        dataIndex: "model",
        key: "model",
        render: (model: string) => <Text className="mono">{model}</Text>,
      },
      priceColumn("rate_card_page.col_input", "input_per_mtok_micros"),
      priceColumn("rate_card_page.col_output", "output_per_mtok_micros"),
      priceColumn("rate_card_page.col_cache_creation", "cache_creation_per_mtok_micros"),
      priceColumn("rate_card_page.col_cache_read", "cache_read_per_mtok_micros"),
      {
        title: "",
        key: "actions",
        width: 140,
        render: (_: unknown, record) => (
          <Space size={6}>
            <Button
              size="small"
              onClick={() => openEdit(record)}
              data-testid={`rc-edit-${record.id}`}
            >
              {t("rate_card_page.edit")}
            </Button>
            <Popconfirm
              title={t("rate_card_page.delete_confirm_title")}
              description={t("rate_card_page.delete_confirm_body")}
              onConfirm={() => void handleDelete(record.id)}
              okText={t("rate_card_page.delete")}
              okButtonProps={{ danger: true }}
            >
              <Button
                size="small"
                danger
                type="text"
                icon={<Trash2 size={13} strokeWidth={1.5} />}
                aria-label={t("rate_card_page.delete")}
                data-testid={`rc-delete-${record.id}`}
              />
            </Popconfirm>
          </Space>
        ),
      },
    ],
    [t, priceColumn, openEdit, handleDelete],
  );

  if (!isSystemAdmin) {
    return (
      <div data-testid="rate-card-root">
        <PageHeader
          icon={<Banknote size={18} strokeWidth={1.5} />}
          title={t("rate_card_page.page_title")}
        />
        <Empty
          description={t("rate_card_page.system_admin_only")}
          style={{ marginTop: 64 }}
          data-testid="rate-card-forbidden"
        />
      </div>
    );
  }

  return (
    <div data-testid="rate-card-root">
      <PageHeader
        icon={<Banknote size={18} strokeWidth={1.5} />}
        title={t("rate_card_page.page_title")}
        subtitle={
          <Text type="secondary" style={{ fontSize: 12 }}>
            {t("rate_card_page.subtitle")}
          </Text>
        }
        actions={
          <Space size={10}>
            <Input
              placeholder={t("rate_card_page.filter_provider")}
              aria-label={t("rate_card_page.filter_provider")}
              value={providerFilter}
              onChange={(e) => setProviderFilter(e.target.value)}
              style={{ width: 140 }}
              size="small"
              allowClear
              data-testid="rc-filter-provider"
            />
            <Input
              placeholder={t("rate_card_page.filter_model")}
              aria-label={t("rate_card_page.filter_model")}
              value={modelFilter}
              onChange={(e) => setModelFilter(e.target.value)}
              style={{ width: 160 }}
              size="small"
              allowClear
              data-testid="rc-filter-model"
            />
            <Button
              type="primary"
              size="small"
              icon={<Plus size={14} strokeWidth={1.5} />}
              onClick={() => setCreateOpen(true)}
              data-testid="rc-create-open"
            >
              {t("rate_card_page.create")}
            </Button>
          </Space>
        }
      />

      {error !== null && (
        <Alert
          type="error"
          showIcon
          message={t("rate_card_page.failed_to_load")}
          description={error}
          style={{ marginBottom: 16 }}
          data-testid="rate-card-error"
        />
      )}

      <Table<RateCardRecord>
        size="small"
        columns={columns}
        dataSource={items}
        rowKey="id"
        loading={loading}
        pagination={false}
        locale={{ emptyText: <Empty description={t("rate_card_page.empty")} /> }}
        data-testid="rate-card-table"
      />

      <Modal
        title={t("rate_card_page.create")}
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => createForm.submit()}
        confirmLoading={creating}
        okText={t("common.save")}
        data-testid="rc-create-modal"
      >
        <Form<CreateFormValues> form={createForm} layout="vertical" onFinish={handleCreate}>
          <Form.Item
            name="provider"
            label={t("rate_card_page.field_provider")}
            rules={[{ required: true }]}
          >
            <Select
              options={providerOptions}
              placeholder={t("rate_card_page.field_provider")}
              aria-label={t("rate_card_page.field_provider")}
              onChange={() => createForm.setFieldsValue({ model: undefined })}
              data-testid="rc-create-provider"
            />
          </Form.Item>
          <Form.Item noStyle shouldUpdate>
            {({ getFieldValue }) => {
              const provider: string | undefined = getFieldValue("provider");
              const models = provider ? (modelsByProvider.get(provider) ?? []) : [];
              return (
                <Form.Item
                  name="model"
                  label={t("rate_card_page.field_model")}
                  rules={[{ required: true }]}
                >
                  <Select
                    options={models.map((m) => ({ value: m, label: m }))}
                    placeholder={t("rate_card_page.field_model")}
                    aria-label={t("rate_card_page.field_model")}
                    disabled={!provider}
                    data-testid="rc-create-model"
                  />
                </Form.Item>
              );
            }}
          </Form.Item>
          <CnyField name="input_cny" label={t("rate_card_page.field_input")} required />
          <CnyField name="output_cny" label={t("rate_card_page.field_output")} required />
          <CnyField name="cache_creation_cny" label={t("rate_card_page.field_cache_creation")} />
          <CnyField name="cache_read_cny" label={t("rate_card_page.field_cache_read")} />
        </Form>
      </Modal>

      <Drawer
        title={
          editing !== null
            ? t("rate_card_page.edit_title", {
                provider: editing.provider,
                model: editing.model,
              })
            : ""
        }
        open={editing !== null}
        onClose={() => setEditing(null)}
        width={480}
        extra={
          <Button type="primary" loading={saving} onClick={() => editForm.submit()}>
            {t("common.save")}
          </Button>
        }
        data-testid="rc-edit-drawer"
      >
        {editing !== null && (
          <>
            <Alert
              type="info"
              showIcon
              message={t("rate_card_page.identity_immutable")}
              style={{ marginBottom: 16 }}
              data-testid="rc-identity-note"
            />
            <dl
              style={{
                display: "grid",
                gridTemplateColumns: "140px 1fr",
                rowGap: 6,
                fontSize: 13,
                marginBottom: 16,
              }}
            >
              <dt style={{ color: "var(--ew-text-tertiary)" }}>
                {t("rate_card_page.field_provider")}
              </dt>
              <dd style={{ margin: 0 }}>{editing.provider}</dd>
              <dt style={{ color: "var(--ew-text-tertiary)" }}>
                {t("rate_card_page.field_model")}
              </dt>
              <dd style={{ margin: 0 }} className="mono">
                {editing.model}
              </dd>
            </dl>
            <Form<EditFormValues> form={editForm} layout="vertical" onFinish={handleEdit}>
              <CnyField name="input_cny" label={t("rate_card_page.field_input")} required />
              <CnyField name="output_cny" label={t("rate_card_page.field_output")} required />
              <CnyField
                name="cache_creation_cny"
                label={t("rate_card_page.field_cache_creation")}
              />
              <CnyField name="cache_read_cny" label={t("rate_card_page.field_cache_read")} />
            </Form>
          </>
        )}
      </Drawer>
    </div>
  );
}
