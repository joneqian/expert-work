/**
 * Add MCP server drawer — Stream W (tenant admin).
 *
 * The primary "Add MCP server" affordance. Opens the catalog browser first;
 * selecting an entitled connector swaps to the auth_schema-driven instantiate
 * form. A secondary "Advanced — add a custom server" link demotes the legacy
 * custom-URL registration (the existing ``CreateMcpServerDrawer`` in create
 * mode).
 *
 * Mirrors ``CreateMcpServerDrawer`` chrome (Drawer 560 px, footer-less body
 * with per-step controls, reset-on-close).
 */
import { useCallback, useEffect, useState } from "react";
import { Button, Divider, Drawer, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { listTenantCatalog, type TenantCatalogEntry } from "../../api/mcp-catalog";
import { CatalogBrowser } from "./CatalogBrowser";
import { InstantiateCatalogForm } from "./InstantiateCatalogForm";
import { CreateMcpServerDrawer } from "../CreateMcpServerDrawer";

export interface AddMcpServerDrawerProps {
  open: boolean;
  onClose: () => void;
  /** Fires after a successful create (catalog or custom) so the parent can
   *  refresh + close. */
  onSaved: () => void;
}

type Step = { kind: "browse" } | { kind: "instantiate"; entry: TenantCatalogEntry };

export function AddMcpServerDrawer({ open, onClose, onSaved }: AddMcpServerDrawerProps) {
  const { t } = useTranslation();

  const [step, setStep] = useState<Step>({ kind: "browse" });
  const [entries, setEntries] = useState<TenantCatalogEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [customOpen, setCustomOpen] = useState(false);

  const reset = useCallback(() => {
    setStep({ kind: "browse" });
    setError(null);
    setCustomOpen(false);
  }, []);

  useEffect(() => {
    if (!open) {
      reset();
      return;
    }
    setLoading(true);
    setError(null);
    listTenantCatalog().then(
      (data) => {
        setEntries(data);
        setLoading(false);
      },
      (err: unknown) => {
        setError(err instanceof Error ? err.message : "unknown error");
        setLoading(false);
      },
    );
  }, [open, reset]);

  const handleClose = useCallback(() => {
    reset();
    onClose();
  }, [reset, onClose]);

  return (
    <>
      <Drawer
        open={open}
        onClose={handleClose}
        title={
          step.kind === "browse"
            ? t("mcp_catalog.browser_title")
            : t("mcp_catalog.instantiate_title", { name: step.entry.display_name })
        }
        width={560}
        destroyOnHidden
        data-testid="amsd-drawer"
      >
        {step.kind === "browse" ? (
          <>
            <CatalogBrowser
              entries={entries}
              loading={loading}
              error={error}
              onSelect={(entry) => setStep({ kind: "instantiate", entry })}
            />
            <Divider />
            <div data-testid="amsd-advanced">
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {t("mcp_catalog.advanced_hint")}
              </Typography.Text>
              <div style={{ marginTop: 8 }}>
                <Button data-testid="amsd-custom" onClick={() => setCustomOpen(true)}>
                  {t("mcp_catalog.advanced_custom")}
                </Button>
              </div>
            </div>
          </>
        ) : (
          <InstantiateCatalogForm
            entry={step.entry}
            onBack={() => setStep({ kind: "browse" })}
            onCreated={() => {
              onSaved();
              handleClose();
            }}
          />
        )}
      </Drawer>

      <CreateMcpServerDrawer
        open={customOpen}
        onClose={() => setCustomOpen(false)}
        onSaved={() => {
          setCustomOpen(false);
          onSaved();
          handleClose();
        }}
        editing={null}
      />
    </>
  );
}
