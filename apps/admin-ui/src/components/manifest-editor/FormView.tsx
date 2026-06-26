/**
 * Curated agent form — a hand-built view over the canonical fields of an agent
 * manifest (basics / model / system prompt / long-term memory / tools). Unlike
 * the old RJSF schema dump, every control emits the FULL merged manifest via the
 * form_model writers, so non-curated fields a user hand-added in raw YAML are
 * preserved across a Form round-trip. The model catalog (configured providers +
 * models) is loaded once and handed to ModelSelect.
 */
import { useEffect, useState } from "react";
import {
  Button,
  Checkbox,
  Collapse,
  Input,
  InputNumber,
  Switch,
  Typography,
} from "antd";
import { useTranslation } from "react-i18next";

import type { ModelCatalog } from "../../api/model_catalog";
import { FieldHelp } from "../FieldHelp";
import { CapabilityPickers } from "./CapabilityPickers";
import { PromptVariablesEditor } from "./PromptVariablesEditor";
import { loadModelCatalog } from "./catalog";
import { ModelSelect } from "./widgets/ModelSelect";
import {
  readApprovalTools,
  readDescription,
  readDynamicWorkersOn,
  readMainSupportsVision,
  readMemoryOn,
  readModel,
  readName,
  readReflectionEvaluator,
  readReflectionEvaluatorOn,
  readSystemPrompt,
  readTools,
  readTopK,
  readVisionModel,
  readVisionOn,
  setApprovalTools,
  setDescription,
  setDynamicWorkersOn,
  setMcpAllowTools,
  setMcpServers,
  setMemoryOn,
  setModel,
  setName,
  setReflectionEvaluator,
  setSystemPrompt,
  setTool,
  setTopK,
  setVisionModel,
} from "./form_model";
import { McpToolPicker } from "./widgets/McpToolPicker";

const { Text } = Typography;

