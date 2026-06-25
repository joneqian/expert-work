/**
 * Platform MCP server config form — Stream MCP platform-servers.
 *
 * The shared 基本 / 认证 / 高级 tabbed form behind both the **create Modal**
 * (``CatalogCreateModal``) and the **edit page** (``McpCatalogDetail``). It owns
 * the antd Form, the icon upload, and the create/patch submit (connect-probe
 * errors surface inline). The parent renders its own submit affordance and
 * triggers it through the imperative ``submit()`` handle.
 *
 * The per-tool enable/disable list lives on its own page tab
 * (``CatalogToolsTab``), not here.
 */
import { forwardRef, useCallback, useEffect, useImperativeHandle } from "react";
import {
  Alert,
  App,
  Button,
  Form,
  Input,
  InputNumber,
  Select,
  Space,
  Switch,
  Tabs,
  Upload,
} from "antd";
import { ImagePlus, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  createPlatformCatalogEntry,
  MCP_CATEGORIES,
  updatePlatformCatalogEntry,
  type CatalogPatchBody,
  type CatalogUpsertBody,
  type McpCatalogEntry,
  type McpRequiredTier,
} from "../../api/mcp-catalog";
import type { McpAuthType, McpTransport } from "../../api/mcp-servers";
import { ApiError } from "../../api/client";

interface CatalogEntryForm {
  name: string;
  display_name: string;
  description?: string;
  category?: string;
  icon?: string;
  transport: McpTransport;
  url_template: string;
  auth_type: McpAuthType;
  bearer_token?: string;
  oauth_client_id?: string;
  oauth_scopes?: string;
  timeout_s?: number;
  sse_read_timeout_s?: number;
  required_tier: McpRequiredTier;
  enabled: boolean;
}

export interface CatalogConfigFormHandle {
  submit: () => Promise<void>;
}

export interface CatalogConfigFormProps {
  editing: McpCatalogEntry | null;
  onSaved: () => void;
  onSubmittingChange?: (submitting: boolean) => void;
}

const TRANSPORT_OPTIONS: { value: McpTransport; label: string }[] = [
  { value: "sse", label: "SSE" },
  { value: "streamable_http", label: "Streamable HTTP" },
];

const AUTH_OPTIONS: { value: McpAuthType; labelKey: string }[] = [
  { value: "none", labelKey: "mcp_catalog.auth_none" },
  { value: "bearer", labelKey: "mcp_catalog.auth_bearer_shared" },
  { value: "oauth2", labelKey: "mcp_catalog.auth_oauth2" },
];

const TIER_OPTIONS: { value: McpRequiredTier; labelKey: string }[] = [
  { value: "free", labelKey: "mcp_catalog.tier_free" },
  { value: "pro", labelKey: "mcp_catalog.tier_pro" },
  { value: "enterprise", labelKey: "mcp_catalog.tier_enterprise" },
];

const NAME_PATTERN = /^[a-z0-9][a-z0-9_-]{0,63}$/;
const ICON_MAX_BYTES = 32 * 1024;

export const CatalogConfigForm = forwardRef<
  CatalogConfigFormHandle,
  CatalogConfigFormProps
