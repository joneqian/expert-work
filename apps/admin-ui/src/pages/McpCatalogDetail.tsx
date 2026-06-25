/**
 * Platform MCP server detail page — Stream MCP platform-servers.
 *
 * Routed at ``/settings/mcp-catalog/:catalogId``. Loads the entry and shows two
 * tabs: **配置** (the shared {@link CatalogConfigForm} in edit mode + a Save
 * button) and **工具 (N)** ({@link CatalogToolsTab} — live tool list with a
 * per-tool enable Switch + refresh). Reached from the catalog list's Edit
 * action; creating happens in a Modal, not here.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { Alert, Button, Spin, Tabs } from "antd";
import { Plug } from "lucide-react";
import { useTranslation } from "react-i18next";

import { PageHeader } from "../components/PageHeader";
import {
  CatalogConfigForm,
  type CatalogConfigFormHandle,
} from "../components/mcp_catalog/CatalogConfigForm";
import { CatalogToolsTab } from "../components/mcp_catalog/CatalogToolsTab";
import {
  getPlatformCatalogEntry,
  type McpCatalogEntry,
} from "../api/mcp-catalog";

export function McpCatalogDetail() {
  const { catalogId } = useParams<{ catalogId: string }>();
  const { t } = useTranslation();
  const formRef = useRef<CatalogConfigFormHandle>(null);

  const [entry, setEntry] = useState<McpCatalogEntry | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [toolCount, setToolCount] = useState<number | null>(null);

  const load = useCallback(async () => {
    if (!catalogId) return;
    setLoading(true);
    try {
      setEntry(await getPlatformCatalogEntry(catalogId));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "unknown error");
    } finally {
      setLoading(false);
    }
  }, [catalogId]);

  useEffect(() => {
    void load();
  }, [load]);

  const backTo = {
    label: t("mcp_catalog.page_title"),
    to: "/settings/mcp-catalog",
  };

  return (
    <div data-testid="mcd-root">
      <PageHeader
        icon={<Plug size={18} strokeWidth={1.5} />}
        title={entry?.display_name ?? t("mcp_catalog.edit_title")}
        subtitle={entry?.name}
        backTo={backTo}
      />

      {error !== null ? (
        <Alert
          type="error"
          showIcon
          data-testid="mcd-error"
          message={t("mcp_catalog.failed_to_load")}
          description={error}
        />
      ) : loading || entry === null ? (
        <div style={{ textAlign: "center", padding: "48px 0" }}>
          <Spin />
        </div>
      ) : (
        <Tabs
          defaultActiveKey="config"
          items={[
            {
              key: "config",
              label: t("mcp_catalog.tab_config"),
              children: (
                <>
                  <CatalogConfigForm
                    ref={formRef}
                    editing={entry}
                    onSaved={load}
                    onSubmittingChange={setSaving}
                  />
                  <div style={{ display: "flex", justifyContent: "flex-end" }}>
                    <Button
                      type="primary"
                      loading={saving}
                      onClick={() => void formRef.current?.submit()}
                      data-testid="mcd-save"
                    >
                      {t("mcp_catalog.submit_save")}
                    </Button>
                  </div>
                </>
              ),
            },
            {
              key: "tools",
              label:
                toolCount === null
                  ? t("mcp_catalog.tools_title")
                  : `${t("mcp_catalog.tools_title")} (${toolCount})`,
              children: (
                <CatalogToolsTab
                  entry={entry}
                  onUpdated={setEntry}
                  onLoaded={setToolCount}
                />
              ),
            },
          ]}
        />
      )}
    </div>
  );
}
