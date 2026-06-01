/**
 * Settings — Create Tenant page (Stream P, Mini-ADR P-1/P-2).
 *
 * Provisions a new tenant via ``POST /v1/tenants``. Platform-level action —
 * only system_admins see the form (mirrors backend gate in
 * ``api/tenants.py``: ``is_system_admin`` → 403 otherwise). On success the new
 * ``tenant_id`` is surfaced prominently so the operator can copy it into the
 * bootstrap / per-tenant config flow.
 */
import { useCallback, useState } from "react";
import {
  Alert,
  App,
  Breadcrumb,
  Button,
  Form,
  Input,
  Select,
  Typography,
} from "antd";
import { Building2, ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";

import { createTenant, type CreateTenantBody, type FirstAdminSummary } from "../api/tenants";
import type { TenantPlan } from "../api/tenant_config";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";

const { Text } = Typography;

const PLAN_OPTIONS: TenantPlan[] = ["free", "pro", "enterprise"];

interface CreateTenantForm {
  display_name: string;
  plan: TenantPlan;
  tenant_id?: string;
  first_admin_email?: string;
  first_admin_display_name?: string;
}

export function SettingsCreateTenant() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const auth = useAuth();
  const isSystemAdmin = auth.identity?.isSystemAdmin ?? false;

  const [form] = Form.useForm<CreateTenantForm>();
  const [submitting, setSubmitting] = useState(false);
  const [createdId, setCreatedId] = useState<string | null>(null);
  const [firstAdmin, setFirstAdmin] = useState<FirstAdminSummary | null>(null);

  const onCreate = useCallback(async () => {
    const values = await form.validateFields();
    const body: CreateTenantBody = {
      display_name: values.display_name,
      plan: values.plan,
    };
    const tid = values.tenant_id?.trim();
    if (tid) {
      body.tenant_id = tid;
    }
    const adminEmail = values.first_admin_email?.trim();
    if (adminEmail) {
      body.first_admin_email = adminEmail;
      const adminName = values.first_admin_display_name?.trim();
      if (adminName) {
        body.first_admin_display_name = adminName;
      }
    }
    setSubmitting(true);
    try {
      const record = await createTenant(body);
      setCreatedId(record.tenant_id);
      setFirstAdmin(record.first_admin ?? null);
      message.success(t("settings_create_tenant.created"));
      form.resetFields();
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
  }, [form, message, t]);

  return (
    <div data-testid="ct-root">
      <div className="hx-page-header">
        <Breadcrumb
          separator={<ChevronRight size={12} strokeWidth={1.5} />}
          items={[{ title: t("common.home") }, { title: t("settings_create_tenant.page_title") }]}
        />
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 8, marginBottom: 16 }}>
          <Building2 size={20} strokeWidth={1.5} />
          <h1 style={{ margin: 0 }}>{t("settings_create_tenant.page_title")}</h1>
        </div>
        <p style={{ color: "var(--hx-text-secondary)", fontSize: 13, margin: "0 0 12px" }}>
          {t("settings_create_tenant.subtitle")}
        </p>
      </div>

      {!isSystemAdmin ? (
        <Alert
          type="warning"
          showIcon
          message={t("settings_create_tenant.not_admin_title")}
          description={t("settings_create_tenant.not_admin_body")}
          data-testid="ct-not-admin"
        />
      ) : (
        <div style={{ maxWidth: 520 }}>
          {createdId !== null && (
            <Alert
              type="success"
              showIcon
              style={{ marginBottom: 16 }}
              message={t("settings_create_tenant.created")}
              description={
                <span>
                  {t("settings_create_tenant.created_detail")}{" "}
                  <Text code copyable data-testid="ct-created-id">
                    {createdId}
                  </Text>
                  {firstAdmin !== null && (
                    <div style={{ marginTop: 8 }} data-testid="ct-first-admin">
                      {t("settings_create_tenant.first_admin_provisioned")}{" "}
                      <Text code>{firstAdmin.email}</Text> ({firstAdmin.status})
                    </div>
                  )}
                </span>
              }
              data-testid="ct-created"
            />
          )}
          <Form
            form={form}
            layout="vertical"
            initialValues={{ plan: "free" }}
            data-testid="ct-form"
          >
            <Form.Item
              name="display_name"
              label={t("settings_create_tenant.field_display_name")}
              rules={[{ required: true, message: t("settings_create_tenant.display_name_required") }]}
            >
              <Input data-testid="ct-display-name" maxLength={128} />
            </Form.Item>
            <Form.Item name="plan" label={t("settings_create_tenant.field_plan")}>
              <Select<TenantPlan>
                data-testid="ct-plan"
                options={PLAN_OPTIONS.map((p) => ({ value: p, label: p }))}
              />
            </Form.Item>
            <Form.Item
              name="tenant_id"
              label={t("settings_create_tenant.field_tenant_id")}
              extra={t("settings_create_tenant.tenant_id_hint")}
            >
              <Input data-testid="ct-tenant-id" placeholder={t("settings_create_tenant.tenant_id_placeholder")} />
            </Form.Item>
            <Form.Item
              name="first_admin_email"
              label={t("settings_create_tenant.field_first_admin_email")}
              extra={t("settings_create_tenant.first_admin_hint")}
              rules={[{ type: "email", message: t("settings_create_tenant.first_admin_email_invalid") }]}
            >
              <Input data-testid="ct-first-admin-email" maxLength={320} />
            </Form.Item>
            <Form.Item
              name="first_admin_display_name"
              label={t("settings_create_tenant.field_first_admin_display_name")}
            >
              <Input data-testid="ct-first-admin-name" maxLength={128} />
            </Form.Item>
            <Button
              type="primary"
              loading={submitting}
              onClick={onCreate}
              data-testid="ct-submit"
            >
              {t("settings_create_tenant.create_btn")}
            </Button>
          </Form>
        </div>
      )}
    </div>
  );
}
