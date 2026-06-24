/**
 * MCP OAuth callback page — Stream MCP-OAUTH.
 *
 * The configured ``mcp_oauth_redirect_uri`` points here. The provider redirects
 * the browser back with ``?state&code`` (a plain navigation, no auth header).
 * This page — rendered INSIDE the authenticated shell — forwards ``state`` +
 * ``code`` to ``GET /v1/mcp-oauth/callback`` (carrying the user's bearer token)
 * to finish the token exchange, then routes to "My MCP connections".
 *
 * Follows the {@link AuthCallback} pattern: a ``cancelled`` flag guards against
 * unmount, and the exchange runs exactly once even under React 18 StrictMode
 * double-mount (the authorization code is single-use).
 */
import { useEffect, useRef, useState } from "react";
import { Alert, Button, Card, Spin, Typography } from "antd";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { forwardOAuthCallback } from "../api/mcp-oauth";
import { ApiError } from "../api/client";

const { Title, Paragraph } = Typography;

const CONNECTIONS_PATH = "/settings/mcp-oauth";

export function McpOAuthCallback() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return; // single-fire (StrictMode double-mount guard)
    started.current = true;

    const state = params.get("state");
    const code = params.get("code");
    let cancelled = false;

    if (!state || !code) {
      setError(t("mcp_oauth.callback_missing_params"));
      return;
    }

    forwardOAuthCallback(state, code)
      .then(() => {
        if (cancelled) return;
        navigate(CONNECTIONS_PATH, { replace: true });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(
          err instanceof ApiError ? `${err.code}: ${err.message}` : String(err),
        );
      });

    return () => {
      cancelled = true;
    };
  }, [params, navigate, t]);

  return (
    <div
      style={{
        display: "flex",
        justifyContent: "center",
        padding: "64px 24px",
      }}
    >
      <Card
        style={{ width: 480, maxWidth: "100%" }}
        data-testid="mo-callback-card"
      >
        <Title level={4} style={{ marginTop: 0 }}>
          {t("mcp_oauth.callback_title")}
        </Title>
        {error === null ? (
          <div style={{ textAlign: "center", padding: "16px 0" }}>
            <Spin />
            <Paragraph type="secondary" style={{ marginTop: 16 }}>
              {t("mcp_oauth.callback_exchanging")}
            </Paragraph>
          </div>
        ) : (
          <>
            <Alert
              type="error"
              showIcon
              message={t("mcp_oauth.callback_failed")}
              description={error}
            />
            <Button
              type="primary"
              style={{ marginTop: 16 }}
              onClick={() => navigate(CONNECTIONS_PATH, { replace: true })}
              data-testid="mo-callback-back"
            >
              {t("mcp_oauth.callback_back")}
            </Button>
          </>
        )}
      </Card>
    </div>
  );
}
