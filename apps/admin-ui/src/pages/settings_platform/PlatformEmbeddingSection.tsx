/**
 * Platform Embedding / Rerank config section (Stream T PR D).
 *
 * Self-contained section: GETs the platform embedding-config on mount, shows
 * the current embedding (+ rerank) selection, and an edit form to switch the
 * embedding model — and optionally enable a rerank model. Saves via PUT.
 * system_admin-only at the route level (Task 3 wires it into /settings/platform);
 * this component does its own data fetch and surfaces backend error codes.
 */
import { useCallback, useEffect, useMemo, useState, type ReactElement } from "react";
import { Alert, App, Button, Select, Spin, Switch, Typography } from "antd";
import { useTranslation } from "react-i18next";

import {
  getPlatformEmbeddingConfig,
  putPlatformEmbeddingConfig,
  type PlatformEmbeddingConfigView,
  type ProviderModel,
} from "../../api/platform_embedding_config";
import { ApiError } from "../../api/client";

const { Text } = Typography;

export interface PlatformEmbeddingSectionProps {
  /** Invoked after a successful save (so a parent page can refresh/notify). */
  onSaved?: () => void;
}

/** Distinct provider names, in first-seen order, from a provider/model list. */
function distinctProviders(models: readonly ProviderModel[]): string[] {
  return [...new Set(models.map((m) => m.provider))];
}

/** Models for a given provider. */
function modelsFor(models: readonly ProviderModel[], provider: string | null): string[] {
  if (provider === null) {
    return [];
  }
  return models.filter((m) => m.provider === provider).map((m) => m.model);
}