>(function CatalogConfigForm({ editing, onSaved, onSubmittingChange }, ref) {
  const { t } = useTranslation();
  const { message } = App.useApp();

  const [form] = Form.useForm<CatalogEntryForm>();
  const authType = Form.useWatch("auth_type", form);
  const iconValue = Form.useWatch("icon", form);

  const isEditing = editing !== null && editing !== undefined;
  const effectiveAuth: McpAuthType = isEditing
    ? editing.auth_type
    : (authType ?? "none");

  useEffect(() => {
    if (editing) {
      form.setFieldsValue({
        name: editing.name,
        display_name: editing.display_name,
        description: editing.description,
        category: editing.category,
        icon: editing.icon ?? undefined,
        transport: editing.transport,
        url_template: editing.url_template,
        auth_type: editing.auth_type,
        oauth_client_id: editing.oauth_client_id ?? undefined,
        oauth_scopes: editing.oauth_scopes ?? undefined,
        timeout_s: editing.timeout_s ?? undefined,
        sse_read_timeout_s: editing.sse_read_timeout_s ?? undefined,
        required_tier: editing.required_tier,
        enabled: editing.enabled,
      });
    } else {
      form.setFieldsValue({
        transport: "sse",
        auth_type: "none",
        required_tier: "free",
        enabled: true,
      });
    }
  }, [editing, form]);

  const handleIconSelect = useCallback(
    (file: File): boolean => {
      if (!file.type.startsWith("image/")) {
        message.error(t("mcp_catalog.icon_type_error"));
        return false;
      }
      if (file.size > ICON_MAX_BYTES) {
        message.error(t("mcp_catalog.icon_too_large"));
        return false;
      }
      const reader = new FileReader();
      reader.onload = () => form.setFieldValue("icon", reader.result as string);
      reader.readAsDataURL(file);
      return false;
    },
    [form, message, t],
  );

  const submit = useCallback(async () => {
    let values: CatalogEntryForm;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    onSubmittingChange?.(true);
    try {
      if (editing) {
        const body: CatalogPatchBody = {
          display_name: values.display_name,
          description: values.description ?? "",
          category: values.category ?? "",
          icon: values.icon ?? "",
          url_template: values.url_template,
          required_tier: values.required_tier,
          enabled: values.enabled,
        };
        if (typeof values.timeout_s === "number") body.timeout_s = values.timeout_s;
        if (typeof values.sse_read_timeout_s === "number")
          body.sse_read_timeout_s = values.sse_read_timeout_s;
        if (editing.auth_type === "bearer" && values.bearer_token)
          body.bearer_token = values.bearer_token;
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
          required_tier: values.required_tier,
          enabled: values.enabled,
        };
        if (typeof values.timeout_s === "number") body.timeout_s = values.timeout_s;
        if (typeof values.sse_read_timeout_s === "number")
          body.sse_read_timeout_s = values.sse_read_timeout_s;
        if (values.auth_type === "bearer") body.bearer_token = values.bearer_token;
        if (values.auth_type === "oauth2") {
          body.oauth_client_id = values.oauth_client_id;
          body.oauth_scopes = values.oauth_scopes ?? "";
        }
        await createPlatformCatalogEntry(body);
      }
      onSaved();
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? `${err.code}: ${err.message}`
          : err instanceof Error
            ? err.message
            : "unknown error";
      message.error(msg);
    } finally {
      onSubmittingChange?.(false);
    }
  }, [form, editing, message, onSaved, onSubmittingChange]);

  useImperativeHandle(ref, () => ({ submit }), [submit]);

  const basicTab = (
    <>
      <Form.Item
        name="name"
        label={t("mcp_catalog.field_identifier")}
        extra={t("mcp_catalog.field_name_hint")}
        rules={[
          { required: true, message: t("mcp_catalog.name_required") },
          { pattern: NAME_PATTERN, message: t("mcp_catalog.name_required") },
        ]}
      >
        <Input data-testid="cce-name" disabled={isEditing} maxLength={64} placeholder="github-prod" />
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
        <Select
          data-testid="cce-category"
          aria-label={t("mcp_catalog.field_category")}
          allowClear
          placeholder={t("mcp_catalog.category_placeholder")}
          options={MCP_CATEGORIES.map((o) => ({ value: o.value, label: t(o.labelKey) }))}
        />
      </Form.Item>
      <Form.Item label={t("mcp_catalog.field_icon")} extra={t("mcp_catalog.icon_hint")}>
        <Space align="center">
          {iconValue && (
            <img
              src={iconValue}
              alt=""
              width={32}
              height={32}
              style={{ borderRadius: 6, objectFit: "cover" }}
              data-testid="cce-icon-preview"
            />
          )}
          <Upload accept="image/*" showUploadList={false} maxCount={1} beforeUpload={handleIconSelect}>
            <Button icon={<ImagePlus size={14} strokeWidth={1.6} />} data-testid="cce-icon-upload">
              {t("mcp_catalog.icon_upload")}
            </Button>
          </Upload>
          {iconValue && (
            <Button
              type="text"
              danger
              icon={<Trash2 size={14} strokeWidth={1.6} />}
              onClick={() => form.setFieldValue("icon", undefined)}
              data-testid="cce-icon-clear"
              aria-label={t("mcp_catalog.icon_clear")}
            />
          )}
        </Space>
      </Form.Item>
      <Form.Item name="icon" hidden>
        <Input />
      </Form.Item>
      <Form.Item name="transport" label={t("mcp_catalog.field_transport")}>
        <Select<McpTransport>
          data-testid="cce-transport"
          aria-label={t("mcp_catalog.field_transport")}
          options={TRANSPORT_OPTIONS}
          disabled={isEditing}
        />
      </Form.Item>
      <Form.Item
        name="url_template"
        label={t("mcp_catalog.field_url")}
        extra={t("mcp_catalog.url_hint")}
        rules={[{ required: true, message: t("mcp_catalog.url_template_required") }]}
      >
        <Input data-testid="cce-url-template" maxLength={2048} placeholder="https://mcp.example.com/mcp" />
      </Form.Item>
    </>
  );

  const authTab = (
    <>
      <Form.Item name="auth_type" label={t("mcp_catalog.field_auth")}>
        <Select<McpAuthType>
          data-testid="cce-auth"
          aria-label={t("mcp_catalog.field_auth")}
          options={AUTH_OPTIONS.map((o) => ({ value: o.value, label: t(o.labelKey) }))}
          disabled={isEditing}
        />
      </Form.Item>
      {effectiveAuth === "bearer" && (
        <>
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 16 }}
            data-testid="cce-shared-warning"
            message={t("mcp_catalog.shared_bearer_warning")}
          />
          <Form.Item
            name="bearer_token"
            label={t("mcp_catalog.field_bearer_token")}
            extra={isEditing ? t("mcp_catalog.bearer_token_keep_hint") : undefined}
            rules={
              isEditing
                ? []
                : [{ required: true, message: t("mcp_catalog.bearer_token_required") }]
            }
          >
            <Input.Password
              data-testid="cce-bearer-token"
              maxLength={4096}
              autoComplete="off"
              placeholder={isEditing ? "••••••••" : ""}
            />
          </Form.Item>
        </>
      )}
      {effectiveAuth === "oauth2" && (
        <>
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 16 }}
            message={t("mcp_catalog.oauth_hint")}
          />
          <Form.Item
            name="oauth_client_id"
            label={t("mcp_catalog.field_oauth_client_id")}
            rules={[{ required: true, message: t("mcp_catalog.oauth_client_id_required") }]}
          >
            <Input data-testid="cce-oauth-client-id" maxLength={256} disabled={isEditing} />
          </Form.Item>
          <Form.Item name="oauth_scopes" label={t("mcp_catalog.field_oauth_scopes")}>
            <Input data-testid="cce-oauth-scopes" maxLength={512} disabled={isEditing} placeholder="read write" />
          </Form.Item>
        </>
      )}
    </>
  );

  const advancedTab = (
    <>
      <Form.Item name="timeout_s" label={t("mcp_catalog.field_timeout")} extra={t("mcp_catalog.timeout_hint")}>
        <InputNumber
          data-testid="cce-timeout"
          aria-label={t("mcp_catalog.field_timeout")}
          min={1}
          max={300}
          style={{ width: "100%" }}
          placeholder="30"
        />
      </Form.Item>
      <Form.Item
        name="sse_read_timeout_s"
        label={t("mcp_catalog.field_sse_timeout")}
        extra={t("mcp_catalog.sse_timeout_hint")}
      >
        <InputNumber
          data-testid="cce-sse-timeout"
          aria-label={t("mcp_catalog.field_sse_timeout")}
          min={1}
          max={3600}
          style={{ width: "100%" }}
          placeholder="300"
        />
      </Form.Item>
      <Form.Item name="required_tier" label={t("mcp_catalog.field_required_tier")}>
        <Select<McpRequiredTier>
          data-testid="cce-tier"
          aria-label={t("mcp_catalog.field_required_tier")}
          options={TIER_OPTIONS.map((o) => ({ value: o.value, label: t(o.labelKey) }))}
        />
      </Form.Item>
      <Form.Item name="enabled" label={t("mcp_catalog.field_enabled")} valuePropName="checked">
        <Switch aria-label={t("mcp_catalog.field_enabled")} data-testid="cce-enabled" />
      </Form.Item>
    </>
  );

  return (
    <Form form={form} layout="vertical" data-testid="cce-form">
      <Tabs
        defaultActiveKey="basic"
        items={[
          { key: "basic", label: t("mcp_catalog.tab_basic"), children: basicTab, forceRender: true },
          { key: "auth", label: t("mcp_catalog.tab_auth"), children: authTab, forceRender: true },
          { key: "advanced", label: t("mcp_catalog.tab_advanced"), children: advancedTab, forceRender: true },
        ]}
      />
    </Form>
  );
});
