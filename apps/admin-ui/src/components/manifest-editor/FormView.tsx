/**
 * Curated agent form — a hand-built view over the canonical fields of an agent
 * manifest (basics / model / system prompt / long-term memory / tools). Unlike
 * the old RJSF schema dump, every control emits the FULL merged manifest via the
 * form_model writers, so non-curated fields a user hand-added in raw YAML are
 * preserved across a Form round-trip. The model catalog (configured providers +
 * models) is loaded once and handed to ModelSelect.
 */
import { useEffect, useState } from "react";
import { Button, Checkbox, Input, InputNumber, Switch, Typography } from "antd";
import { useTranslation } from "react-i18next";

import type { ModelCatalog } from "../../api/model_catalog";
import { loadModelCatalog } from "./catalog";
import { ModelSelect } from "./widgets/ModelSelect";
import {
  readDescription,
  readMemoryOn,
  readModel,
  readName,
  readReflectionEvaluator,
  readReflectionEvaluatorOn,
  readSystemPrompt,
  readTools,
  readTopK,
  setDescription,
  setMcpAllowTools,
  setMcpServers,
  setMemoryOn,
  setModel,
  setName,
  setReflectionEvaluator,
  setSystemPrompt,
  setTool,
  setTopK,
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

  return (
    <div data-testid="manifest-form-view" style={{ maxWidth: 560 }}>
      <section data-testid="af-basic" style={SECTION}>
        <Heading>{t("agent_form.section_basic")}</Heading>
        <div style={FIELD} data-testid="af-name">
          <label style={LABEL}>
            {t("agent_form.field_name")} <span style={{ color: "#ff4d4f" }}>*</span>
          </label>
          <Input
            value={readName(formData)}
            placeholder={t("agent_form.field_name_placeholder")}
            aria-label={t("agent_form.field_name")}
            onChange={(e) => onChange(setName(formData, e.target.value))}
          />
        </div>
        <div style={FIELD} data-testid="af-description">
          <label style={LABEL}>{t("agent_form.field_description")}</label>
          <Input
            value={readDescription(formData)}
            aria-label={t("agent_form.field_description")}
            onChange={(e) => onChange(setDescription(formData, e.target.value))}
          />
        </div>
      </section>

      <section data-testid="af-model" style={SECTION}>
        <Heading>{t("agent_form.section_model")}</Heading>
        <ModelSelect
          value={readModel(formData)}
          catalog={catalog}
          onChange={(mdl) => onChange(setModel(formData, mdl))}
        />
      </section>

      <section data-testid="af-prompt" style={SECTION}>
        <Heading>{t("agent_form.section_prompt")}</Heading>
        <div data-testid="af-prompt-input">
          <Input.TextArea
            rows={6}
            value={readSystemPrompt(formData)}
            placeholder={t("agent_form.field_prompt_placeholder")}
            aria-label={t("agent_form.section_prompt")}
            onChange={(e) => onChange(setSystemPrompt(formData, e.target.value))}
          />
        </div>
      </section>

      <section data-testid="af-memory" style={SECTION}>
        <Heading>{t("agent_form.section_memory")}</Heading>
        <label style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
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
            <label style={LABEL}>{t("agent_form.memory_topk")}</label>
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
        <Heading>{t("agent_form.section_reflection_evaluator")}</Heading>
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

      <section data-testid="af-tools" style={SECTION}>
        <Heading>{t("agent_form.section_tools")}</Heading>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <Checkbox
            data-testid="af-tool-web_search"
            checked={tools.webSearch}
            onChange={(e) => onChange(setTool(formData, "webSearch", e.target.checked))}
          >
            {t("agent_form.tool_web_search")}
          </Checkbox>
          <Checkbox
            data-testid="af-tool-http"
            checked={tools.http}
            onChange={(e) => onChange(setTool(formData, "http", e.target.checked))}
          >
            {t("agent_form.tool_http")}
          </Checkbox>
          <Checkbox
            data-testid="af-tool-mcp"
            checked={tools.mcp}
            onChange={(e) => onChange(setTool(formData, "mcp", e.target.checked))}
          >
            {t("agent_form.tool_mcp")}
          </Checkbox>
        </div>
        {tools.mcp && (
          <McpToolPicker
            servers={tools.mcpServers}
            allowTools={tools.mcpAllowTools}
            onServersChange={(next) => onChange(setMcpServers(formData, next))}
            onAllowToolsChange={(next) => onChange(setMcpAllowTools(formData, next))}
          />
        )}
      </section>
    </div>
  );
}
