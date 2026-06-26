/**
 * Visual manifest editor — Stream S PR C (Mini-ADRs S-1/S-2/S-6).
 *
 * VS-Code-Settings style: a schema-driven Form tab and a raw YAML escape
 * hatch over a single in-memory ``manifestObject``. Switching tabs serialises
 * (Form→YAML) or parses+validates (YAML→Form); an invalid YAML→Form switch is
 * blocked with an inline error. ``onChange`` always carries the latest manifest
 * as a YAML string so the parent submits exactly what's shown.
 */
import { useEffect, useMemo, useState } from "react";
import { Alert, Spin } from "antd";
import validator from "@rjsf/validator-ajv8";
import { useTranslation } from "react-i18next";

import type { JsonSchema } from "../../api/manifest_schema";
import { loadAgentSchema } from "./schema";
import { dumpYaml, parseYaml } from "./yaml";
import { FormView, type FormSection } from "./FormView";
import { YamlView } from "./YamlView";

type Tab = FormSection | "yaml";

// One flat row of tabs (the YAML escape hatch sits alongside the curated form
// sections instead of nesting under a Form tab). ``labelKey`` is an i18n key.
const TABS: ReadonlyArray<{ value: Tab; labelKey: string }> = [
  { value: "basic", labelKey: "manifest_editor.tab_basic" },
  { value: "model", labelKey: "manifest_editor.tab_model" },
  { value: "prompt", labelKey: "manifest_editor.tab_prompt" },
  { value: "tools", labelKey: "manifest_editor.tab_tools" },
  { value: "capabilities", labelKey: "manifest_editor.tab_capabilities" },
  { value: "memory", labelKey: "manifest_editor.tab_memory" },
  { value: "governance", labelKey: "manifest_editor.tab_governance" },
  { value: "yaml", labelKey: "manifest_editor.tab_yaml" },
];

const isFormSection = (tab: Tab): tab is FormSection => tab !== "yaml";

interface ManifestEditorProps {
  mode: "create" | "edit";
  initialYaml: string;
  onChange: (yaml: string) => void;
}

function safeSeed(initialYaml: string): unknown {
  try {
    const parsed = parseYaml(initialYaml);
    // The Form view (RJSF) expects an object. A scalar/array/empty seed (e.g.
    // a stray "42") would render a broken form, so fall back to {} — the raw
    // value is still recoverable via the YAML tab.
    return parsed !== null &&
      typeof parsed === "object" &&
      !Array.isArray(parsed)
      ? parsed
      : {};
  } catch {
    return {};
  }
}

export function ManifestEditor({
  mode,
  initialYaml,
  onChange,
}: ManifestEditorProps) {
  const { t } = useTranslation();
  const seed = useMemo(() => safeSeed(initialYaml), [initialYaml]);

  const [schema, setSchema] = useState<JsonSchema | null>(null);
  const [schemaError, setSchemaError] = useState(false);
  const [tab, setTab] = useState<Tab>("basic");
  const [manifestObject, setManifestObject] = useState<unknown>(seed);
  const [yamlText, setYamlText] = useState<string>(initialYaml);
  const [switchError, setSwitchError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    loadAgentSchema().then(
      (s) => alive && setSchema(s),
      () => alive && setSchemaError(true),
    );
    return () => {
      alive = false;
    };
  }, []);

  function handleFormChange(data: unknown): void {
    // The curated Form merges edits into the full manifest and preserves
    // non-curated fields: keys a user hand-added in raw YAML survive a Form
    // round-trip (the form_model writers patch only the curated paths). The
    // backend ManifestLoader re-validates on submit regardless.
    setManifestObject(data);
    const y = dumpYaml(data);
    setYamlText(y);
    onChange(y);
  }

  function handleYamlChange(text: string): void {
    setYamlText(text);
    onChange(text);
  }

  function switchTo(next: Tab): void {
    if (next === tab) return;
    // Leaving for YAML: serialise the current curated manifest.
    if (next === "yaml") {
      const y = dumpYaml(manifestObject);
      setYamlText(y);
      onChange(y);
      setSwitchError(null);
      setTab("yaml");
      return;
    }
    // Moving between curated sections needs no (de)serialisation — they share
    // one ``manifestObject``; only the rendered section changes.
    if (isFormSection(tab)) {
      setSwitchError(null);
      setTab(next);
      return;
    }
    // Returning from YAML: parse + validate before adopting the edited text.
    let parsed: unknown;
    try {
      parsed = parseYaml(yamlText);
    } catch {
      setSwitchError(t("manifest_editor.invalid_yaml_hint"));
      return;
    }
    if (
      schema &&
      validator.validateFormData(parsed, schema).errors.length > 0
    ) {
      setSwitchError(t("manifest_editor.invalid_yaml_hint"));
      return;
    }
    setManifestObject(parsed);
    setSwitchError(null);
    setTab(next);
  }

  if (schemaError) {
    return (
      <Alert
        type="error"
        showIcon
        message={t("manifest_editor.schema_load_failed")}
        data-testid="manifest-schema-error"
      />
    );
  }
  if (schema === null) {
    return (
      <div
        data-testid="manifest-schema-loading"
        style={{ padding: 24, textAlign: "center" }}
      >
        <Spin />{" "}
        <span style={{ marginLeft: 8 }}>
          {t("manifest_editor.loading_schema")}
        </span>
      </div>
    );
  }

  const tabButton = (value: Tab, label: string) => {
    const active = tab === value;
    return (
      <button
        key={value}
        type="button"
        role="tab"
        aria-selected={active}
        data-testid={`manifest-tab-${value}`}
        onClick={() => switchTo(value)}
        style={{
          padding: "4px 16px",
          border: "1px solid var(--hx-border, #303030)",
          background: active ? "var(--hx-brand, #13c2c2)" : "transparent",
          color: active ? "#fff" : "inherit",
          cursor: "pointer",
        }}
      >
        {label}
      </button>
    );
  };

  return (
    <div data-testid={`manifest-editor-${mode}`}>
      <div
        role="tablist"
        style={{ display: "flex", flexWrap: "wrap", marginBottom: 12 }}
      >
        {TABS.map((tabDef) => tabButton(tabDef.value, t(tabDef.labelKey)))}
      </div>

      {switchError !== null && (
        <Alert
          type="warning"
          showIcon
          message={t("manifest_editor.invalid_yaml_title")}
          description={switchError}
          style={{ marginBottom: 12 }}
          data-testid="manifest-switch-error"
        />
      )}

      {isFormSection(tab) ? (
        <FormView
          formData={manifestObject}
          onChange={handleFormChange}
          section={tab}
        />
      ) : (
        <YamlView value={yamlText} onChange={handleYamlChange} />
      )}
    </div>
  );
}