interface FormViewProps {
  formData: unknown;
  onChange: (data: unknown) => void;
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

export function FormView({ formData, onChange }: FormViewProps) {
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

  const toggleApproval = (name: string, on: boolean): void => {
    const next = on
      ? [...approvalTools, name]
      : approvalTools.filter((t) => t !== name);
    onChange(setApprovalTools(formData, next));
  };

  return (
    <div data-testid="manifest-form-view" style={{ maxWidth: 560 }}>
      <section data-testid="af-basic" style={SECTION}>
        <Heading>{t("agent_form.section_basic")}</Heading>
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
            onChange={(e) => onChange(setDescription(formData, e.target.value))}
          />
        </div>
      </section>

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

      <section data-testid="af-prompt" style={SECTION}>
        <Heading>
          {t("agent_form.section_prompt")}
          <FieldHelp
            text={t("agent_form.section_prompt_help")}
            testId="af-prompt"
          />
        </Heading>
        <div data-testid="af-prompt-input">
          <Input.TextArea
            rows={6}
            value={readSystemPrompt(formData)}
            placeholder={t("agent_form.field_prompt_placeholder")}
            aria-label={t("agent_form.section_prompt")}
            onChange={(e) =>
              onChange(setSystemPrompt(formData, e.target.value))
            }
          />
        </div>
      </section>

      <PromptVariablesEditor formData={formData} onChange={onChange} />

      <Collapse
        ghost
        style={{ marginTop: 4 }}
        data-testid="af-advanced"
        items={[
          {
            key: "advanced",
            label: t("agent_form.section_advanced"),
            forceRender: true,
            children: (
              <>
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
                  )}
                </section>

                <section data-testid="af-reflection-evaluator" style={SECTION}>
                  <Heading>
                    {t("agent_form.section_reflection_evaluator")}
                    <FieldHelp
                      text={t("agent_form.section_reflection_evaluator_help")}
                      testId="af-reflection-evaluator"
                    />
                  </Heading>
                  <Text
                    type="secondary"
                    style={{ display: "block", marginBottom: 12 }}
                  >
                    {t("agent_form.reflection_evaluator_hint")}
                  </Text>
                  <ModelSelect
                    value={readReflectionEvaluator(formData) ?? {}}
                    catalog={catalog}
                    onChange={(mdl) =>
                      onChange(setReflectionEvaluator(formData, mdl))
                    }
                  />
                  {readReflectionEvaluatorOn(formData) && (
                    <Button
                      type="link"
                      size="small"
                      data-testid="af-reflection-evaluator-clear"
                      style={{ paddingLeft: 0 }}
                      onClick={() =>
                        onChange(setReflectionEvaluator(formData, null))
                      }
                    >
                      {t("agent_form.reflection_evaluator_clear")}
                    </Button>
                  )}
                </section>

                {/* Stream J.6 Path B — only when the main model can't see images itself:
          a separate VL model handles image questions via the ask_image tool. */}
                {readModel(formData).name !== undefined &&
                  !readMainSupportsVision(formData) && (
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
                        value={readVisionModel(formData) ?? {}}
                        catalog={catalog}
                        onChange={(mdl) =>
                          onChange(setVisionModel(formData, mdl))
                        }
                      />
                      {readVisionOn(formData) && (
                        <Button
                          type="link"
                          size="small"
                          data-testid="af-vision-clear"
                          style={{ paddingLeft: 0 }}
                          onClick={() =>
                            onChange(setVisionModel(formData, null))
                          }
                        >
                          {t("agent_form.vision_clear")}
                        </Button>
                      )}
                    </section>
                  )}

                <section data-testid="af-tools" style={SECTION}>
                  <Heading>
                    {t("agent_form.section_tools")}
                    <FieldHelp
                      text={t("agent_form.section_tools_help")}
                      testId="af-tools"
                    />
                  </Heading>
                  <div
                    style={{ display: "flex", flexDirection: "column", gap: 8 }}
                  >
                    <span>
                      <Checkbox
                        data-testid="af-tool-web_search"
                        checked={tools.webSearch}
                        onChange={(e) =>
                          onChange(
                            setTool(formData, "webSearch", e.target.checked),
                          )
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
                    <span>
                      <Checkbox
                        data-testid="af-tool-mcp"
                        checked={tools.mcp}
                        onChange={(e) =>
                          onChange(setTool(formData, "mcp", e.target.checked))
                        }
                      >
                        {t("agent_form.tool_mcp")}
                      </Checkbox>
                      <FieldHelp
                        text={t("agent_form.tool_mcp_help")}
                        testId="af-tool-mcp"
                      />
                    </span>
                  </div>
                  {tools.mcp && (
                    <McpToolPicker
                      servers={tools.mcpServers}
                      allowTools={tools.mcpAllowTools}
                      onServersChange={(next) =>
                        onChange(setMcpServers(formData, next))
                      }
                      onAllowToolsChange={(next) =>
                        onChange(setMcpAllowTools(formData, next))
                      }
                    />
                  )}
                </section>

                <section data-testid="af-approval" style={SECTION}>
                  <Heading>
                    {t("agent_form.section_approval")}
                    <FieldHelp
                      text={t("agent_form.section_approval_help")}
                      testId="af-approval"
                    />
                  </Heading>
                  <Text
                    type="secondary"
                    style={{ display: "block", marginBottom: 12 }}
                  >
                    {t("agent_form.approval_hint")}
                  </Text>
                  <div
                    style={{ display: "flex", flexDirection: "column", gap: 8 }}
                  >
                    {GATEABLE_TOOLS.map((name) => (
                      <span key={name}>
                        <Checkbox
                          data-testid={`af-approval-${name}`}
                          checked={approvalTools.includes(name)}
                          onChange={(e) =>
                            toggleApproval(name, e.target.checked)
                          }
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
                  <label
                    style={{ display: "flex", alignItems: "center", gap: 8 }}
                  >
                    <Switch
                      checked={dynamicWorkersOn}
                      data-testid="af-dynamic-workers-toggle"
                      aria-label={t("agent_form.section_dynamic_workers")}
                      onChange={(on) =>
                        onChange(setDynamicWorkersOn(formData, on))
                      }
                    />
                    <Text type="secondary">
                      {t("agent_form.dynamic_workers_hint")}
                    </Text>
                  </label>
                </section>

                <CapabilityPickers formData={formData} onChange={onChange} />
              </>
            ),
          },
        ]}
      />
    </div>
  );
}
