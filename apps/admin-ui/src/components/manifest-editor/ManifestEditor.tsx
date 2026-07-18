/**
 * Visual manifest editor — Stream S PR C (Mini-ADRs S-1/S-2/S-6), migrated to
 * the group-nav + detail-pane layout (agent-config-page redesign PR1).
 *
 * Left: a ``GroupNav`` tree over ``CONFIG_GROUPS`` (+ an optional caller
 * leading node). Right: a detail pane that stacks the active group's
 * ``FormView`` sections. A top-right toggle swaps the pane to a raw YAML
 * escape hatch over the same in-memory ``manifestObject``: toggling on
 * serialises (Form→YAML); toggling off parses+validates (YAML→Form), and an
 * invalid switch is blocked with an inline error — same semantics as the
 * flat-tab row this replaces. ``onChange`` always carries the latest manifest
 * as a YAML string so the parent submits exactly what's shown.
 */
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Alert, Button, Spin } from "antd";
import validator from "@rjsf/validator-ajv8";
import { useTranslation } from "react-i18next";

import type { JsonSchema } from "../../api/manifest_schema";
import { loadAgentSchema } from "./schema";
import { dumpYaml, parseYaml } from "./yaml";
import { normalizeForSubmit } from "./form_model";
import { FormView, type FormSection } from "./FormView";
import { YamlView } from "./YamlView";
import { GroupNav } from "./GroupNav";
import { SettingsSearch } from "./SettingsSearch";
import { CONFIG_GROUPS } from "./groups";
import { RunBudgetSection } from "./groups/RunBudgetSection";
import type { McpPickerSource } from "./widgets/McpToolPicker";

/** A caller-supplied node rendered ABOVE the registered groups in the tree —
 * e.g. an Agent template's marketplace-metadata form. Its content is kept
 * mounted (hidden when inactive) so any embedded antd Form keeps its state
 * across group switches. Switching to/from a leading tab never touches the
 * manifest, so no (de)serialisation happens. ``GroupNav`` renders a single
 * leading node, so only ``leadingTabs[0]`` gets a tree entry — every current
 * caller passes at most one. */
export interface LeadingTab {
  value: string;
  label: string;
  content: ReactNode;
  /** Optionally fold one manifest section into this leading tab — that
   * section renders below ``content`` and is dropped from its mapped
   * ``CONFIG_GROUPS`` group so it never renders twice. Used to merge a
   * template's "basic info" with the manifest's "basic" section. */
  mergeSection?: FormSection;
}

interface ManifestEditorProps {
  mode: "create" | "edit";
  initialYaml: string;
  onChange: (yaml: string) => void;
  leadingTabs?: ReadonlyArray<LeadingTab>;
  /** Forwarded to the MCP section — ``catalog`` for a platform template. */
  mcpSource?: McpPickerSource;
}

