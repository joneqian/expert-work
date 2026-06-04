/**
 * Catalog browser — Stream W (tenant admin).
 *
 * Card list of ``listTenantCatalog()`` entries. Each card shows the
 * connector's display_name, description, category and required-tier badge.
 * Entries with ``entitled === false`` render a lock badge ("requires {tier}
 * plan") and are not selectable. Selecting an entitled entry calls
 * ``onSelect``.
 */
import { Alert, Badge, Button, Card, Empty, Spin, Tag, Typography } from "antd";
import { Lock } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { McpRequiredTier, TenantCatalogEntry } from "../../api/mcp-catalog";

const { Text, Paragraph } = Typography;

const TIER_COLOR: Record<McpRequiredTier, string> = {
  free: "default",
  pro: "blue",
  enterprise: "gold",
};

export interface CatalogBrowserProps {
  entries: TenantCatalogEntry[];
  loading: boolean;
  error: string | null;
  onSelect: (entry: TenantCatalogEntry) => void;
}

export function CatalogBrowser({ entries, loading, error, onSelect }: CatalogBrowserProps) {
  const { t } = useTranslation();

  if (loading) {
    return (
      <div style={{ textAlign: "center", padding: "32px 0" }} data-testid="cb-loading">
        <Spin />
      </div>
    );
  }

  if (error !== null) {
    return (
      <Alert
        type="error"
        showIcon
        message={t("mcp_catalog.browser_failed")}
        description={error}
        data-testid="cb-error"
      />
    );
  }

  if (entries.length === 0) {
    return (
      <Empty description={t("mcp_catalog.browser_empty")} data-testid="cb-empty" />
    );
  }

  return (
    <div data-testid="cb-root" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {entries.map((entry) => {
        const locked = !entry.entitled;
        const card = (
          <Card
            key={entry.id}
            size="small"
            hoverable={!locked}
            data-testid={`cb-card-${entry.name}`}
            style={locked ? { opacity: 0.6 } : undefined}
            styles={{ body: { padding: 14 } }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
              <div style={{ minWidth: 0 }}>
                <Text strong>{entry.display_name}</Text>
                {entry.category && (
                  <Tag style={{ marginLeft: 8 }}>{entry.category}</Tag>
                )}
                <Tag color={TIER_COLOR[entry.required_tier]} style={{ marginLeft: 4 }}>
                  {t(`mcp_catalog.tier_${entry.required_tier}`)}
                </Tag>
                {entry.description && (
                  <Paragraph
                    type="secondary"
                    style={{ marginTop: 6, marginBottom: 0, fontSize: 12 }}
                    ellipsis={{ rows: 2 }}
                  >
                    {entry.description}
                  </Paragraph>
                )}
              </div>
              <div style={{ flexShrink: 0, alignSelf: "center" }}>
                {locked ? (
                  <Button
                    size="small"
                    disabled
                    icon={<Lock size={13} strokeWidth={1.5} />}
                    data-testid={`cb-locked-${entry.name}`}
                  >
                    {t("mcp_catalog.requires_tier", {
                      tier: t(`mcp_catalog.tier_${entry.required_tier}`),
                    })}
                  </Button>
                ) : (
                  <Button
                    type="primary"
                    size="small"
                    data-testid={`cb-select-${entry.name}`}
                    onClick={() => onSelect(entry)}
                  >
                    {t("mcp_catalog.select")}
                  </Button>
                )}
              </div>
            </div>
          </Card>
        );
        return locked ? (
          <Badge.Ribbon
            key={entry.id}
            text={t("mcp_catalog.locked_ribbon")}
            color="gray"
          >
            {card}
          </Badge.Ribbon>
        ) : (
          card
        );
      })}
    </div>
  );
}
