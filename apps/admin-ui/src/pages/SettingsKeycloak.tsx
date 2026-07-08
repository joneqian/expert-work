/**
 * Keycloak hub — platform-operator entry to the self-hosted Keycloak admin
 * console (IAM).
 *
 * ``system_admin`` ONLY. Keycloak manages every tenant's users, so it holds
 * cross-tenant identity data with no per-tenant isolation — it lives in the
 * platform nav group and self-guards on ``isSystemAdmin`` (mirrors the
 * Observability hub's rationale).
 *
 * The card external-links to ``${VITE_KEYCLOAK_BASE_URL}/admin/`` — the admin
 * console landing, from which an operator picks the ``expert-work`` realm →
 * Users → Credentials to set a member / first-admin password. An unset base
 * URL shows a "configure" hint naming the env var rather than a dead link.
 */
import { Alert, Button, Card, Tag, Typography } from "antd";
import { ExternalLink, KeyRound } from "lucide-react";
import { useTranslation } from "react-i18next";

import { PageHeader } from "../components/PageHeader";
import { useAuth } from "../auth/AuthContext";
import { readKeycloakBaseUrl } from "../config/env";

const { Text, Paragraph } = Typography;

const KEYCLOAK_ENV = "VITE_KEYCLOAK_BASE_URL";

export function SettingsKeycloak() {
  const { t } = useTranslation();
  const { identity } = useAuth();
  const isSystemAdmin = identity?.isSystemAdmin ?? false;

  const base = readKeycloakBaseUrl();
  const consoleUrl = base === undefined ? undefined : `${base}/admin/`;

  return (
    <div>
      <PageHeader
        icon={<KeyRound size={18} strokeWidth={1.5} />}
        title={t("keycloak_page.page_title")}
        subtitle={t("keycloak_page.subtitle")}
      />

      {!isSystemAdmin ? (
        <Alert
          type="warning"
          showIcon
          message={t("keycloak_page.not_admin_title")}
          description={t("keycloak_page.not_admin_body")}
          data-testid="kc-not-admin"
        />
      ) : (
        <Card size="small" data-testid="kc-card">
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              gap: 16,
            }}
          >
            <div>
              <Text strong>{t("keycloak_page.console_name")}</Text>
              <Paragraph
                type="secondary"
                style={{ margin: "4px 0 0", fontSize: 12 }}
              >
                {t("keycloak_page.console_desc")}
              </Paragraph>
            </div>
            {consoleUrl ? (
              <a
                href={consoleUrl}
                target="_blank"
                rel="noreferrer noopener"
                data-testid="kc-open"
              >
                <Button
                  type="primary"
                  icon={<ExternalLink size={13} strokeWidth={1.5} />}
                >
                  {t("keycloak_page.open")}
                </Button>
              </a>
            ) : (
              <Tag data-testid="kc-unconfigured">
                {t("keycloak_page.unconfigured", { env: KEYCLOAK_ENV })}
              </Tag>
            )}
          </div>
        </Card>
      )}
    </div>
  );
}
