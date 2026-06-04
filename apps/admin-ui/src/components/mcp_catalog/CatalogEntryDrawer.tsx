/**
 * Create / Edit catalog connector drawer — Stream W (system_admin).
 *
 * Opened from ``/settings/mcp-catalog``. Create submits
 * ``POST /v1/platform/mcp-catalog``; edit submits
 * ``PATCH /v1/platform/mcp-catalog/{id}`` with only the mutable subset
 * (``name`` and ``transport`` are immutable and disabled in edit mode).
 *
 * Mirrors ``CreateMcpServerDrawer`` (Drawer 560 px, ``Form.useForm``,
 * ``layout="vertical"``, footer Cancel / Submit, ``ApiError`` →
 * ``${err.code}: ${err.message}`` message, reset-on-close) and embeds the
 * ``AuthSchemaBuilder`` for the field list.
 */
import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  App,
  Button,
  Drawer,
  Form,
  Input,
  Select,
  Switch,
} from "antd";
import { useTranslation } from "react-i18next";

import {
  createPlatformCatalogEntry,
  updatePlatformCatalogEntry,
  type CatalogPatchBody,
  type CatalogUpsertBody,
  type McpCatalogEntry,
  type McpRequiredTier,
} from "../../api/mcp-catalog";
import type { McpAuthType, McpTransport } from "../../api/mcp-servers";
import { ApiError } from "../../api/client";
import {
  AuthSchemaBuilder,
  stripAuthFieldUids,
  type AuthSchemaBuilderField,
} from "./AuthSchemaBuilder";
import { validateAuthSchemaSecrets } from "./validation";

interface CatalogEntryForm {
  name: string;
  display_name: string;
  description?: string;
  category?: string;
  icon?: string;
  transport: McpTransport;
  url_template: string;
  auth_type: McpAuthType;
  required_tier: McpRequiredTier;
  enabled: boolean;
}

export interface CatalogEntryDrawerProps {
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
  editing?: McpCatalogEntry | null;
}

const TRANSPORT_OPTIONS: { value: McpTransport; label: string }[] = [
  { value: "sse", label: "SSE" },
  { value: "streamable_http", label: "Streamable HTTP" },
];

const AUTH_OPTIONS: { value: McpAuthType; labelKey: string }[] = [
  { value: "none", labelKey: "mcp_catalog.auth_none" },
  { value: "bearer", labelKey: "mcp_catalog.auth_bearer" },
];

const TIER_OPTIONS: { value: McpRequiredTier; labelKey: string }[] = [
  { value: "free", labelKey: "mcp_catalog.tier_free" },
  { value: "pro", labelKey: "mcp_catalog.tier_pro" },
  { value: "enterprise", labelKey: "mcp_catalog.tier_enterprise" },
];

const NAME_PATTERN = /^[a-z0-9][a-z0-9_-]{0,63}$/;

