/**
 * Visual manifest editor — Stream S PR C (Mini-ADRs S-1/S-2/S-6).
 *
 * VS-Code-Settings style: a schema-driven Form tab and a raw YAML escape
 * hatch over a single in-memory ``manifestObject``. Switching tabs serialises
 * (Form→YAML) or parses+validates (YAML→Form); an invalid YAML→Form switch is
 * blocked with an inline error. ``onChange`` always carries the latest manifest
 * as a YAML string so the parent submits exactly what's shown.
 */
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Alert, Spin } from "antd";
import validator from "@rjsf/validator-ajv8";
import { useTranslation } from "react-i18next";

import type { JsonSchema } from "../../api/manifest_schema";
import { loadAgentSchema } from "./schema";
import { dumpYaml, parseYaml } from "./yaml";
import { FormView, type FormSection } from "./FormView";
import { YamlView } from "./YamlView";
import type { McpPickerSource } from "./widgets/McpToolPicker";

// The curated manifest field groups + the YAML escape hatch, as one flat tab
// row. ``labelKey`` is an i18n key.
const MANIFEST_TABS: ReadonlyArray<{
  value: FormSection | "yaml";
  labelKey: string;
}> = [
  { value: "basic", labelKey: "manifest_editor.tab_basic" },
  { value: "model", labelKey: "manifest_editor.tab_model" },
  { value: "prompt", labelKey: "manifest_editor.tab_prompt" },
  { value: "tools", labelKey: "manifest_editor.tab_tools" },
  { value: "mcp", labelKey: "manifest_editor.tab_mcp" },
  { value: "capabilities", labelKey: "manifest_editor.tab_capabilities" },
  { value: "memory", labelKey: "manifest_editor.tab_memory" },
  { value: "governance", labelKey: "manifest_editor.tab_governance" },
  { value: "yaml", labelKey: "manifest_editor.tab_yaml" },
];

const FORM_SECTIONS: readonly string[] = MANIFEST_TABS.map(
  (t) => t.value,
).filter((v) => v !== "yaml");

const isFormSection = (tab: string): tab is FormSection =>
  FORM_SECTIONS.includes(tab);

/** A caller-supplied tab rendered in the SAME flat row, BEFORE the manifest
 * tabs — e.g. an Agent template's marketplace-metadata form. Its content is
 * kept mounted (hidden when inactive) so any embedded antd Form keeps its
 * state across tab switches. Switching to/from a leading tab never touches the
 * manifest, so no (de)serialisation happens. */
export interface LeadingTab {
  value: string;
  label: string;
  content: ReactNode;
  /** Optionally fold one manifest section into this leading tab — that section
   * renders below ``content`` and is removed from the manifest tab row. Used
   * to merge a template's "basic info" with the manifest's "basic" section. */
  mergeSection?: FormSection;
}

interface ManifestEditorProps {
  mode: "create" | "edit";
  initialYaml: string;
  onChange: (yaml: string) => void;
  leadingTabs?: ReadonlyArray<LeadingTab>;
  /** Forwarded to the MCP tab — ``catalog`` for a platform template. */
  mcpSource?: McpPickerSource;
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
  leadingTabs = [],
  mcpSource,
}: ManifestEditorProps) {
  const { t } = useTranslation();
  const seed = useMemo(() => safeSeed(initialYaml), [initialYaml]);

  const [schema, setSchema] = useState<JsonSchema | null>(null);
  const [schemaError, setSchemaError] = useState(false);
  const [tab, setTab] = useState<string>(leadingTabs[0]?.value ?? "basic");
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

  function switchTo(next: string): void {
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
    // Moving away from anything but YAML (a curated section or a leading tab)
    // needs no (de)serialisation — they share one ``manifestObject`` (a leading
    // tab doesn't touch the manifest at all); only the rendered panel changes.
    if (tab !== "yaml") {
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

  // Without leading tabs the editor has nothing to show until the schema
  // resolves, so it gates the whole component (unchanged behaviour). With
  // leading tabs (e.g. a template's metadata form) the tab row + that form
  // stay usable while the schema loads; only the manifest body waits.
  if (leadingTabs.length === 0) {
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
  }

  const tabButton = (value: string, label: string) => {
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
          flex: "0 0 auto",
          whiteSpace: "nowrap",
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

  const isLeadingActive = leadingTabs.some((lt) => lt.value === tab);
  // Manifest sections folded into a leading tab — dropped from the tab row.
  const mergedSections = new Set<string>(
    leadingTabs.flatMap((lt) => (lt.mergeSection ? [lt.mergeSection] : [])),
  );

  const manifestBody = schemaError ? (
    <Alert
      type="error"
      showIcon
      message={t("manifest_editor.schema_load_failed")}
      data-testid="manifest-schema-error"
    />
  ) : schema === null ? (
    <div
      data-testid="manifest-schema-loading"
      style={{ padding: 24, textAlign: "center" }}
    >
      <Spin />{" "}
      <span style={{ marginLeft: 8 }}>
        {t("manifest_editor.loading_schema")}
      </span>
    </div>
  ) : isFormSection(tab) ? (
    <FormView
      formData={manifestObject}
      onChange={handleFormChange}
      section={tab}
      mcpSource={mcpSource}
    />
  ) : (
    <YamlView value={yamlText} onChange={handleYamlChange} />
  );

  return (
    <div data-testid={`manifest-editor-${mode}`}>
      {/* One flat row; horizontally scrollable so a long tab set never wraps. */}
      <div
        role="tablist"
        style={{
          display: "flex",
          flexWrap: "nowrap",
          overflowX: "auto",
          marginBottom: 12,
        }}
      >
        {leadingTabs.map((lt) => tabButton(lt.value, lt.label))}
        {MANIFEST_TABS.filter(
          (tabDef) => !mergedSections.has(tabDef.value),
        ).map((tabDef) => tabButton(tabDef.value, t(tabDef.labelKey)))}
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

      {/* Leading tabs stay mounted (hidden when inactive) so an embedded antd
          Form keeps its state across tab switches. */}
      {leadingTabs.map((lt) => (
        <div
          key={lt.value}
          data-testid={`manifest-leading-${lt.value}`}
          style={{ display: tab === lt.value ? "block" : "none" }}
        >
          {/* Folded manifest section first (e.g. agent name/description on top),
              then the caller's own content (e.g. template marketplace fields). */}
          {lt.mergeSection && (
            <FormView
              formData={manifestObject}
              onChange={handleFormChange}
              section={lt.mergeSection}
              mcpSource={mcpSource}
              bare
            />
          )}
          {lt.content}
        </div>
      ))}

      {!isLeadingActive && manifestBody}
    </div>
  );
}
