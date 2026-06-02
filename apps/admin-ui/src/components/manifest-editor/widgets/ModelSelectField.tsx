/**
 * Custom RJSF field for a ModelSpec — Stream S PR D (Mini-ADR S-3).
 *
 * Linked provider→model dropdowns (configured providers only, from the model
 * catalog in formContext); selecting a model copies its vision capability into
 * supports_vision. The remaining ModelSpec scalars stay editable in an
 * "Advanced" panel (requirement 5) so nothing is lost. Applied to spec.model
 * and its direct fallback[] items.
 *
 * Requirement 5 uses approach (b) — a bounded hand-rendered advanced set rather
 * than delegating to RJSF's SchemaField. Delegating a *reduced* object schema
 * back through SchemaField with the parent field's idSchema re-enters RJSF's
 * default-merge → onChange → re-render cycle and never converges (heap OOM in
 * jsdom). NOTE: because the advanced set is enumerated here, a future new
 * ModelSpec scalar needs a one-line addition below (or use the YAML tab).
 */
import { Alert, Collapse, Input, InputNumber, Select, Tag } from "antd";
import type { FieldProps } from "@rjsf/utils";
import { useTranslation } from "react-i18next";

import type { ModelCatalog } from "../../../api/model_catalog";
import { lookupModel, modelsFor, providerHasEmbeddings, providerNames } from "../catalog";

type ModelSpecData = {
  provider?: string;
  name?: string;
  supports_vision?: boolean;
  [k: string]: unknown;
};

type AdvancedKind = "number" | "text";
type AdvancedField = { key: string; label: string; kind: AdvancedKind };

/**
 * The bounded advanced ModelSpec set. Only fields present in the incoming
 * JSON Schema's ``properties`` are rendered, so this stays a superset that
 * adapts to whatever ModelSpec subschema RJSF hands us.
 */
const ADVANCED_FIELDS: AdvancedField[] = [
  { key: "temperature", label: "temperature", kind: "number" },
  { key: "max_tokens", label: "max_tokens", kind: "number" },
  { key: "rate_limit_rpm", label: "rate_limit_rpm", kind: "number" },
  { key: "api_key_ref", label: "api_key_ref", kind: "text" },
  { key: "base_url", label: "base_url", kind: "text" },
  { key: "azure_deployment", label: "azure_deployment", kind: "text" },
  { key: "azure_api_version", label: "azure_api_version", kind: "text" },
];

export function ModelSelectField(props: FieldProps) {
  const { formData, onChange, formContext, schema } = props;
  const { t } = useTranslation();
  const data = (formData ?? {}) as ModelSpecData;
  const catalog = (formContext as { modelCatalog?: ModelCatalog } | undefined)?.modelCatalog;

  const providers = catalog ? providerNames(catalog) : [];
  const models = catalog && data.provider ? modelsFor(catalog, data.provider) : [];

  function onProvider(provider: string): void {
    onChange({ ...data, provider, name: undefined, supports_vision: false });
  }
  function onModel(name: string): void {
    const entry = catalog && data.provider ? lookupModel(catalog, data.provider, name) : undefined;
    onChange({ ...data, name, supports_vision: entry?.vision ?? false });
  }
  function onAdvanced(key: string, value: unknown): void {
    onChange({ ...data, [key]: value });
  }

  const noEmbeddings = catalog && data.provider ? !providerHasEmbeddings(catalog, data.provider) : false;

  // Only render advanced fields that exist in this ModelSpec subschema.
  const schemaProps = (schema.properties as Record<string, unknown> | undefined) ?? {};
  const advanced = ADVANCED_FIELDS.filter((f) => f.key in schemaProps);

  return (
    <div data-testid="model-select-field">
      <div data-testid="model-select-provider" style={{ marginBottom: 8 }}>
        <Select
          aria-label={t("model_select.provider_label")}
          placeholder={t("model_select.provider_placeholder")}
          loading={!catalog}
          disabled={!catalog}
          value={data.provider}
          onChange={onProvider}
          options={providers.map((p) => ({ label: p, value: p }))}
          style={{ width: "100%" }}
        />
      </div>
      <div data-testid="model-select-name" style={{ marginBottom: 8 }}>
        <Select
          aria-label={t("model_select.model_label")}
          placeholder={t("model_select.model_placeholder")}
          disabled={!data.provider}
          value={data.name}
          onChange={onModel}
          options={models.map((m) => ({ label: m.name, value: m.name }))}
          style={{ width: "100%" }}
        />
      </div>
      <div data-testid="model-select-vision" style={{ marginBottom: 8 }}>
        <Tag color={data.supports_vision ? "cyan" : "default"}>
          {data.supports_vision ? t("model_select.vision_on") : t("model_select.vision_off")}
        </Tag>
      </div>
      {noEmbeddings && (
        <Alert
          type="info"
          showIcon
          message={t("model_select.no_embeddings")}
          style={{ marginBottom: 8 }}
          data-testid="model-select-no-embeddings"
        />
      )}
      {advanced.length > 0 && (
        <Collapse
          defaultActiveKey={["advanced"]}
          items={[
            {
              key: "advanced",
              label: "Advanced",
              children: (
                <div data-testid="model-select-advanced">
                  {advanced.map((f) => (
                    <label
                      key={f.key}
                      style={{ display: "block", marginBottom: 8 }}
                    >
                      <span style={{ display: "block", marginBottom: 4 }}>{f.label}</span>
                      {f.kind === "number" && (
                        <InputNumber
                          value={data[f.key] as number | undefined}
                          onChange={(v) => onAdvanced(f.key, v ?? undefined)}
                          style={{ width: "100%" }}
                        />
                      )}
                      {f.kind === "text" && (
                        <Input
                          value={(data[f.key] as string | undefined) ?? ""}
                          onChange={(e) =>
                            onAdvanced(f.key, e.target.value === "" ? undefined : e.target.value)
                          }
                        />
                      )}
                    </label>
                  ))}
                </div>
              ),
            },
          ]}
        />
      )}
    </div>
  );
}
