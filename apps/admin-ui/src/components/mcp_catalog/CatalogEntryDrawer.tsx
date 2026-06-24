/**
 * Create / Edit platform MCP server drawer — Stream MCP platform-servers (P3).
 *
 * A platform admin configures a fully usable **shared** MCP server (the
 * platform-server model replaced the old template + auth_schema flow):
 *
 * - **None** — a public, unauthenticated server.
 * - **Bearer (shared)** — the platform supplies one token; all enabling
 *   tenants share that identity (only for tools without per-user data
 *   isolation — a warning says so). The token is write-only: blank on edit
 *   keeps the stored one.
 * - **OAuth (per-user)** — the platform registers an OAuth app (client id +
 *   scopes); each user authorizes their own account.
 *
 * Create → ``POST /v1/platform/mcp-catalog``; edit → ``PATCH .../{id}`` with
 * the mutable subset (``name`` / ``transport`` / ``auth_type`` immutable).
 * Tabs: 基本 / 认证 / 高级 (mirrors ``CreateMcpServerDrawer``).
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
  Tabs,
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
  { value: "bearer", labelKey: "mcp_catalog.auth_bearer_shared" },
  { value: "oauth2", labelKey: "mcp_catalog.auth_oauth2" },
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
  const authType = Form.useWatch("auth_type", form);

  const isEditing = editing !== null && editing !== undefined;
  const effectiveAuth: McpAuthType = isEditing
    ? editing.auth_type
    : (authType ?? "none");

  const reset = useCallback(() => {
    form.resetFields();
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
        oauth_client_id: editing.oauth_client_id ?? undefined,
        oauth_scopes: editing.oauth_scopes ?? undefined,
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
    setSubmitting(true);
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
        // Write-only: only send the token when the admin typed a new one.
        if (editing.auth_type === "bearer" && values.bearer_token) {
          body.bearer_token = values.bearer_token;
        }
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
        if (values.auth_type === "bearer") {
          body.bearer_token = values.bearer_token;
        }
        if (values.auth_type === "oauth2") {
          body.oauth_client_id = values.oauth_client_id;
          body.oauth_scopes = values.oauth_scopes ?? "";
        }
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
  }, [form, editing, message, onSaved, onClose, reset]);

  const basicTab = (
    <>
      <Form.Item
        name="name"
        label={t("mcp_catalog.field_name")}
        extra={t("mcp_catalog.field_name_hint")}
        rules={[
          { required: true, message: t("mcp_catalog.name_required") },
          { pattern: NAME_PATTERN, message: t("mcp_catalog.name_required") },
        ]}
      >
        <Input
          data-testid="cce-name"
          disabled={isEditing}
          maxLength={64}
          placeholder="github"
        />
      </Form.Item>
      <Form.Item
        name="display_name"
        label={t("mcp_catalog.field_display_name")}
        rules={[
          { required: true, message: t("mcp_catalog.display_name_required") },
        ]}
      >
        <Input
          data-testid="cce-display-name"
          maxLength={128}
          placeholder="GitHub"
        />
      </Form.Item>
      <Form.Item name="description" label={t("mcp_catalog.field_description")}>
        <Input.TextArea
          data-testid="cce-description"
          maxLength={512}
          rows={2}
        />
      </Form.Item>
      <Form.Item name="category" label={t("mcp_catalog.field_category")}>
        <Input
          data-testid="cce-category"
          maxLength={64}
          placeholder="dev-tools"
        />
      </Form.Item>
      <Form.Item name="icon" label={t("mcp_catalog.field_icon")}>
        <Input
          data-testid="cce-icon"
          maxLength={256}
          placeholder="https://… or emoji"
        />
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
        rules={[
          { required: true, message: t("mcp_catalog.url_template_required") },
        ]}
      >
        <Input
          data-testid="cce-url-template"
          maxLength={2048}
          placeholder="https://mcp.example.com/mcp"
        />
      </Form.Item>
    </>
  );

  const authTab = (
    <>
      <Form.Item name="auth_type" label={t("mcp_catalog.field_auth")}>
        <Select<McpAuthType>
          data-testid="cce-auth"
          aria-label={t("mcp_catalog.field_auth")}
          options={AUTH_OPTIONS.map((o) => ({
            value: o.value,
            label: t(o.labelKey),
          }))}
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
            extra={
              isEditing ? t("mcp_catalog.bearer_token_keep_hint") : undefined
            }
            rules={
              isEditing
                ? []
                : [
                    {
                      required: true,
                      message: t("mcp_catalog.bearer_token_required"),
                    },
                  ]
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
            rules={[
              {
                required: true,
                message: t("mcp_catalog.oauth_client_id_required"),
              },
            ]}
          >
            <Input
              data-testid="cce-oauth-client-id"
              maxLength={256}
              disabled={isEditing}
            />
          </Form.Item>
          <Form.Item
            name="oauth_scopes"
            label={t("mcp_catalog.field_oauth_scopes")}
          >
            <Input
              data-testid="cce-oauth-scopes"
              maxLength={512}
              disabled={isEditing}
              placeholder="read write"
            />
          </Form.Item>
        </>
      )}
    </>
  );

  const advancedTab = (
    <>
      <Form.Item
        name="required_tier"
        label={t("mcp_catalog.field_required_tier")}
      >
        <Select<McpRequiredTier>
          data-testid="cce-tier"
          aria-label={t("mcp_catalog.field_required_tier")}
          options={TIER_OPTIONS.map((o) => ({
            value: o.value,
            label: t(o.labelKey),
          }))}
        />
      </Form.Item>
      <Form.Item
        name="enabled"
        label={t("mcp_catalog.field_enabled")}
        valuePropName="checked"
      >
        <Switch
          aria-label={t("mcp_catalog.field_enabled")}
          data-testid="cce-enabled"
        />
      </Form.Item>
    </>
  );

  return (
    <Drawer
      open={open}
      onClose={handleCancel}
      title={
        isEditing ? t("mcp_catalog.edit_title") : t("mcp_catalog.add_title")
      }
      width={560}
      destroyOnHidden
      footer={
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
          <Button
            onClick={handleCancel}
            disabled={submitting}
            data-testid="cce-cancel"
          >
            {t("common.cancel")}
          </Button>
          <Button
            type="primary"
            loading={submitting}
            onClick={handleSubmit}
            data-testid="cce-submit"
          >
            {isEditing
              ? t("mcp_catalog.submit_save")
              : t("mcp_catalog.submit_add")}
          </Button>
        </div>
      }
    >
      <Form form={form} layout="vertical" data-testid="cce-form">
        <Tabs
          defaultActiveKey="basic"
          items={[
            {
              key: "basic",
              label: t("mcp_catalog.tab_basic"),
              children: basicTab,
              forceRender: true,
            },
            {
              key: "auth",
              label: t("mcp_catalog.tab_auth"),
              children: authTab,
              forceRender: true,
            },
            {
              key: "advanced",
              label: t("mcp_catalog.tab_advanced"),
              children: advancedTab,
              forceRender: true,
            },
          ]}
        />
      </Form>
    </Drawer>
  );
}
