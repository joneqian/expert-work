/**
 * Create / Edit MCP Server drawer — Stream V-F.
 *
 * Opened from ``/settings/mcp-servers``.  In create mode it submits
 * ``POST /v1/mcp-servers``; in edit mode it submits
 * ``PATCH /v1/mcp-servers/{name}`` (token omitted if blank to preserve the
 * existing secret).  A "Test connection" button validates the relevant form
 * fields then calls ``POST /v1/mcp-servers/test`` — nothing is persisted.
 *
 * Mirrors the structure of ``CreateTenantDrawer`` (Drawer 520 px,
 * ``Form.useForm``, ``layout="vertical"``, footer Cancel / Submit,
 * ``ApiError`` → ``${err.code}: ${err.message}`` message, reset-on-close).
 */
import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  App,
  Button,
  Drawer,
  Form,
  Input,
  InputNumber,
  Select,
  Space,
  Spin,
} from "antd";
import { useTranslation } from "react-i18next";

import {
  createMcpServer,
  testMcpConnection,
  updateMcpServer,
  type McpAuthType,
  type McpServer,
  type McpTransport,
} from "../api/mcp-servers";
import { ApiError } from "../api/client";

// ── Types ──────────────────────────────────────────────────────────────────

interface McpServerForm {
  name: string;
  transport: McpTransport;
  url: string;
  auth_type: McpAuthType;
  token?: string;
  timeout_s: number;
}

export interface CreateMcpServerDrawerProps {
  open: boolean;
  onClose: () => void;
  /** Fires after a successful create/update so the parent can refresh. */
  onSaved: () => void;
  /** When supplied the drawer opens in edit mode and pre-fills the fields. */
  editing?: McpServer | null;
}

// ── Constants ──────────────────────────────────────────────────────────────

const TRANSPORT_OPTIONS: { value: McpTransport; label: string }[] = [
  { value: "sse", label: "SSE" },
  { value: "streamable_http", label: "Streamable HTTP" },
];

const AUTH_OPTIONS: { value: McpAuthType; label: string }[] = [
  { value: "none", label: "None" },
  { value: "bearer", label: "Bearer token" },
];

const NAME_PATTERN = /^[a-z0-9][a-z0-9_-]{0,63}$/;
const URL_PATTERN = /^https?:\/\/.+/;

// ── Component ──────────────────────────────────────────────────────────────

