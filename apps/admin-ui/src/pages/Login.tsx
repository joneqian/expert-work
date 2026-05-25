/**
 * Login page — Stream H.1b PR 1 + PR 2a (i18n) + PR 2b (OIDC).
 *
 * Two sign-in surfaces, the right one chosen at build time:
 *
 *   - **OIDC code-flow** is preferred when ``VITE_OIDC_ISSUER`` is
 *     configured. The primary CTA hands off to the IdP via
 *     :func:`signIn` and the user comes back through
 *     :ref:`AuthCallback`.
 *   - **Token-paste** stays available always — for developers, for
 *     operators using API keys, and as the only option when no IdP
 *     is configured. When OIDC IS configured the form is folded
 *     behind a "Developer login" disclosure.
 */
import { useState } from "react";
import { Alert, Button, Card, Divider, Form, Input, Typography } from "antd";
import { LogIn } from "lucide-react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { useAuth } from "../auth/AuthContext";
import { isOidcConfigured, signIn } from "../auth/oidc";

const { Title, Paragraph, Text } = Typography;

interface LoginLocationState {
  from?: string;
}

export function Login() {
  const { t } = useTranslation();
  const { status, login } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [submitError, setSubmitError] = useState<string | null>(null);
  const oidcAvailable = isOidcConfigured();
  // When OIDC is the primary path, default to a collapsed token form
  // so the SSO CTA isn't competing with a textarea for attention.
  const [showDevLogin, setShowDevLogin] = useState(!oidcAvailable);

  if (status === "authenticated") {
    const from = (location.state as LoginLocationState | null)?.from ?? "/agents";
    return <Navigate to={from} replace />;
  }

  const returnPath = (location.state as LoginLocationState | null)?.from ?? "/agents";

  const onOidcSignIn = async () => {
    setSubmitError(null);
    try {
      await signIn(returnPath);
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : String(err));
    }
  };

  const onTokenSubmit = (values: { token: string }) => {
    const trimmed = values.token.trim();
    if (!trimmed) {
      setSubmitError(t("login.token_empty"));
      return;
    }
    setSubmitError(null);
    login(trimmed);
    navigate(returnPath, { replace: true });
  };

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 24,
        background: "var(--hx-surface-base)",
      }}
    >
      <Card
        style={{ width: 480, maxWidth: "100%" }}
        styles={{ body: { padding: 32 } }}
        data-testid="login-card"
      >
        <Title level={3} style={{ marginTop: 0 }}>
          {t("login.title")}
        </Title>
        <Paragraph type="secondary" style={{ marginBottom: 24 }}>
          {t("login.paragraph")}
        </Paragraph>

        {submitError && (
          <Alert
            type="error"
            showIcon
            message={submitError}
            style={{ marginBottom: 16 }}
            data-testid="login-error"
          />
        )}

        {oidcAvailable && (
          <>
            <Button
              type="primary"
              block
              size="large"
              onClick={() => {
                void onOidcSignIn();
              }}
              icon={<LogIn size={16} strokeWidth={1.5} />}
              data-testid="login-sso"
            >
              {t("login.sign_in_sso")}
            </Button>
            <Paragraph
              type="secondary"
              style={{ marginTop: 8, marginBottom: 0, fontSize: 12 }}
            >
              {t("login.sso_help")}
            </Paragraph>
            <Divider style={{ margin: "20px 0" }} />
            <Button
              type="link"
              size="small"
              onClick={() => setShowDevLogin((v) => !v)}
              data-testid="login-dev-toggle"
              style={{ padding: 0, marginBottom: showDevLogin ? 12 : 0 }}
            >
              {showDevLogin ? t("login.dev_login_hide") : t("login.dev_login_toggle")}
            </Button>
          </>
        )}

        {showDevLogin && (
          <Form
            layout="vertical"
            onFinish={onTokenSubmit}
            data-testid="login-dev-form"
          >
            {oidcAvailable && (
              <Text
                type="secondary"
                style={{ fontSize: 12, display: "block", marginBottom: 8 }}
              >
                {t("login.dev_login_section")}
              </Text>
            )}
            <Form.Item
              name="token"
              label={t("login.token_label")}
              rules={[{ required: true, message: t("login.token_required") }]}
            >
              <Input.TextArea
                rows={4}
                placeholder={t("login.token_placeholder")}
                autoComplete="off"
                spellCheck={false}
                data-testid="login-token"
                style={{ fontFamily: "var(--hx-font-mono, ui-monospace)" }}
              />
            </Form.Item>
            <Button
              type={oidcAvailable ? "default" : "primary"}
              htmlType="submit"
              block
              data-testid="login-submit"
            >
              {t("common.sign_in")}
            </Button>
          </Form>
        )}

        {!oidcAvailable && (
          <Paragraph type="secondary" style={{ marginTop: 24, marginBottom: 0 }}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {t("login.pr2_hint")}&nbsp;
              <code>docs/dev/oidc-keycloak.md</code>.
            </Text>
          </Paragraph>
        )}
      </Card>
    </div>
  );
}
