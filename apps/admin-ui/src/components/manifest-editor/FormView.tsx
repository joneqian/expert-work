/**
 * Curated agent form — a hand-built view over the canonical fields of an agent
 * manifest. The fields are grouped into named sections (basic / model / prompt /
 * tools / capabilities / memory / governance); the parent ``ManifestEditor``
 * renders one section per tab, so the form reads as a short focused panel
 * instead of one long scroll. Every control emits the FULL merged manifest via
 * the form_model writers, so non-curated fields a user hand-added in raw YAML
 * are preserved across a Form round-trip. The model catalog is loaded once and
 * handed to ModelSelect.
 */
import { useEffect, useState, type ReactNode } from "react";
import {
  Alert,
  Button,
  Checkbox,
  Collapse,
  Input,
  InputNumber,
  Select,
  Switch,
  Typography,
} from "antd";
import { useTranslation } from "react-i18next";

import type { ModelCatalog } from "../../api/model_catalog";
import { FieldHelp } from "../FieldHelp";
import { KnowledgePicker } from "./KnowledgePicker";
import { SkillPicker } from "./SkillPicker";
import { SubagentPicker } from "./SubagentPicker";
import { PromptVariablesEditor } from "./PromptVariablesEditor";
import { loadModelCatalog } from "./catalog";
import { ModelSelect } from "./widgets/ModelSelect";
import {
  readActionScreen,
  readActionScreenOnError,
  readApprovalTools,
  readDescription,
  readDynamicWorkersOn,
  readExtends,
  readFallback,
  readMainSupportsVision,
  readMemoryOn,
  readModel,
  readName,
  readOutputDlp,
  readOutputJudge,
  readOutputJudgeOnError,
  readOutputSchemaName,
  readOutputScreen,
  readPromptInjection,
  readPromptJinja,
  readPromptVariables,
  readApprovalTimeout,
  readReconcileWrites,
  readReflectionEvaluator,
  readReflectionEvaluatorOn,
  readRecallMode,
  readRunDeadline,
  readSystemPrompt,
  readTools,
  readTopK,
  readToolBudgetOn,
  readTrajectoryRecording,
  readVerifyReads,
  readVisionModel,
  readVisionOn,
  readWriteBack,
  readWriteMinImportance,
  setActionScreen,
  setActionScreenOnError,
  setApprovalTimeout,
  setApprovalTools,
  setDescription,
  setDynamicWorkersOn,
  setFallback,
  setMcp,
  setMemoryOn,
  setModel,
  setName,
  setOutputDlp,
  setOutputJudge,
  setOutputJudgeOnError,
  setOutputScreen,
  setPromptInjection,
  setReconcileWrites,
  setRecallMode,
  setReflectionEvaluator,
  setRunDeadline,
  setSystemPrompt,
  setTool,
  setTopK,
  setToolBudgetOn,
  setTrajectoryRecording,
  setVerifyReads,
  setVisionModel,
  setWriteBack,
  setWriteMinImportance,
} from "./form_model";
import { FallbackChainEditor } from "./widgets/FallbackChainEditor";
import { McpToolPicker, type McpPickerSource } from "./widgets/McpToolPicker";
import { PromptTemplateEditor } from "./widgets/PromptTemplateEditor";

const { Text } = Typography;

/** The named field groups; each maps to one tab in ``ManifestEditor``. */
export type FormSection =
  | "basic"
  | "model"
  | "prompt"
  | "tools"
  | "mcp"
  | "knowledge"
  | "skills"
  | "subagents"
  | "memory"
  | "governance"
  | "defenses";

interface FormViewProps {
  formData: unknown;
  onChange: (data: unknown) => void;
  /** Which field group to render. Defaults to ``basic`` for stand-alone use.
   *  Ignored when ``sections`` is supplied. */
  section?: FormSection;
  /** Stack-render several sections in one pane — the group-nav + detail-pane
   *  layout's "capabilities" group (tools/mcp/knowledge/skills/subagents) is
   *  the main user. Each section is wrapped in a ``data-section-id`` anchor
   *  div with a small sub-section title (the section's own ``manifest_editor
   *  .tab_<section>`` label). Takes precedence over ``section`` when given. */
  sections?: readonly FormSection[];
  /** Where the MCP tab sources servers — ``catalog`` for a platform template,
   *  ``available`` (default) for a tenant agent. */
  mcpSource?: McpPickerSource;
  /** Drop the section heading — used when the section is folded into another
   *  tab (e.g. "basic" merged into a template's "basic info") so there's no
   *  redundant sub-heading. */
  bare?: boolean;
}

