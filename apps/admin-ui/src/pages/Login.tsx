/**
 * Login page — Stream H.1b PR 1.
 *
 * M0 surface: an operator pastes a JWT (OIDC) or a helix API key and
 * we persist it to localStorage. PR 2 of H.1b replaces this with a
 * proper OIDC code flow (helix doesn't own user auth — it federates
 * to the tenant's OIDC provider).
 *
 * The form is intentionally bare:
 *   - one textarea (token bodies are long; single-line fields truncate
 *     visually + tempt copy-paste mistakes)
 *   - inline help linking to ``docs/auth.md`` (placeholder until that
 *     doc lands)
 *   - no remember-me checkbox (token already persists across reloads)
 */
import { useState } from "react";
import { Alert, Button, Card, Form, Input, Typography } from "antd";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";

const { Title, Paragraph, Text } = Typography;

interface LoginLocationState {
  from?: string;
}

export function Login() {
  const { status, login } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [submitError, setSubmitError] = useState<string | null>(null);

  if (status === "authenticated") {
    const from = (location.state as LoginLocationState | null)?.from ?? "/agents";
    return <Navigate to={from} replace />;
  }

  const onFinish = (values: { token: string }) => {
    const trimmed = values.token.trim();
    if (!trimmed) {
      setSubmitError("token cannot be empty");
      return;
    }
    setSubmitError(null);
    login(trimmed);
    const from = (location.state as LoginLocationState | null)?.from ?? "/agents";
    navigate(from, { replace: true });
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
          helix Admin
        </Title>
        <Paragraph type="secondary" style={{ marginBottom: 24 }}>
          Paste your OIDC JWT or helix API key to sign in. Both are stored in
          this browser only; the control-plane re-verifies on every request.
        </Paragraph>

        {submitError && (
          <Alert
            type="error"
            showIcon
            message={submitError}
            style={{ marginBottom: 16 }}
          />
        )}

        <Form layout="vertical" onFinish={onFinish}>
          <Form.Item
            name="token"
            label="Token"
            rules={[{ required: true, message: "token is required" }]}
          >
            <Input.TextArea
              rows={4}
              placeholder="eyJ… (JWT)   or   aforge_pat_… (helix API key)"
              autoComplete="off"
              spellCheck={false}
              data-testid="login-token"
              style={{ fontFamily: "var(--hx-font-mono, ui-monospace)" }}
            />
          </Form.Item>
          <Button
            type="primary"
            htmlType="submit"
            block
            data-testid="login-submit"
          >
            Sign in
          </Button>
        </Form>

        <Paragraph type="secondary" style={{ marginTop: 24, marginBottom: 0 }}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            OIDC code-flow login lands in H.1b PR&nbsp;2 — see&nbsp;
            <code>docs/streams/STREAM-H-DESIGN.md</code>.
          </Text>
        </Paragraph>
      </Card>
    </div>
  );
}