export function PlatformEmbeddingSection({ onSaved }: PlatformEmbeddingSectionProps): ReactElement {
  const { t } = useTranslation();
  const { message } = App.useApp();

  const [view, setView] = useState<PlatformEmbeddingConfigView | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [embeddingProvider, setEmbeddingProvider] = useState<string | null>(null);
  const [embeddingModel, setEmbeddingModel] = useState<string | null>(null);
  const [rerankOn, setRerankOn] = useState(false);
  const [rerankProvider, setRerankProvider] = useState<string | null>(null);
  const [rerankModel, setRerankModel] = useState<string | null>(null);

  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const applyView = useCallback((next: PlatformEmbeddingConfigView) => {
    setView(next);
    setEmbeddingProvider(next.embedding?.provider ?? null);
    setEmbeddingModel(next.embedding?.model ?? null);
    setRerankOn(next.rerank !== null);
    setRerankProvider(next.rerank?.provider ?? null);
    setRerankModel(next.rerank?.model ?? null);
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      applyView(await getPlatformEmbeddingConfig());
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "unknown error");
    } finally {
      setLoading(false);
    }
  }, [applyView]);

  useEffect(() => {
    void load();
  }, [load]);

  const embeddingProviders = useMemo(
    () => distinctProviders(view?.available_embedding ?? []),
    [view],
  );
  const embeddingModels = useMemo(
    () => modelsFor(view?.available_embedding ?? [], embeddingProvider),
    [view, embeddingProvider],
  );
  const rerankProviders = useMemo(
    () => distinctProviders(view?.available_rerank ?? []),
    [view],
  );
  const rerankModels = useMemo(
    () => modelsFor(view?.available_rerank ?? [], rerankProvider),
    [view, rerankProvider],
  );

  const errMessage = useCallback(
    (err: ApiError): string => {
      const key = `settings_platform.embedding_err_${err.code}`;
      const translated = t(key);
      return translated === key ? err.message : translated;
    },
    [t],
  );

  const onSave = useCallback(async () => {
    setSaving(true);
    setSaveError(null);
    try {
      const result = await putPlatformEmbeddingConfig({
        embedding_provider: embeddingProvider ?? "",
        embedding_model: embeddingModel ?? "",
        ...(rerankOn && rerankProvider !== null && rerankModel !== null
          ? { rerank_provider: rerankProvider, rerank_model: rerankModel }
          : {}),
      });
      // Refresh local state from the PUT result, preserving the catalog.
      applyView({
        embedding: result.embedding,
        rerank: result.rerank,
        available_embedding: view?.available_embedding ?? [],
        available_rerank: view?.available_rerank ?? [],
      });
      message.success(t("settings_platform.embedding_saved"));
      onSaved?.();
    } catch (err) {
      setSaveError(err instanceof ApiError ? errMessage(err) : t("settings_platform.embedding_save"));
    } finally {
      setSaving(false);
    }
  }, [
    embeddingProvider,
    embeddingModel,
    rerankOn,
    rerankProvider,
    rerankModel,
    applyView,
    view,
    message,
    t,
    onSaved,
    errMessage,
  ]);

  if (loading) {
    return (
      <div style={{ padding: 24, textAlign: "center" }} data-testid="pe-loading">
        <Spin />
      </div>
    );
  }

  if (loadError !== null || view === null) {
    return (
      <Alert
        type="error"
        showIcon
        message={t("settings_platform.embedding_current")}
        description={loadError ?? "unknown error"}
        data-testid="pe-load-error"
      />
    );
  }

  return (
    <div data-testid="pe-root">
      <h2 style={{ fontSize: 15, margin: "8px 0" }}>{t("settings_platform.embedding_current")}</h2>
      {view.embedding === null ? (
        <Alert
          type="warning"
          showIcon
          message={t("settings_platform.embedding_unconfigured")}
          style={{ marginBottom: 16 }}
          data-testid="pe-unconfigured"
        />
      ) : (
        <Text style={{ display: "block", marginBottom: 16 }}>
          {view.embedding.provider} / {view.embedding.model}
        </Text>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: 16, maxWidth: 480 }}>
        <label>
          <div style={{ marginBottom: 4 }}>{t("settings_platform.embedding_provider_label")}</div>
          <div data-testid="pe-embedding-provider">
            <Select
              style={{ width: "100%" }}
              value={embeddingProvider ?? undefined}
              onChange={(value: string) => {
                setEmbeddingProvider(value);
                setEmbeddingModel(null);
              }}
              options={embeddingProviders.map((p) => ({ label: p, value: p }))}
              aria-label={t("settings_platform.embedding_provider_label")}
            />
          </div>
        </label>

        <label>
          <div style={{ marginBottom: 4 }}>{t("settings_platform.embedding_model_label")}</div>
          <div data-testid="pe-embedding-model">
            <Select
              style={{ width: "100%" }}
              value={embeddingModel ?? undefined}
              disabled={embeddingProvider === null}
              onChange={(value: string) => setEmbeddingModel(value)}
              options={embeddingModels.map((m) => ({ label: m, value: m }))}
              aria-label={t("settings_platform.embedding_model_label")}
            />
          </div>
        </label>

        <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <Switch
            checked={rerankOn}
            onChange={(checked) => {
              setRerankOn(checked);
              if (!checked) {
                setRerankProvider(null);
                setRerankModel(null);
              }
            }}
            aria-label={t("settings_platform.rerank_enable")}
            data-testid="pe-rerank-toggle"
          />
          <span>{t("settings_platform.rerank_enable")}</span>
        </label>

        {rerankOn && (
          <>
            <label>
              <div style={{ marginBottom: 4 }}>{t("settings_platform.rerank_provider_label")}</div>
              <div data-testid="pe-rerank-provider">
                <Select
                  style={{ width: "100%" }}
                  value={rerankProvider ?? undefined}
                  onChange={(value: string) => {
                    setRerankProvider(value);
                    setRerankModel(null);
                  }}
                  options={rerankProviders.map((p) => ({ label: p, value: p }))}
                  aria-label={t("settings_platform.rerank_provider_label")}
                />
              </div>
            </label>

            <label>
              <div style={{ marginBottom: 4 }}>{t("settings_platform.rerank_model_label")}</div>
              <div data-testid="pe-rerank-model">
                <Select
                  style={{ width: "100%" }}
                  value={rerankModel ?? undefined}
                  disabled={rerankProvider === null}
                  onChange={(value: string) => setRerankModel(value)}
                  options={rerankModels.map((m) => ({ label: m, value: m }))}
                  aria-label={t("settings_platform.rerank_model_label")}
                />
              </div>
            </label>
          </>
        )}

        {saveError !== null && (
          <Alert type="error" showIcon message={saveError} data-testid="pe-error" />
        )}

        <div>
          <Button type="primary" loading={saving} onClick={onSave} data-testid="pe-save">
            {t("settings_platform.embedding_save")}
          </Button>
        </div>
      </div>
    </div>
  );
}