function safeSeed(initialYaml: string): unknown {
  try {
    const parsed = parseYaml(initialYaml);
    // The Form view (RJSF) expects an object. A scalar/array/empty seed (e.g.
    // a stray "42") would render a broken form, so fall back to {} — the raw
    // value is still recoverable via the YAML view.
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
  const primaryLeading = leadingTabs[0];

  const [schema, setSchema] = useState<JsonSchema | null>(null);
  const [schemaError, setSchemaError] = useState(false);
  const [group, setGroup] = useState<string>(
    primaryLeading?.value ?? CONFIG_GROUPS[0].id,
  );
  const [manifestObject, setManifestObject] = useState<unknown>(seed);
  const [yamlText, setYamlText] = useState<string>(initialYaml);
  const [yamlActive, setYamlActive] = useState(false);
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
    //
    // Keep the raw ``data`` as the form's working state (so an added-but-unfilled
    // fallback row still shows its picker), but serialize a normalized copy so
    // the submitted manifest never carries an incomplete / duplicate fallback
    // entry the backend would reject.
    setManifestObject(data);
    const y = dumpYaml(normalizeForSubmit(data));
    setYamlText(y);
    onChange(y);
  }

  function handleYamlChange(text: string): void {
    setYamlText(text);
    onChange(text);
  }

  // Guarded YAML→Form transition — parses + validates ``yamlText`` before
  // adopting it; an invalid switch is refused (inline error, stays on YAML)
  // — moved verbatim from the old ``switchTo``. Shared by the YAML toggle
  // button AND by clicking a group node while YAML mode is active (below),
  // so both paths run the exact same guard and neither can desync the tree
  // highlight from the pane. Returns whether the switch succeeded.
  function tryExitYaml(): boolean {
    let parsed: unknown;
    try {
      parsed = parseYaml(yamlText);
    } catch {
      setSwitchError(t("manifest_editor.invalid_yaml_hint"));
      return false;
    }
    if (
      schema &&
      validator.validateFormData(parsed, schema).errors.length > 0
    ) {
      setSwitchError(t("manifest_editor.invalid_yaml_hint"));
      return false;
    }
    setManifestObject(parsed);
    setSwitchError(null);
    setYamlActive(false);
    return true;
  }

  // Top-right YAML toggle — replaces the old flat-tab row's "yaml" tab.
  // Entering YAML serialises the current curated manifest (normalized so an
  // incomplete fallback row doesn't leak into the YAML view / submit).
  function toggleYaml(): void {
    if (!yamlActive) {
      const y = dumpYaml(normalizeForSubmit(manifestObject));
      setYamlText(y);
      onChange(y);
      setSwitchError(null);
      setYamlActive(true);
      return;
    }
    tryExitYaml();
  }

  // Group-nav click — mirrors the old UI where clicking a form tab from the
  // yaml tab ran the same guarded switch as the toggle: while YAML mode is
  // active, attempt the YAML→Form transition first. On success, exit YAML
  // mode AND move to the clicked group; on failure, stay in YAML mode with
  // the error shown and DON'T move the tree highlight (setGroup is skipped).
  function handleGroupSelect(id: string): void {
    if (yamlActive) {
      if (tryExitYaml()) {
        setGroup(id);
      }
      return;
    }
    setGroup(id);
  }

  // Without leading tabs the editor has nothing to show until the schema
  // resolves, so it gates the whole component (unchanged behaviour). With
  // leading tabs (e.g. a template's metadata form) the tree + that form stay
  // usable while the schema loads; only the manifest pane waits.
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

  const isLeadingActive = leadingTabs.some((lt) => lt.value === group);
  // Manifest sections folded into a leading tab — dropped from their mapped
  // group's stacked render so they never render twice.
  const mergedSections = new Set<string>(
    leadingTabs.flatMap((lt) => (lt.mergeSection ? [lt.mergeSection] : [])),
  );
  // A group whose EVERY (statically registered) section got folded into a
  // leading tab renders nothing on its own — e.g. "basic" once its only
  // section is merged. Its node must be unreachable, or clicking it would
  // show FormView with an empty sections array (a blank pane). Groups that
  // are statically empty to begin with (context/sandbox/observability,
  // pending Phase 2) are untouched — they keep showing with the pending hint.
  // "budget" is also statically empty (``sections: []``) but is special-cased
  // below to render RunBudgetSection instead (Task 6 pilot).
  const hiddenGroups = CONFIG_GROUPS.filter(
    (g) =>
      g.sections.length > 0 &&
      g.sections.every((s) => mergedSections.has(s)),
  ).map((g) => g.id);
  const activeConfigGroup = CONFIG_GROUPS.find((g) => g.id === group);

  const pane = schemaError ? (
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
  ) : yamlActive ? (
    <YamlView value={yamlText} onChange={handleYamlChange} />
  ) : activeConfigGroup?.id === "budget" ? (
    <RunBudgetSection formData={manifestObject} onChange={handleFormChange} />
  ) : activeConfigGroup && activeConfigGroup.sections.length === 0 ? (
    <div data-testid="cfg-pane-pending" style={{ padding: 24 }}>
      <Alert
        type="info"
        showIcon
        message={t("manifest_editor.group_pending_hint")}
      />
    </div>
  ) : activeConfigGroup ? (
    <FormView
      formData={manifestObject}
      onChange={handleFormChange}
      sections={activeConfigGroup.sections.filter(
        (s) => !mergedSections.has(s),
      )}
      mcpSource={mcpSource}
    />
  ) : null;

  // The leading pane wins over the group/YAML pane only when it's active AND
  // the YAML toggle is off — toggling YAML always shows YAML, whatever group
  // was selected before.
  const showLeadingPane = !yamlActive && isLeadingActive;

  return (
    <div data-testid={`manifest-editor-${mode}`}>
      <div style={{ display: "flex", gap: 24, alignItems: "flex-start" }}>
        <div data-testid="cfg-nav" style={{ flex: "0 0 200px", width: 200 }}>
          <GroupNav
            active={group}
            onSelect={handleGroupSelect}
            leading={
              primaryLeading
                ? { value: primaryLeading.value, label: primaryLeading.label }
                : undefined
            }
            hiddenGroups={hiddenGroups}
          />
        </div>

        <div data-testid="cfg-pane" style={{ flex: 1, minWidth: 0 }}>
          <div
            style={{
              display: "flex",
              justifyContent: "flex-end",
              alignItems: "center",
              gap: 12,
              marginBottom: 12,
            }}
          >
            <SettingsSearch onPick={handleGroupSelect} exclude={hiddenGroups} />
            <Button
              type={yamlActive ? "primary" : "default"}
              size="small"
              data-testid="cfg-yaml-toggle"
              aria-pressed={yamlActive}
              onClick={toggleYaml}
            >
              {t("manifest_editor.tab_yaml")}
            </Button>
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

          {/* Leading tabs stay mounted (hidden when inactive) so an embedded
              antd Form keeps its state across group switches. */}
          {leadingTabs.map((lt) => (
            <div
              key={lt.value}
              data-testid={`manifest-leading-${lt.value}`}
              style={{
                display: !yamlActive && group === lt.value ? "block" : "none",
              }}
            >
              {/* Folded manifest section first (e.g. agent name/description on
                  top), then the caller's own content (e.g. template
                  marketplace fields). */}
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

          {!showLeadingPane && pane}
        </div>
      </div>
    </div>
  );
}
