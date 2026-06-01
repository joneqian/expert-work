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
import { FormView } from "./FormView";
import { YamlView } from "./YamlView";

type Tab = "form" | "yaml";

interface ManifestEditorProps {
  mode: "create" | "edit";
  initialYaml: string;
  onChange: (yaml: string) => void;
}

function safeSeed(initialYaml: string): unknown {
  try {
    return parseYaml(initialYaml) ?? {};
  } catch {
    return {};
  }
}

export function ManifestEditor({ mode, initialYaml, onChange }: ManifestEditorProps) {
  const { t } = useTranslation();
  const seed = useMemo(() => safeSeed(initialYaml), [initialYaml]);

  const [schema, setSchema] = useState<JsonSchema | null>(null);
  const [schemaError, setSchemaError] = useState(false);
  const [tab, setTab] = useState<Tab>("form");
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
    if (next === "yaml") {
      const y = dumpYaml(manifestObject);
      setYamlText(y);
      onChange(y);
      setSwitchError(null);
      setTab("yaml");
      return;
    }
    let parsed: unknown;
    try {
      parsed = parseYaml(yamlText);
    } catch {
      setSwitchError(t("manifest_editor.invalid_yaml_hint"));
      return;
    }
    if (schema && validator.validateFormData(parsed, schema).errors.length > 0) {
      setSwitchError(t("manifest_editor.invalid_yaml_hint"));
      return;
    }
    setManifestObject(parsed);
    setSwitchError(null);
    setTab("form");
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
      <div data-testid="manifest-schema-loading" style={{ padding: 24, textAlign: "center" }}>
        <Spin /> <span style={{ marginLeft: 8 }}>{t("manifest_editor.loading_schema")}</span>
      </div>
    );
  }

  const tabButton = (value: Tab, label: string) => {
    const active = tab === value;
    return (
      <button
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
      <div role="tablist" style={{ display: "inline-flex", marginBottom: 12 }}>
        {tabButton("form", t("manifest_editor.tab_form"))}
        {tabButton("yaml", t("manifest_editor.tab_yaml"))}
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

      {tab === "form" ? (
        <FormView schema={schema} formData={manifestObject} onChange={handleFormChange} />
      ) : (
        <YamlView value={yamlText} onChange={handleYamlChange} />
      )}
    </div>
  );
}