const SECTION: React.CSSProperties = { marginBottom: 24 };
const FIELD: React.CSSProperties = { marginBottom: 16 };
const LABEL: React.CSSProperties = { display: "block", marginBottom: 4 };

// Tools the approval gate can require a human verdict for — the base
// capabilities most worth gating (always-on code exec / file writes) plus the
// opt-in network tools. The gate can't remove a capability, only pause it.
const GATEABLE_TOOLS = [
  "exec_python",
  "bash",
  "write_file",
  "edit_file",
  "web_search",
  "http",
  "mcp",
] as const;

function Heading({ children }: { children: React.ReactNode }) {
  return <h3 style={{ fontSize: 15, margin: "0 0 12px" }}>{children}</h3>;
}

export function FormView({
  formData,
  onChange,
  section = "basic",
  sections,
  mcpSource = "available",
  bare = false,
}: FormViewProps) {
  const { t } = useTranslation();
  const [catalog, setCatalog] = useState<ModelCatalog | undefined>(undefined);

  useEffect(() => {
    let alive = true;
    loadModelCatalog().then(
      (c) => {
        if (alive) setCatalog(c);
      },
      () => {
        /* catalog optional — ModelSelect degrades to a disabled/loading select */
      },
    );
    return () => {
      alive = false;
    };
  }, []);

  const tools = readTools(formData);
  const memoryOn = readMemoryOn(formData);
  const approvalTools = readApprovalTools(formData);
  const dynamicWorkersOn = readDynamicWorkersOn(formData);
  const outputSchemaName = readOutputSchemaName(formData);

  const toggleApproval = (name: string, on: boolean): void => {
    const next = on
      ? [...approvalTools, name]
      : approvalTools.filter((t) => t !== name);
    onChange(setApprovalTools(formData, next));
  };

  const sectionsRecord: Record<FormSection, ReactNode> = {
    basic: (
      <section data-testid="af-basic" style={SECTION}>
        {!bare && <Heading>{t("agent_form.section_basic")}</Heading>}
        <div style={FIELD} data-testid="af-name">
          <label style={LABEL}>
            {t("agent_form.field_name")}{" "}
            <span style={{ color: "#ff4d4f" }}>*</span>
            <FieldHelp
              text={t("agent_form.field_name_help")}
              testId="af-name"
            />
          </label>
          <Input
            value={readName(formData)}
            placeholder={t("agent_form.field_name_placeholder")}
            aria-label={t("agent_form.field_name")}
            onChange={(e) => onChange(setName(formData, e.target.value))}
          />
        </div>
        {/* When folded into another tab (``bare``) the description is dropped —
            that tab carries its own description field (no duplicate). */}
        {!bare && (
          <div style={FIELD} data-testid="af-description">
            <label style={LABEL}>
              {t("agent_form.field_description")}
              <FieldHelp
                text={t("agent_form.field_description_help")}
                testId="af-description"
              />
            </label>
            <Input
              value={readDescription(formData)}
              aria-label={t("agent_form.field_description")}
              onChange={(e) =>
                onChange(setDescription(formData, e.target.value))
              }
            />
          </div>
        )}
      </section>
    ),

    model: (
      <>
        <section data-testid="af-model" style={SECTION}>
          <Heading>
            {t("agent_form.section_model")}
            <FieldHelp
              text={t("agent_form.section_model_help")}
              testId="af-model"
            />
          </Heading>
          <ModelSelect
            value={readModel(formData)}
            catalog={catalog}
            onChange={(mdl) => onChange(setModel(formData, mdl))}
          />
        </section>

        {/* E.11 provider fallback chain — only meaningful once a primary model
          is picked. A slow / failing provider falls over to the next instead
          of killing the run (the failure mode this feature exists to prevent). */}
        {!!readModel(formData).provider && !!readModel(formData).name && (
          <section data-testid="af-fallback" style={SECTION}>
            <Heading>
              {t("agent_form.section_fallback")}
              <FieldHelp
                text={t("agent_form.section_fallback_help")}
                testId="af-fallback"
              />
            </Heading>
            <Text type="secondary" style={{ display: "block", marginBottom: 12 }}>
              {t("agent_form.fallback_hint")}
            </Text>
            <FallbackChainEditor
              value={readFallback(formData)}
              catalog={catalog}
              onChange={(chain) => onChange(setFallback(formData, chain))}
            />
          </section>
        )}

        <section data-testid="af-reflection-evaluator" style={SECTION}>
          <Heading>
            {t("agent_form.section_reflection_evaluator")}
            <FieldHelp
              text={t("agent_form.section_reflection_evaluator_help")}
              testId="af-reflection-evaluator"
            />
          </Heading>
          <Text type="secondary" style={{ display: "block", marginBottom: 12 }}>
            {t("agent_form.reflection_evaluator_hint")}
          </Text>
          <ModelSelect
            value={readReflectionEvaluator(formData) ?? {}}
            catalog={catalog}
            onChange={(mdl) => onChange(setReflectionEvaluator(formData, mdl))}
          />
          {readReflectionEvaluatorOn(formData) && (
            <Button
              type="link"
              size="small"
              data-testid="af-reflection-evaluator-clear"
              style={{ paddingLeft: 0 }}
              onClick={() => onChange(setReflectionEvaluator(formData, null))}
            >
              {t("agent_form.reflection_evaluator_clear")}
            </Button>
          )}
        </section>

        {/* Stream J.6 Path B — shown whenever the main model can't see images
          itself (including before one is picked); a separate VL model handles
          image questions via the ask_image tool. Hidden only when the main
          model is itself vision-capable (no fallback needed). */}
        {!readMainSupportsVision(formData) && (
          <section data-testid="af-vision" style={SECTION}>
            <Heading>
              {t("agent_form.section_vision")}
              <FieldHelp
                text={t("agent_form.section_vision_help")}
                testId="af-vision"
              />
            </Heading>
            <Text
              type="secondary"
              style={{ display: "block", marginBottom: 12 }}
            >
              {t("agent_form.vision_hint")}
            </Text>
            <ModelSelect
              visionOnly
              value={readVisionModel(formData) ?? {}}
              catalog={catalog}
              onChange={(mdl) => onChange(setVisionModel(formData, mdl))}
            />
            {readVisionOn(formData) && (
              <Button
                type="link"
                size="small"
                data-testid="af-vision-clear"
                style={{ paddingLeft: 0 }}
                onClick={() => onChange(setVisionModel(formData, null))}
              >
                {t("agent_form.vision_clear")}
              </Button>
            )}
          </section>
        )}
      </>
    ),

    prompt: (
      <>
        <section data-testid="af-prompt" style={SECTION}>
          <Heading>
            {t("agent_form.section_prompt")}
            <FieldHelp
              text={t("agent_form.section_prompt_help")}
              testId="af-prompt"
            />
          </Heading>
          <div data-testid="af-prompt-input">
            {readPromptJinja(formData) ? (
              <PromptTemplateEditor
                value={readSystemPrompt(formData)}
                variables={readPromptVariables(formData)}
                onChange={(v) => onChange(setSystemPrompt(formData, v))}
              />
            ) : (
              <Input.TextArea
                rows={6}
                value={readSystemPrompt(formData)}
                placeholder={t("agent_form.field_prompt_placeholder")}
                aria-label={t("agent_form.section_prompt")}
                onChange={(e) =>
                  onChange(setSystemPrompt(formData, e.target.value))
                }
              />
            )}
          </div>
        </section>
        <PromptVariablesEditor formData={formData} onChange={onChange} />
        {/* Stream RT-1 (RT-ADR-4) — structured final reply. The JSON Schema
            block is authored in the YAML view (a schema editor is out of the
            curated form's scope); the form surfaces the state + help copy. */}
        <section data-testid="af-output-schema" style={SECTION}>
          <Heading>
            {t("agent_form.section_output_schema")}
            <FieldHelp
              text={t("agent_form.section_output_schema_help")}
              testId="af-output-schema"
            />
          </Heading>
          <Text type="secondary">
            {outputSchemaName
              ? t("agent_form.output_schema_on_hint", { name: outputSchemaName })
              : t("agent_form.output_schema_off_hint")}
          </Text>
        </section>
      </>
    ),

    tools: (
      <section data-testid="af-tools" style={SECTION}>
        <Heading>
          {t("agent_form.section_tools")}
          <FieldHelp
            text={t("agent_form.section_tools_help")}
            testId="af-tools"
          />
        </Heading>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <span>
            <Checkbox
              data-testid="af-tool-web_search"
              checked={tools.webSearch}
              onChange={(e) =>
                onChange(setTool(formData, "webSearch", e.target.checked))
              }
            >
              {t("agent_form.tool_web_search")}
            </Checkbox>
            <FieldHelp
              text={t("agent_form.tool_web_search_help")}
              testId="af-tool-web_search"
            />
          </span>
          <span>
            <Checkbox
              data-testid="af-tool-http"
              checked={tools.http}
              onChange={(e) =>
                onChange(setTool(formData, "http", e.target.checked))
              }
            >
              {t("agent_form.tool_http")}
            </Checkbox>
            <FieldHelp
              text={t("agent_form.tool_http_help")}
              testId="af-tool-http"
            />
          </span>
        </div>
      </section>
    ),

    mcp: (
      <section data-testid="af-mcp" style={SECTION}>
        <Heading>
          {t("agent_form.section_mcp")}
          <FieldHelp text={t("agent_form.section_mcp_help")} testId="af-mcp" />
        </Heading>
        <McpToolPicker
          source={mcpSource}
          servers={tools.mcpServers}
          allowTools={tools.mcpAllowTools}
          onChange={(nextServers, nextAllow) =>
            onChange(setMcp(formData, nextServers, nextAllow))
          }
        />
      </section>
    ),

    knowledge: <KnowledgePicker formData={formData} onChange={onChange} />,
    skills: <SkillPicker formData={formData} onChange={onChange} />,
    subagents: <SubagentPicker formData={formData} onChange={onChange} />,

    memory: (
      <section data-testid="af-memory" style={SECTION}>
        <Heading>
          {t("agent_form.section_memory")}
          <FieldHelp
            text={t("agent_form.section_memory_help")}
            testId="af-memory"
          />
        </Heading>
        <label
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            marginBottom: 8,
          }}
        >
          <Switch
            checked={memoryOn}
            data-testid="af-memory-toggle"
            aria-label={t("agent_form.section_memory")}
            onChange={(on) => onChange(setMemoryOn(formData, on))}
          />
          <Text type="secondary">{t("agent_form.memory_hint")}</Text>
        </label>
        {memoryOn && (
          <>
            <div style={FIELD} data-testid="af-topk">
              <label style={LABEL}>
                {t("agent_form.memory_topk")}
                <FieldHelp
                  text={t("agent_form.memory_topk_help")}
                  testId="af-topk"
                />
              </label>
              <InputNumber
                min={1}
                value={readTopK(formData) ?? 5}
                aria-label={t("agent_form.memory_topk")}
                onChange={(v) => onChange(setTopK(formData, v ?? 5))}
              />
            </div>
            <label
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                marginBottom: 8,
              }}
            >
              <Switch
                checked={readWriteBack(formData)}
                data-testid="af-memory-writeback"
                aria-label={t("agent_form.memory_write_back")}
                onChange={(on) => onChange(setWriteBack(formData, on))}
              />
              <Text type="secondary">
                {t("agent_form.memory_write_back")}
                <FieldHelp
                  text={t("agent_form.memory_write_back_help")}
                  testId="af-memory-writeback"
                />
              </Text>
            </label>
            <Collapse
              data-testid="af-memory-advanced"
              defaultActiveKey={[]}
              items={[
                {
                  key: "advanced",
                  label: t("agent_form.section_advanced"),
                  children: (
                    <>
                      <label
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 8,
                          marginBottom: 12,
                        }}
                      >
                        <Switch
                          checked={readVerifyReads(formData)}
                          data-testid="af-memory-verify-reads"
                          aria-label={t("agent_form.memory_verify_reads")}
                          onChange={(on) =>
                            onChange(setVerifyReads(formData, on))
                          }
                        />
                        <Text type="secondary">
                          {t("agent_form.memory_verify_reads")}
                          <FieldHelp
                            text={t("agent_form.memory_verify_reads_help")}
                            testId="af-memory-verify-reads"
                          />
                        </Text>
                      </label>
                      <div style={FIELD} data-testid="af-memory-min-importance">
                        <label style={LABEL}>
                          {t("agent_form.memory_write_min_importance")}
                          <FieldHelp
                            text={t(
                              "agent_form.memory_write_min_importance_help",
                            )}
                            testId="af-memory-min-importance"
                          />
                        </label>
                        <InputNumber
                          min={0}
                          max={1}
                          step={0.05}
                          value={readWriteMinImportance(formData)}
                          aria-label={t(
                            "agent_form.memory_write_min_importance",
                          )}
                          onChange={(v) =>
                            onChange(setWriteMinImportance(formData, v ?? 0))
                          }
                        />
                      </div>
                      <label
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 8,
                          marginBottom: 12,
                        }}
                      >
                        <Switch
                          checked={readReconcileWrites(formData)}
                          data-testid="af-memory-reconcile"
                          aria-label={t("agent_form.memory_reconcile_writes")}
                          onChange={(on) =>
                            onChange(setReconcileWrites(formData, on))
                          }
                        />
                        <Text type="secondary">
                          {t("agent_form.memory_reconcile_writes")}
                          <FieldHelp
                            text={t("agent_form.memory_reconcile_writes_help")}
                            testId="af-memory-reconcile"
                          />
                        </Text>
                      </label>
                      <div style={FIELD} data-testid="af-memory-recall-mode">
                        <label style={LABEL}>
                          {t("agent_form.memory_recall_mode")}
                          <FieldHelp
                            text={t("agent_form.memory_recall_mode_help")}
                            testId="af-memory-recall-mode"
                          />
                        </label>
                        <Select
                          style={{ width: "100%" }}
                          value={readRecallMode(formData)}
                          aria-label={t("agent_form.memory_recall_mode")}
                          onChange={(v) => onChange(setRecallMode(formData, v))}
                          options={[
                            {
                              value: "per_session",
                              label: t("agent_form.memory_recall_per_session"),
                            },
                            {
                              value: "per_turn",
                              label: t("agent_form.memory_recall_per_turn"),
                            },
                          ]}
                        />
                      </div>
                    </>
                  ),
                },
              ]}
            />
          </>
        )}
      </section>
    ),

    governance: (
      <>
        <section data-testid="af-approval" style={SECTION}>
          <Heading>
            {t("agent_form.section_approval")}
            <FieldHelp
              text={t("agent_form.section_approval_help")}
              testId="af-approval"
            />
          </Heading>
          <Text type="secondary" style={{ display: "block", marginBottom: 12 }}>
            {t("agent_form.approval_hint")}
          </Text>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {GATEABLE_TOOLS.map((name) => (
              <span key={name}>
                <Checkbox
                  data-testid={`af-approval-${name}`}
                  checked={approvalTools.includes(name)}
                  onChange={(e) => toggleApproval(name, e.target.checked)}
                >
                  {name}
                </Checkbox>
              </span>
            ))}
          </div>
        </section>

        <section data-testid="af-dynamic-workers" style={SECTION}>
          <Heading>
            {t("agent_form.section_dynamic_workers")}
            <FieldHelp
              text={t("agent_form.section_dynamic_workers_help")}
              testId="af-dynamic-workers"
            />
          </Heading>
          <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <Switch
              checked={dynamicWorkersOn}
              data-testid="af-dynamic-workers-toggle"
              aria-label={t("agent_form.section_dynamic_workers")}
              onChange={(on) => onChange(setDynamicWorkersOn(formData, on))}
            />
            <Text type="secondary">{t("agent_form.dynamic_workers_hint")}</Text>
          </label>
        </section>

        <section data-testid="af-run-deadline" style={SECTION}>
          <Heading>
            {t("agent_form.section_run_deadline")}
            <FieldHelp
              text={t("agent_form.section_run_deadline_help")}
              testId="af-run-deadline"
            />
          </Heading>
          <Text type="secondary" style={{ display: "block", marginBottom: 8 }}>
            {t("agent_form.run_deadline_hint")}
          </Text>
          <InputNumber
            min={0}
            max={86400}
            value={readRunDeadline(formData)}
            aria-label={t("agent_form.section_run_deadline")}
            onChange={(v) => onChange(setRunDeadline(formData, v ?? 0))}
          />
        </section>

        <Collapse
          data-testid="af-governance-advanced"
          defaultActiveKey={[]}
          items={[
            {
              key: "advanced",
              label: t("agent_form.section_advanced"),
              children: (
                <>
                  <div style={FIELD} data-testid="af-approval-timeout">
                    <label style={LABEL}>
                      {t("agent_form.approval_timeout")}
                      <FieldHelp
                        text={t("agent_form.approval_timeout_help")}
                        testId="af-approval-timeout"
                      />
                    </label>
                    <InputNumber
                      min={60}
                      max={604800}
                      value={readApprovalTimeout(formData)}
                      aria-label={t("agent_form.approval_timeout")}
                      onChange={(v) =>
                        onChange(setApprovalTimeout(formData, v ?? 86400))
                      }
                    />
                  </div>
                  <label
                    style={{ display: "flex", alignItems: "center", gap: 8 }}
                  >
                    <Switch
                      checked={readTrajectoryRecording(formData)}
                      data-testid="af-trajectory-recording"
                      aria-label={t("agent_form.trajectory_recording")}
                      onChange={(on) =>
                        onChange(setTrajectoryRecording(formData, on))
                      }
                    />
                    <Text type="secondary">
                      {t("agent_form.trajectory_recording")}
                      <FieldHelp
                        text={t("agent_form.trajectory_recording_help")}
                        testId="af-trajectory-recording"
                      />
                    </Text>
                  </label>
                  <label
                    style={{ display: "flex", alignItems: "center", gap: 8 }}
                  >
                    <Switch
                      checked={readToolBudgetOn(formData)}
                      data-testid="af-tool-budget"
                      aria-label={t("agent_form.tool_budget")}
                      onChange={(on) =>
                        onChange(setToolBudgetOn(formData, on))
                      }
                    />
                    <Text type="secondary">
                      {t("agent_form.tool_budget")}
                      <FieldHelp
                        text={t("agent_form.tool_budget_help")}
                        testId="af-tool-budget"
                      />
                    </Text>
                  </label>
                </>
              ),
            },
          ]}
        />
      </>
    ),

    defenses: (
      <section data-testid="af-defenses" style={SECTION}>
        <Heading>
          {t("agent_form.section_defenses")}
          <FieldHelp
            text={t("agent_form.section_defenses_help")}
            testId="af-defenses"
          />
        </Heading>

        {readExtends(formData) !== undefined && (
          <div data-testid="af-defenses-extends-note" style={FIELD}>
            <Alert
              type="info"
              showIcon
              message={t("agent_form.defenses_extends_note")}
            />
          </div>
        )}

        {/* 输入防护 */}
        <Text type="secondary" style={{ display: "block", margin: "0 0 8px" }}>
          {t("agent_form.defenses_group_input")}
        </Text>
        <div style={FIELD} data-testid="af-defenses-prompt-injection">
          <label style={LABEL}>
            {t("agent_form.defenses_prompt_injection")}
            <FieldHelp
              text={t("agent_form.defenses_prompt_injection_help")}
              testId="af-defenses-prompt-injection"
            />
          </label>
          <Switch
            checked={readPromptInjection(formData) === "spotlight"}
            onChange={(on) =>
              onChange(setPromptInjection(formData, on ? "spotlight" : "off"))
            }
          />
          {readPromptInjection(formData) === "off" && (
            <Alert
              type="warning"
              showIcon
              style={{ marginTop: 8 }}
              message={t("agent_form.defenses_prompt_injection_off_warn")}
            />
          )}
        </div>

        {/* 输出防护 */}
        <Text type="secondary" style={{ display: "block", margin: "16px 0 8px" }}>
          {t("agent_form.defenses_group_output")}
        </Text>
        <div style={FIELD} data-testid="af-defenses-output-screen">
          <label style={LABEL}>
            {t("agent_form.defenses_output_screen")}
            <FieldHelp
              text={t("agent_form.defenses_output_screen_help")}
              testId="af-defenses-output-screen"
            />
          </label>
          <Switch
            checked={readOutputScreen(formData) === "block"}
            onChange={(on) =>
              onChange(setOutputScreen(formData, on ? "block" : "off"))
            }
          />
          {readOutputScreen(formData) === "off" && (
            <Alert
              type="warning"
              showIcon
              style={{ marginTop: 8 }}
              message={t("agent_form.defenses_output_screen_off_warn")}
            />
          )}
        </div>

        <div style={FIELD} data-testid="af-defenses-output-judge">
          <label style={LABEL}>
            {t("agent_form.defenses_output_judge")}
            <FieldHelp
              text={t("agent_form.defenses_output_judge_help")}
              testId="af-defenses-output-judge"
            />
          </label>
          <Switch
            checked={readOutputJudge(formData) === "block"}
            onChange={(on) =>
              onChange(setOutputJudge(formData, on ? "block" : "off"))
            }
          />
          {readOutputJudge(formData) === "block" && (
            <div style={{ marginTop: 8 }}>
              <Alert
                type="warning"
                showIcon
                style={{ marginBottom: 8 }}
                message={t("agent_form.defenses_output_judge_on_warn")}
              />
              <label style={LABEL}>
                {t("agent_form.defenses_output_judge_on_error")}
              </label>
              <Select
                data-testid="af-defenses-output-judge-on-error"
                style={{ width: 240 }}
                value={readOutputJudgeOnError(formData)}
                onChange={(v) =>
                  onChange(
                    setOutputJudgeOnError(
                      formData,
                      v as "open" | "closed",
                    ),
                  )
                }
                options={[
                  { value: "open", label: t("agent_form.defenses_on_error_open") },
                  { value: "closed", label: t("agent_form.defenses_on_error_closed") },
                ]}
              />
            </div>
          )}
        </div>

        <div style={FIELD} data-testid="af-defenses-output-dlp">
          <label style={LABEL}>
            {t("agent_form.defenses_output_dlp")}
            <FieldHelp
              text={t("agent_form.defenses_output_dlp_help")}
              testId="af-defenses-output-dlp"
            />
          </label>
          <Switch
            checked={readOutputDlp(formData) === "redact"}
            onChange={(on) =>
              onChange(setOutputDlp(formData, on ? "redact" : "off"))
            }
          />
          {readOutputDlp(formData) === "redact" && (
            <Alert
              type="info"
              showIcon
              style={{ marginTop: 8 }}
              message={t("agent_form.defenses_output_dlp_on_note")}
            />
          )}
        </div>

        {/* 工具行为防护 */}
        <Text type="secondary" style={{ display: "block", margin: "16px 0 8px" }}>
          {t("agent_form.defenses_group_action")}
        </Text>
        <div style={FIELD} data-testid="af-defenses-action-screen">
          <label style={LABEL}>
            {t("agent_form.defenses_action_screen")}
            <FieldHelp
              text={t("agent_form.defenses_action_screen_help")}
              testId="af-defenses-action-screen"
            />
          </label>
          <Select
            data-testid="af-defenses-action-screen-select"
            style={{ width: 240 }}
            value={readActionScreen(formData)}
            onChange={(v) =>
              onChange(
                setActionScreen(
                  formData,
                  v as "off" | "block" | "approval",
                ),
              )
            }
            options={[
              { value: "off", label: t("agent_form.defenses_action_screen_off") },
              { value: "block", label: t("agent_form.defenses_action_screen_block") },
              { value: "approval", label: t("agent_form.defenses_action_screen_approval") },
            ]}
          />
          {readActionScreen(formData) !== "off" && (
            <div style={{ marginTop: 8 }}>
              <Alert
                type="info"
                showIcon
                style={{ marginBottom: 8 }}
                message={t("agent_form.defenses_action_screen_on_note")}
              />
              <label style={LABEL}>
                {t("agent_form.defenses_action_screen_on_error")}
              </label>
              <Select
                data-testid="af-defenses-action-screen-on-error"
                style={{ width: 240 }}
                value={readActionScreenOnError(formData)}
                onChange={(v) =>
                  onChange(
                    setActionScreenOnError(
                      formData,
                      v as "open" | "closed",
                    ),
                  )
                }
                options={[
                  { value: "open", label: t("agent_form.defenses_on_error_open") },
                  { value: "closed", label: t("agent_form.defenses_on_error_closed") },
                ]}
              />
            </div>
          )}
        </div>
      </section>
    ),
  };

  return (
    <div data-testid="manifest-form-view" style={{ maxWidth: 760 }}>
      {sections
        ? sections.map((s) => (
            <div key={s} data-section-id={s}>
              <Text
                strong
                type="secondary"
                style={{ display: "block", fontSize: 13, margin: "0 0 8px" }}
              >
                {t(`manifest_editor.tab_${s}`)}
              </Text>
              {sectionsRecord[s]}
            </div>
          ))
        : sectionsRecord[section]}
    </div>
  );
}
