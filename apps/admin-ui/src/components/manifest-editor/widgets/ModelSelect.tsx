/**
 * Plain controlled model picker for the curated agent form. Linked
 * provider→model dropdowns (configured providers only, from the model catalog);
 * selecting a model copies its vision capability into supports_vision. A
 * temperature slider is surfaced directly; the remaining scalars
 * (max_tokens, rate_limit_rpm) live in a collapsed "Advanced" panel.
 *
 * Refactored from the old RJSF model field — no RJSF coupling.
 */
import { Collapse, InputNumber, Select, Slider, Tag } from "antd";
import { useTranslation } from "react-i18next";

import type { ModelCatalog } from "../../../api/model_catalog";
import type { ModelFields } from "../form_model";
import { lookupModel, modelsFor, providerNames } from "../catalog";

interface ModelSelectProps {
  value: ModelFields;
  catalog?: ModelCatalog;
  onChange: (next: ModelFields) => void;
}

export function ModelSelect({ value, catalog, onChange }: ModelSelectProps) {
  const { t } = useTranslation();
  const providers = catalog ? providerNames(catalog) : [];
  const models = catalog && value.provider ? modelsFor(catalog, value.provider) : [];

  function onProvider(provider: string): void {
    onChange({ ...value, provider, name: undefined, supports_vision: false });
  }
  function onModel(name: string): void {
    const entry = catalog && value.provider ? lookupModel(catalog, value.provider, name) : undefined;
    onChange({ ...value, name, supports_vision: entry?.vision ?? false });
  }

  const temperature = value.temperature ?? 0.2;

  return (
    <div data-testid="model-select-field">
      <div data-testid="model-select-provider" style={{ marginBottom: 8 }}>
        <Select
          aria-label={t("model_select.provider_label")}
          placeholder={t("model_select.provider_placeholder")}
          loading={!catalog}
          disabled={!catalog}
          value={value.provider}
          onChange={onProvider}
          options={providers.map((p) => ({ label: p, value: p }))}
          style={{ width: "100%" }}
        />
      </div>
      <div data-testid="model-select-name" style={{ marginBottom: 8 }}>
        <Select
          aria-label={t("model_select.model_label")}
          placeholder={t("model_select.model_placeholder")}
          disabled={!value.provider}
          value={value.name}
          onChange={onModel}
          options={models.map((m) => ({ label: m.name, value: m.name }))}
          style={{ width: "100%" }}
        />
      </div>
      <div data-testid="model-select-vision" style={{ marginBottom: 8 }}>
        <Tag color={value.supports_vision ? "cyan" : "default"}>
          {value.supports_vision ? t("model_select.vision_on") : t("model_select.vision_off")}
        </Tag>
      </div>
      <label data-testid="model-select-temperature" style={{ display: "block", marginBottom: 8 }}>
        <span style={{ display: "block", marginBottom: 4 }}>
          {t("model_select.temperature")}: {temperature}
        </span>
        <Slider
          min={0}
          max={2}
          step={0.1}
          value={temperature}
          ariaLabelForHandle={t("model_select.temperature")}
          onChange={(v) => onChange({ ...value, temperature: v })}
        />
      </label>
      <Collapse
        data-testid="model-select-advanced"
        defaultActiveKey={[]}
        items={[
          {
            key: "advanced",
            label: t("model_select.advanced"),
            children: (
              <>
                <label style={{ display: "block", marginBottom: 8 }}>
                  <span style={{ display: "block", marginBottom: 4 }}>max_tokens</span>
                  <InputNumber
                    value={value.max_tokens}
                    onChange={(v) => onChange({ ...value, max_tokens: v ?? undefined })}
                    style={{ width: "100%" }}
                  />
                </label>
                <label style={{ display: "block", marginBottom: 8 }}>
                  <span style={{ display: "block", marginBottom: 4 }}>rate_limit_rpm</span>
                  <InputNumber
                    value={value.rate_limit_rpm}
                    onChange={(v) => onChange({ ...value, rate_limit_rpm: v ?? undefined })}
                    style={{ width: "100%" }}
                  />
                </label>
              </>
            ),
          },
        ]}
      />
    </div>
  );
}