export function CreateMcpServerDrawer({
  open,
  onClose,
  onSaved,
  editing = null,
}: CreateMcpServerDrawerProps) {
  const { t } = useTranslation();
  const { message } = App.useApp();

  const [form] = Form.useForm<McpServerForm>();
  const [submitting, setSubmitting] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<
    { ok: true; count: number } | { ok: false; msg: string } | null
  >(null);

  // Controlled auth_type so the token field can show/hide reactively.
  const [authType, setAuthType] = useState<McpAuthType>("none");

  // ── Reset ──────────────────────────────────────────────────────────────

  const reset = useCallback(() => {
    form.resetFields();
    setTestResult(null);
    setAuthType("none");
  }, [form]);

  useEffect(() => {
    if (!open) {
      reset();
      return;
    }
    // Pre-fill when editing.
    if (editing) {
      form.setFieldsValue({
        name: editing.name,
        transport: editing.transport,
        url: editing.url,
        auth_type: editing.auth_type,
        timeout_s: editing.timeout_s,
        // token always blank in edit mode — user must re-enter to rotate.
      });
      setAuthType(editing.auth_type);
    } else {
      form.setFieldsValue({ transport: "sse", auth_type: "none", timeout_s: 30 });
      setAuthType("none");
    }
  }, [open, editing, form, reset]);

  // ── Handlers ───────────────────────────────────────────────────────────

  const handleCancel = useCallback(() => {
    reset();
    onClose();
  }, [reset, onClose]);

  const handleTestConnection = useCallback(async () => {
    // Validate the fields relevant to the connection test.
    let values: McpServerForm;
    try {
      values = await form.validateFields(["transport", "url", "auth_type", "token", "timeout_s"]);
    } catch {
      return;
    }
    setTesting(true);
    setTestResult(null);
    try {
      const body: Parameters<typeof testMcpConnection>[0] = {
        transport: values.transport,
        url: values.url,
        auth_type: values.auth_type,
        timeout_s: values.timeout_s,
      };
      if (values.auth_type === "bearer" && values.token) {
        body.token = values.token;
      }
      const result = await testMcpConnection(body);
      setTestResult({ ok: true, count: result.tool_count });
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? `${err.code}: ${err.message}`
          : err instanceof Error
            ? err.message
            : "unknown error";
      setTestResult({ ok: false, msg });
    } finally {
      setTesting(false);
    }
  }, [form]);

  const handleSubmit = useCallback(async () => {
    let values: McpServerForm;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    setSubmitting(true);
    try {
      if (editing) {
        // PATCH — omit token if blank (preserve existing secret).
        const body: Parameters<typeof updateMcpServer>[1] = {
          url: values.url,
          timeout_s: values.timeout_s,
          enabled: editing.enabled,
        };
        const trimmedToken = values.token?.trim();
        if (trimmedToken) {
          body.token = trimmedToken;
        }
        await updateMcpServer(editing.name, body);
      } else {
        const body: Parameters<typeof createMcpServer>[0] = {
          name: values.name,
          transport: values.transport,
          url: values.url,
          auth_type: values.auth_type,
          timeout_s: values.timeout_s,
        };
        if (values.auth_type === "bearer" && values.token) {
          body.token = values.token;
        }
        await createMcpServer(body);
      }
      onSaved();
      reset();
      onClose();
    } catch (err) {
      // Custom registration may be disabled platform-wide (Stream W).
      if (err instanceof ApiError && err.code === "MCP_CUSTOM_DISABLED") {
        message.error(t("create_mcp_server.custom_disabled"));
        return;
      }
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
  }, [form, editing, message, onSaved, onClose, reset, t]);

  // ── Render ─────────────────────────────────────────────────────────────

  const isEditing = editing !== null && editing !== undefined;

  return (
    <Drawer
      open={open}
      onClose={handleCancel}
      title={isEditing ? t("create_mcp_server.edit_title") : t("create_mcp_server.add_title")}
      width={520}
      destroyOnHidden
      footer={
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
          <Button onClick={handleCancel} disabled={submitting} data-testid="cms-cancel">
            {t("common.cancel")}
          </Button>
          <Button
            type="primary"
            loading={submitting}
            onClick={handleSubmit}
            data-testid="cms-submit"
          >
            {isEditing ? t("create_mcp_server.submit_save") : t("create_mcp_server.submit_add")}
          </Button>
        </div>
      }
    >
      <Form
        form={form}
        layout="vertical"
        initialValues={{ transport: "sse", auth_type: "none", timeout_s: 30 }}
        data-testid="cms-form"
      >
        {/* Name */}
        <Form.Item
          name="name"
          label={t("create_mcp_server.field_name")}
          rules={[
            { required: true, message: t("create_mcp_server.name_required") },
            {
              pattern: NAME_PATTERN,
              message: t("create_mcp_server.name_required"),
            },
          ]}
        >
          <Input
            data-testid="cms-name"
            disabled={isEditing}
            maxLength={64}
            placeholder="my-mcp-server"
          />
        </Form.Item>

        {/* Transport */}
        <Form.Item name="transport" label={t("create_mcp_server.field_transport")}>
          <Select<McpTransport>
            data-testid="cms-transport"
            options={TRANSPORT_OPTIONS}
            disabled={isEditing}
          />
        </Form.Item>

        {/* URL */}
        <Form.Item
          name="url"
          label={t("create_mcp_server.field_url")}
          rules={[
            { required: true, message: t("create_mcp_server.url_required") },
            {
              validator: (_rule, value: string | undefined) => {
                const v = (value ?? "").trim();
                if (v === "" || URL_PATTERN.test(v)) return Promise.resolve();
                return Promise.reject(new Error(t("create_mcp_server.url_invalid")));
              },
            },
          ]}
        >
          <Input
            data-testid="cms-url"
            placeholder="https://mcp.example.com/mcp"
            maxLength={2048}
          />
        </Form.Item>

        {/* Auth type */}
        <Form.Item name="auth_type" label={t("create_mcp_server.field_auth")}>
          <Select<McpAuthType>
            data-testid="cms-auth"
            options={AUTH_OPTIONS}
            onChange={(val) => {
              setAuthType(val);
              setTestResult(null);
            }}
          />
        </Form.Item>

        {/* Token — shown only when auth_type=bearer */}
        {authType === "bearer" && (
          <Form.Item
            name="token"
            label={t("create_mcp_server.field_token")}
            extra={
              isEditing
                ? t("create_mcp_server.token_hint_edit")
                : t("create_mcp_server.token_hint_create")
            }
            rules={
              isEditing
                ? []
                : [{ required: true, message: t("create_mcp_server.token_required") }]
            }
          >
            <Input.Password data-testid="cms-token" maxLength={4096} />
          </Form.Item>
        )}

        {/* Timeout */}
        <Form.Item name="timeout_s" label={t("create_mcp_server.field_timeout")}>
          <InputNumber
            data-testid="cms-timeout"
            min={1}
            max={300}
            step={1}
            style={{ width: "100%" }}
          />
        </Form.Item>

        {/* Test connection */}
        <Form.Item>
          <Space direction="vertical" style={{ width: "100%" }}>
            <Button
              onClick={handleTestConnection}
              disabled={testing || submitting}
              data-testid="cms-test"
              icon={testing ? <Spin size="small" /> : undefined}
            >
              {t("create_mcp_server.test_connection")}
            </Button>

            {testResult !== null && (
              <div data-testid="cms-test-result">
                {testResult.ok ? (
                  <Alert
                    type="success"
                    showIcon
                    message={t("create_mcp_server.test_ok", { count: testResult.count })}
                  />
                ) : (
                  <Alert
                    type="error"
                    showIcon
                    message={t("create_mcp_server.test_failed")}
                    description={testResult.msg}
                  />
                )}
              </div>
            )}
          </Space>
        </Form.Item>
      </Form>
    </Drawer>
  );
}
