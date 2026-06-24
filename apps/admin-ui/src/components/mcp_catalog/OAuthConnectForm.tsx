/**
 * OAuth connect panel — Stream MCP-OAUTH (tenant member).
 *
 * Shown instead of {@link InstantiateCatalogForm} when the selected catalog
 * connector is ``oauth2``. The user authorizes with their **own** account: the
 * "Authorize" button calls ``initiateMcpOAuth`` and navigates the browser to the
 * provider's authorize URL. After the provider redirects back to the admin-ui
 * callback page, the connection appears under "My MCP connections".
 */
import { useCallback, useState } from "react";
import { Alert, App, Button, Typography } from "antd";
import { ExternalLink } from "lucide-react";
import { useTranslation } from "react-i18next";

import { initiateMcpOAuth } from "../../api/mcp-oauth";
import type { TenantCatalogEntry } from "../../api/mcp-catalog";
import { ApiError } from "../../api/client";

const { Paragraph } = Typography;

export interface OAuthConnectFormProps {
  entry: TenantCatalogEntry;
  onBack: () => void;
}

/** Maps a backend initiate error code → an i18n key. */
const ERROR_CODE_KEYS: Record<string, string> = {
  MCP_CATALOG_TIER_REQUIRED: "mcp_catalog.err_tier_required",
  MCP_CATALOG_NOT_OAUTH: "mcp_oauth.err_not_oauth",
  MCP_CATALOG_NOT_FOUND: "mcp_catalog.err_not_found",
  MCP_OAUTH_NOT_CONFIGURED: "mcp_oauth.err_not_configured",
};

export function OAuthConnectForm({ entry, onBack }: OAuthConnectFormProps) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [authorizing, setAuthorizing] = useState(false);

  const handleAuthorize = useCallback(async () => {
    setAuthorizing(true);
    try {
      const result = await initiateMcpOAuth(entry.id);
      // Full-page navigation to the provider's consent screen.
      window.location.assign(result.authorize_url);
    } catch (err) {
      setAuthorizing(false);
      if (err instanceof ApiError) {
        const key = ERROR_CODE_KEYS[err.code];
        message.error(key ? t(key) : `${err.code}: ${err.message}`);
      } else {
        message.error(err instanceof Error ? err.message : "unknown error");
      }
    }
  }, [entry.id, message, t]);

  return (
    <div data-testid="ocf-root">
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message={t("mcp_oauth.connect_title", { name: entry.display_name })}
        description={
          <Paragraph type="secondary" style={{ marginBottom: 0 }}>
            {t("mcp_oauth.connect_hint")}
          </Paragraph>
        }
      />
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          gap: 8,
          marginTop: 16,
        }}
      >
        <Button onClick={onBack} disabled={authorizing} data-testid="ocf-back">
          {t("mcp_catalog.back")}
        </Button>
        <Button
          type="primary"
          loading={authorizing}
          onClick={handleAuthorize}
          icon={<ExternalLink size={15} strokeWidth={1.6} />}
          data-testid="ocf-authorize"
        >
          {t("mcp_oauth.authorize")}
        </Button>
      </div>
    </div>
  );
}
