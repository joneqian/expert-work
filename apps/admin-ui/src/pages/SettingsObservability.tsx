/**
 * Observability hub — platform-operator entry to the self-hosted
 * observability stack (Langfuse / Grafana / Tempo).
 *
 * ``system_admin`` ONLY. These dashboards hold platform-wide data with **no
 * per-tenant isolation** — Langfuse's single ClickHouse mixes every tenant's
 * (PII-masked) LLM traces, Grafana/Tempo span all tenants. Exposing them to a
 * tenant user would leak cross-tenant data (ADR-0005 § observability), so this
 * lives in the platform nav group and self-guards on ``isSystemAdmin``. Tenant
 * users get expert_work's own tenant-isolated Run detail instead (token summary +
 * event stream + control actions).
 *
 * Each card external-links to the tool's base URL (build-time env); an unset
 * URL shows a "configure" hint naming the env var rather than a dead link.
 */
import { Alert, Button, Card, Space, Tag, Typography } from "antd";
import { ExternalLink, LineChart } from "lucide-react";
import { useTranslation } from "react-i18next";

import { PageHeader } from "../components/PageHeader";
import { useAuth } from "../auth/AuthContext";
import {
  readGrafanaBaseUrl,
  readLangfuseBaseUrl,
  readTempoBaseUrl,
} from "../config/env";

const { Text, Paragraph } = Typography;

const TOOLS: ReadonlyArray<{ key: string; env: string; getUrl: () => string | undefined }> = [
  { key: "langfuse", env: "VITE_LANGFUSE_BASE_URL", getUrl: readLangfuseBaseUrl },
  { key: "grafana", env: "VITE_GRAFANA_BASE_URL", getUrl: readGrafanaBaseUrl },
  { key: "tempo", env: "VITE_TEMPO_BASE_URL", getUrl: readTempoBaseUrl },
];

export function SettingsObservability() {
  const { t } = useTranslation();
  const { identity } = useAuth();
  const isSystemAdmin = identity?.isSystemAdmin ?? false;

  return (
    <div>
      <PageHeader
        icon={<LineChart size={18} strokeWidth={1.5} />}
        title={t("observability_page.page_title")}
        subtitle={t("observability_page.subtitle")}
      />

      {!isSystemAdmin ? (
        <Alert
          type="warning"
          showIcon
          message={t("observability_page.not_admin_title")}
          description={t("observability_page.not_admin_body")}
          data-testid="obs-not-admin"
        />
      ) : (
        <>
          <Alert
            type="info"
            showIcon
            message={t("observability_page.tenant_isolation_note")}
            style={{ marginBottom: 16 }}
            data-testid="obs-isolation-note"
          />
          <Space direction="vertical" style={{ width: "100%" }} size={12}>
            {TOOLS.map((tool) => {
              const url = tool.getUrl();
              return (
                <Card key={tool.key} size="small" data-testid={`obs-tool-${tool.key}`}>
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      gap: 16,
                    }}
                  >
                    <div>
                      <Text strong>{t(`observability_page.${tool.key}_name`)}</Text>
                      <Paragraph
                        type="secondary"
                        style={{ margin: "4px 0 0", fontSize: 12 }}
                      >
                        {t(`observability_page.${tool.key}_desc`)}
                      </Paragraph>
                    </div>
                    {url ? (
                      <a
                        href={url}
                        target="_blank"
                        rel="noreferrer noopener"
                        data-testid={`obs-open-${tool.key}`}
                      >
                        <Button type="primary" icon={<ExternalLink size={13} strokeWidth={1.5} />}>
                          {t("observability_page.open")}
                        </Button>
                      </a>
                    ) : (
                      <Tag data-testid={`obs-unconfigured-${tool.key}`}>
                        {t("observability_page.unconfigured", { env: tool.env })}
                      </Tag>
                    )}
                  </div>
                </Card>
              );
            })}
          </Space>
        </>
      )}
    </div>
  );
}