export function CatalogEntryDrawer({
  open,
  onClose,
  onSaved,
  editing = null,
}: CatalogEntryDrawerProps) {
  const { t } = useTranslation();
  const { message } = App.useApp();

  const [form] = Form.useForm<CatalogEntryForm>();
  const [submitting, setSubmitting] = useState(false);
  const [fields, setFields] = useState<AuthSchemaBuilderField[]>([]);
  const [guardError, setGuardError] = useState<string | null>(null);

  const isEditing = editing !== null && editing !== undefined;

  const reset = useCallback(() => {
    form.resetFields();
    setFields([]);
    setGuardError(null);
  }, [form]);

  useEffect(() => {
    if (!open) {
      reset();
      return;
    }
    if (editing) {
      form.setFieldsValue({
        name: editing.name,
        display_name: editing.display_name,
        description: editing.description,
        category: editing.category,
        icon: editing.icon,
        transport: editing.transport,
        url_template: editing.url_template,
        auth_type: editing.auth_type,
        required_tier: editing.required_tier,
        enabled: editing.enabled,
      });
      setFields(editing.auth_schema?.fields ?? []);
    } else {
      form.setFieldsValue({
        transport: "sse",
        auth_type: "none",
        required_tier: "free",
        enabled: true,
      });
      setFields([]);
    }
    setGuardError(null);
  }, [open, editing, form, reset]);

  const handleCancel = useCallback(() => {
    reset();
    onClose();
  }, [reset, onClose]);

  const handleSubmit = useCallback(async () => {
    let values: CatalogEntryForm;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    // Client-side guard mirrors the backend bearer/none secret rule.
    // auth_type is immutable post-create, so in edit mode the effective
    // value is the existing entry's auth_type (the Select is disabled).
    const effectiveAuthType = editing ? editing.auth_type : values.auth_type;
    const guard = validateAuthSchemaSecrets(effectiveAuthType, fields);
    if (guard !== null) {
      setGuardError(t(guard));
      return;
    }
    setGuardError(null);

    // Strip the internal-only ``_uid`` React key — the backend
    // ``McpConnectorAuthField`` is ``extra="forbid"`` and would 422 on it.
    const cleanFields = stripAuthFieldUids(fields);

    setSubmitting(true);
    try {
      if (editing) {
        const body: CatalogPatchBody = {
          display_name: values.display_name,
          description: values.description ?? "",
          category: values.category ?? "",
          icon: values.icon ?? "",
          url_template: values.url_template,
          auth_schema: { fields: cleanFields },
          required_tier: values.required_tier,
          enabled: values.enabled,
        };
        await updatePlatformCatalogEntry(editing.id, body);
      } else {
        const body: CatalogUpsertBody = {
          name: values.name,
          display_name: values.display_name,
          description: values.description ?? "",
          category: values.category ?? "",
          icon: values.icon ?? "",
          transport: values.transport,
          url_template: values.url_template,
          auth_type: values.auth_type,
          auth_schema: { fields: cleanFields },
          required_tier: values.required_tier,
          enabled: values.enabled,
        };
        await createPlatformCatalogEntry(body);
      }
      onSaved();
      reset();
      onClose();
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? `${err.code}: ${err.message}`
          : err instanceof Error
            ? err.message
            : "unknown error";
      message.error(msg);
    } finally {
      setSubmitting(false);
    }
  }, [form, fields, editing, message, onSaved, onClose, reset, t]);

  return (
    <Drawer
      open={open}
      onClose={handleCancel}
      title={isEditing ? t("mcp_catalog.edit_title") : t("mcp_catalog.add_title")}
      width={560}
      destroyOnHidden
      footer={
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
          <Button onClick={handleCancel} disabled={submitting} data-testid="cce-cancel">
            {t("common.cancel")}
          </Button>
          <Button
            type="primary"
            loading={submitting}
            onClick={handleSubmit}
            data-testid="cce-submit"
          >
            {isEditing ? t("mcp_catalog.submit_save") : t("mcp_catalog.submit_add")}
          </Button>
        </div>
      }
    >
      <Form form={form} layout="vertical" data-testid="cce-form">
        <Form.Item
          name="name"
          label={t("mcp_catalog.field_name")}
          extra={t("mcp_catalog.field_name_hint")}
          rules={[
            { required: true, message: t("mcp_catalog.name_required") },
            { pattern: NAME_PATTERN, message: t("mcp_catalog.name_required") },
          ]}
        >
          <Input data-testid="cce-name" disabled={isEditing} maxLength={64} placeholder="github" />
        </Form.Item>

        <Form.Item
          name="display_name"
          label={t("mcp_catalog.field_display_name")}
          rules={[{ required: true, message: t("mcp_catalog.display_name_required") }]}
        >
          <Input data-testid="cce-display-name" maxLength={128} placeholder="GitHub" />
        </Form.Item>

        <Form.Item name="description" label={t("mcp_catalog.field_description")}>
          <Input.TextArea data-testid="cce-description" maxLength={512} rows={2} />
        </Form.Item>

        <Form.Item name="category" label={t("mcp_catalog.field_category")}>
          <Input data-testid="cce-category" maxLength={64} placeholder="dev-tools" />
        </Form.Item>

        <Form.Item name="icon" label={t("mcp_catalog.field_icon")}>
          <Input data-testid="cce-icon" maxLength={256} placeholder="https://… or emoji" />
        </Form.Item>

        <Form.Item name="transport" label={t("mcp_catalog.field_transport")}>
          <Select<McpTransport>
            data-testid="cce-transport"
            options={TRANSPORT_OPTIONS}
            disabled={isEditing}
          />
        </Form.Item>

        <Form.Item
          name="url_template"
          label={t("mcp_catalog.field_url_template")}
          extra={t("mcp_catalog.url_template_hint")}
          rules={[{ required: true, message: t("mcp_catalog.url_template_required") }]}
        >
          <Input
            data-testid="cce-url-template"
            maxLength={2048}
            placeholder="https://mcp.example.com/{workspace}/sse"
          />
        </Form.Item>

        <Form.Item name="auth_type" label={t("mcp_catalog.field_auth")}>
          <Select<McpAuthType>
            data-testid="cce-auth"
            options={AUTH_OPTIONS.map((o) => ({ value: o.value, label: t(o.labelKey) }))}
            disabled={isEditing}
            onChange={() => setGuardError(null)}
          />
        </Form.Item>

        <Form.Item name="required_tier" label={t("mcp_catalog.field_required_tier")}>
          <Select<McpRequiredTier>
            data-testid="cce-tier"
            options={TIER_OPTIONS.map((o) => ({ value: o.value, label: t(o.labelKey) }))}
          />
        </Form.Item>

        <Form.Item label={t("mcp_catalog.field_auth_schema")} extra={t("mcp_catalog.auth_schema_hint")}>
          <AuthSchemaBuilder value={fields} onChange={setFields} />
        </Form.Item>

        {guardError !== null && (
          <Alert
            type="error"
            showIcon
            data-testid="cce-guard-error"
            message={guardError}
            style={{ marginBottom: 16 }}
          />
        )}

        <Form.Item name="enabled" label={t("mcp_catalog.field_enabled")} valuePropName="checked">
          <Switch aria-label={t("mcp_catalog.field_enabled")} data-testid="cce-enabled" />
        </Form.Item>
      </Form>
    </Drawer>
  );
}
