/**
 * Instantiate-from-catalog form — Stream W (tenant admin).
 *
 * Renders one input per ``auth_schema`` field of the selected catalog entry
 * (``kind:"param"`` → text, ``kind:"secret"`` → password), plus an optional
 * instance-name override and a timeout. On submit it calls
 * ``instantiateCatalogEntry`` and surfaces the backend error codes with
 * friendly inline messages.
 */
import { useCallback, useEffect, useState } from "react";
import { App, Button, Form, Input, InputNumber } from "antd";
import { useTranslation } from "react-i18next";

import {
  instantiateCatalogEntry,
  type InstantiateBody,
  type TenantCatalogEntry,
} from "../../api/mcp-catalog";
import type { McpServer } from "../../api/mcp-servers";
import { ApiError } from "../../api/client";

export interface InstantiateCatalogFormProps {
  entry: TenantCatalogEntry;
  onCreated: (server: McpServer) => void;
  onBack: () => void;
}

/** Maps a backend instantiate error code → an i18n key. */
const ERROR_CODE_KEYS: Record<string, string> = {
  MCP_CATALOG_TIER_REQUIRED: "mcp_catalog.err_tier_required",
  MCP_CATALOG_FIELD_MISSING: "mcp_catalog.err_field_missing",
  MCP_CATALOG_FIELD_UNKNOWN: "mcp_catalog.err_field_unknown",
  MCP_CATALOG_PARAM_INVALID: "mcp_catalog.err_param_invalid",
  MCP_CATALOG_URL_TEMPLATE: "mcp_catalog.err_url_template",
  MCP_SERVER_INVALID_URL: "mcp_catalog.err_invalid_url",
  MCP_SERVER_DUPLICATE: "mcp_catalog.err_duplicate",
  MCP_CATALOG_NOT_FOUND: "mcp_catalog.err_not_found",
};

export function InstantiateCatalogForm({
  entry,
  onCreated,
  onBack,
}: InstantiateCatalogFormProps) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [form] = Form.useForm<Record<string, string | number | undefined>>();
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    form.resetFields();
    form.setFieldsValue({ timeout_s: 30 });
  }, [entry.id, form]);

  const handleSubmit = useCallback(async () => {
    let values: Record<string, string | number | undefined>;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    const params: Record<string, string> = {};
    const secrets: Record<string, string> = {};
    for (const field of entry.auth_schema?.fields ?? []) {
      const raw = values[`field_${field.key}`];
      const val = typeof raw === "string" ? raw : raw === undefined ? "" : String(raw);
      if (field.kind === "secret") {
        if (val) secrets[field.key] = val;
      } else {
        // Omit empty optional params so we never substitute "" into the URL
        // template for an unfilled field.
        if (val) params[field.key] = val;
      }
    }
    const nameOverride = typeof values.instance_name === "string" ? values.instance_name.trim() : "";
    const body: InstantiateBody = { params, secrets };
    if (nameOverride) body.name = nameOverride;
    if (typeof values.timeout_s === "number") body.timeout_s = values.timeout_s;

    setSubmitting(true);
    try {
      const server = await instantiateCatalogEntry(entry.id, body);
      onCreated(server);
    } catch (err) {
      if (err instanceof ApiError) {
        const key = ERROR_CODE_KEYS[err.code];
        message.error(key ? t(key) : `${err.code}: ${err.message}`);
      } else {
        message.error(err instanceof Error ? err.message : "unknown error");
      }
    } finally {
      setSubmitting(false);
    }
  }, [form, entry, message, onCreated, t]);

  const fields = entry.auth_schema?.fields ?? [];

  return (
    <div data-testid="icf-root">
      <Form form={form} layout="vertical" data-testid="icf-form">
        <Form.Item
          name="instance_name"
          label={t("mcp_catalog.instance_name")}
          extra={t("mcp_catalog.instance_name_hint")}
        >
          <Input data-testid="icf-name" maxLength={64} placeholder={entry.name} />
        </Form.Item>

        {fields.map((field) => (
          <Form.Item
            key={field.key}
            name={`field_${field.key}`}
            label={field.label || field.key}
            rules={
              field.required
                ? [{ required: true, message: t("mcp_catalog.field_value_required", { label: field.label || field.key }) }]
                : []
            }
          >
            {field.kind === "secret" ? (
              <Input.Password
                data-testid={`icf-field-${field.key}`}
                maxLength={4096}
                autoComplete="off"
              />
            ) : (
              <Input data-testid={`icf-field-${field.key}`} maxLength={1024} />
            )}
          </Form.Item>
        ))}

        <Form.Item name="timeout_s" label={t("create_mcp_server.field_timeout")}>
          <InputNumber data-testid="icf-timeout" min={1} max={300} step={1} style={{ width: "100%" }} />
        </Form.Item>

        <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
          <Button onClick={onBack} disabled={submitting} data-testid="icf-back">
            {t("mcp_catalog.back")}
          </Button>
          <Button
            type="primary"
            loading={submitting}
            onClick={handleSubmit}
            data-testid="icf-create"
          >
            {t("mcp_catalog.create")}
          </Button>
        </div>
      </Form>
    </div>
  );
}
