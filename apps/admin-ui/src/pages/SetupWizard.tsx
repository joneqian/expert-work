/**
 * First-run setup wizard — platform bootstrap.
 *
 * Mounted at ``/setup`` *outside* :ref:`ProtectedRoute`: nobody can log
 * in until the first platform system_admin exists, so the wizard must
 * be reachable while anonymous. :ref:`SetupGate` redirects an
 * un-initialized platform here automatically; a manual visit when the
 * platform is already initialized bounces back to ``/``.
 *
 * Visual language is aligned with :ref:`Login` (dark brand surface,
 * centered card). On success we swap the form for a confirmation card
 * whose CTA hands off to the normal sign-in flow (OIDC redirect when
 * configured, otherwise ``/login``).
 */
import { useState } from "react";
import { Alert, Button, Card, Form, Input, Result, Typography } from "antd";
import { Rocket } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { App } from "antd";

import { runSetup, type RunSetupResult } from "../api/setup";
import { ApiError } from "../api/client";
import { isOidcConfigured, signIn } from "../auth/oidc";

const { Title, Paragraph, Text } = Typography;

const PASSWORD_MIN = 8;
const PASSWORD_MAX = 256;

interface SetupFormValues {
  platform_tenant_display_name: string;
  admin_email: string;
  admin_password: string;
  admin_password_confirm: string;
  admin_display_name?: string;
  setup_token: string;
}

export function SetupWizard() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const navigate = useNavigate();
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<RunSetupResult | null>(null);
  const [alreadyInitialized, setAlreadyInitialized] = useState(false);

  const goToLogin = () => {
    if (isOidcConfigured()) {
      void signIn("/agents").catch(() => navigate("/login", { replace: true }));
    } else {
      navigate("/login", { replace: true });
    }
  };

  const onFinish = async (values: SetupFormValues) => {
    setSubmitting(true);
    try {
      const created = await runSetup(
        {
          admin_email: values.admin_email.trim(),
          admin_password: values.admin_password,
          admin_display_name: values.admin_display_name?.trim() || undefined,
          platform_tenant_display_name:
            values.platform_tenant_display_name?.trim() || undefined,
        },
        values.setup_token.trim(),
      );
      setResult(created);
      message.success(t("setup.success_toast"));
    } catch (err) {
      const code = err instanceof ApiError ? err.code : "UNKNOWN";
      if (code === "ALREADY_INITIALIZED") {
        setAlreadyInitialized(true);
        message.warning(t("setup.error_already_initialized"));
      } else {
        message.error(setupErrorMessage(t, code, err));
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Shell>
      {result !== null ? (
        <Result
          status="success"
          title={t("setup.done_title")}
          subTitle={t("setup.done_subtitle")}
          extra={
            <Button
              type="primary"
              onClick={goToLogin}
              data-testid="setup-go-login"
            >
              {t("setup.go_to_login")}
            </Button>
          }
        />
      ) : alreadyInitialized ? (
        <Result
          status="info"
          title={t("setup.already_initialized_title")}
          subTitle={t("setup.already_initialized_subtitle")}
          extra={
            <Button
              type="primary"
              onClick={goToLogin}
              data-testid="setup-already-login"
            >
              {t("setup.go_to_login")}
            </Button>
          }
        />
      ) : (
        <>
          <Title level={3} style={{ marginTop: 0 }}>
            {t("setup.title")}
          </Title>
          <Paragraph type="secondary" style={{ marginBottom: 24 }}>
            {t("setup.paragraph")}
          </Paragraph>

          <Form<SetupFormValues>
            layout="vertical"
            requiredMark="optional"
            onFinish={(values) => {
              void onFinish(values);
            }}
            initialValues={{ platform_tenant_display_name: "Platform" }}
            data-testid="setup-form"
          >
            <Form.Item
              name="platform_tenant_display_name"
              label={t("setup.platform_name_label")}
            >
              <Input data-testid="setup-platform-name" />
            </Form.Item>

            <Form.Item
              name="admin_email"
              label={t("setup.admin_email_label")}
              rules={[
                { required: true, message: t("setup.admin_email_required") },
                { type: "email", message: t("setup.admin_email_invalid") },
              ]}
            >
              <Input
                type="email"
                autoComplete="username"
                data-testid="setup-admin-email"
              />
            </Form.Item>

            <Form.Item
              name="admin_password"
              label={t("setup.admin_password_label")}
              rules={[
                { required: true, message: t("setup.admin_password_required") },
                {
                  min: PASSWORD_MIN,
                  max: PASSWORD_MAX,
                  message: t("setup.admin_password_length"),
                },
              ]}
            >
              <Input.Password
                autoComplete="new-password"
                data-testid="setup-admin-password"
              />
            </Form.Item>

            <Form.Item
              name="admin_password_confirm"
              label={t("setup.admin_password_confirm_label")}
              dependencies={["admin_password"]}
              rules={[
                {
                  required: true,
                  message: t("setup.admin_password_confirm_required"),
                },
                ({ getFieldValue }) => ({
                  validator(_, value) {
                    if (!value || getFieldValue("admin_password") === value) {
                      return Promise.resolve();
                    }
                    return Promise.reject(
                      new Error(t("setup.admin_password_mismatch")),
                    );
                  },
                }),
              ]}
            >
              <Input.Password
                autoComplete="new-password"
                data-testid="setup-admin-password-confirm"
              />
            </Form.Item>

            <Form.Item
              name="admin_display_name"
              label={t("setup.admin_display_name_label")}
            >
              <Input data-testid="setup-admin-display-name" />
            </Form.Item>

            <Form.Item
              name="setup_token"
              label={t("setup.setup_token_label")}
              extra={
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {t("setup.setup_token_hint")}
                </Text>
              }
              rules={[
                { required: true, message: t("setup.setup_token_required") },
              ]}
            >
              <Input.Password
                autoComplete="off"
                data-testid="setup-token"
                style={{ fontFamily: "var(--ew-font-mono, ui-monospace)" }}
              />
            </Form.Item>

            <Alert
              type="info"
              showIcon
              message={t("setup.notice")}
              style={{ marginBottom: 16 }}
            />

            <Button
              type="primary"
              htmlType="submit"
              block
              size="large"
              loading={submitting}
              icon={<Rocket size={16} strokeWidth={1.5} />}
              data-testid="setup-submit"
            >
              {t("setup.submit")}
            </Button>
          </Form>
        </>
      )}
    </Shell>
  );
}

/** Map a backend ``detail.code`` to a localized, user-facing message. */
function setupErrorMessage(
  t: (key: string) => string,
  code: string,
  err: unknown,
): string {
  switch (code) {
    case "INVALID_SETUP_TOKEN":
      return t("setup.error_invalid_token");
    case "SETUP_NOT_CONFIGURED":
      return t("setup.error_not_configured");
    case "ADMIN_EMAIL_EXISTS":
      return t("setup.error_email_exists");
    case "KEYCLOAK_UNAVAILABLE":
      return t("setup.error_keycloak_unavailable");
    default:
      return err instanceof ApiError && err.message
        ? err.message
        : t("setup.error_generic");
  }
}

/** Centered dark-brand frame shared by the form + result states — keeps
 *  the wizard visually aligned with :ref:`Login`. */
function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 24,
        background: "var(--ew-surface-base)",
      }}
    >
      <Card
        style={{ width: 480, maxWidth: "100%" }}
        styles={{ body: { padding: 32 } }}
        data-testid="setup-card"
      >
        {children}
      </Card>
    </div>
  );
}
