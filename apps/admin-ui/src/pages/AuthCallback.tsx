/**
 * OIDC callback page — Stream H.1b PR 2b.
 *
 * Mounted at ``/auth/callback``. After the IdP redirects the user back
 * here with the authorization code, :func:`handleCallback` exchanges
 * it for an id_token (via PKCE) and we feed it into :ref:`AuthContext`
 * just like a paste-login. The ``returnPath`` carried through ``state``
 * routes the user back to wherever they were heading before login.
 */
import { useEffect, useState } from "react";
import { Alert, Card, Spin, Typography } from "antd";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { useAuth } from "../auth/AuthContext";
import { handleCallback } from "../auth/oidc";

const { Title, Paragraph } = Typography;

export function AuthCallback() {
  const { t } = useTranslation();
  const { login } = useAuth();
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    handleCallback()
      .then((result) => {
        if (cancelled) return;
        login(result.idToken);
        navigate(result.returnPath, { replace: true });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [login, navigate]);

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
        styles={{ body: { padding: 32, textAlign: "center" } }}
        data-testid="auth-callback-card"
      >
        <Title level={4} style={{ marginTop: 0 }}>
          {t("auth_callback.title")}
        </Title>
        {error === null ? (
          <>
            <Spin size="large" />
            <Paragraph type="secondary" style={{ marginTop: 16, marginBottom: 0 }}>
              {t("auth_callback.exchanging")}
            </Paragraph>
          </>
        ) : (
          <Alert
            type="error"
            showIcon
            message={t("auth_callback.failed")}
            description={error}
            data-testid="auth-callback-error"
          />
        )}
      </Card>
    </div>
  );
}
