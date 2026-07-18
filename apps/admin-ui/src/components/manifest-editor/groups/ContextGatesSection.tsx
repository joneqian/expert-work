/**
 * ContextGatesSection — "上下文与压缩" (Context & Compression) group, Task 3
 * of the agent-config-page redesign (PR2). Visualizes the three sequential
 * context gates (PolicySpec.tool_result_prune → .working_memory →
 * .context_compression) plus the sibling tool-output-budget master switch,
 * as four collapsible panels (18 knobs total) — the pending-hint placeholder
 * this group showed since Phase 1/PR1 is now real.
 *
 * Structure: an intro ``Text`` explaining the three-gate order, then an antd
 * ``Collapse`` with one panel per PolicySpec sub-block. The first panel
 * (tool-result prune, the cheapest/first gate) is expanded by default per
 * spec ("防疲劳" — don't dump all 18 fields open at once); the other three
 * start collapsed. Every panel uses ``forceRender`` so all 18 fields mount
 * regardless of collapse state (collapsed panels are hidden via antd's own
 * motion/height, not unmounted) — needed for both testability and so a
 * browser "find in page" can reach a collapsed field's copy.
 *
 * Each panel's field array is a ``FieldDef`` table (Task 1's declarative
 * pattern, same as ``RunBudgetSection``) rendered via ``PolicyFieldList``;
 * this component only wires the ``readContextGates``/``patchContextGates``
 * pair (form_model.ts, Task 2) to it. ``kind: "number"`` fields with
 * ``effectiveDefault: null`` (max_turns / max_tokens) render as an empty
 * InputNumber when unset — unset IS the feature-off state (a coarse legacy
 * cap that predates the three gates), not "falls back to a numeric default".
 */
import { Collapse, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { PolicyFieldList, type FieldDef } from "./field_defs";
import {
  patchContextGates,
  readContextGates,
  type ContextGatesFields,
} from "../form_model";

const { Text } = Typography;

interface ContextGatesSectionProps {
  formData: unknown;
  onChange: (data: unknown) => void;
}

// ① tool_result_prune — the cheapest, first-line gate: collapses old tool
// results into a reference. i18nKey prefix ``pr_`` mirrors ContextGatesFields'
// ``pr*`` value keys.
const TOOL_RESULT_PRUNE_DEFS: readonly FieldDef[] = [
  {
    fieldId: "policies.tool_result_prune.enabled",
    i18nKey: "context_gates.pr_enabled",
    valueKey: "prEnabled",
    kind: "switch",
    effectiveDefault: true,
  },
  {
    fieldId: "policies.tool_result_prune.threshold_pct",
    i18nKey: "context_gates.pr_threshold_pct",
    valueKey: "prThresholdPct",
    kind: "percent",
    effectiveDefault: 0.7,
  },
  {
    fieldId: "policies.tool_result_prune.recent_tool_results_kept",
    i18nKey: "context_gates.pr_recent_kept",
    valueKey: "prRecentKept",
    kind: "number",
    effectiveDefault: 4,
    min: 0,
  },
];

// ② working_memory — the second gate: trims to first turn + N recent turns,
// no LLM call. i18nKey prefix ``wm_``.
const WORKING_MEMORY_DEFS: readonly FieldDef[] = [
  {
    fieldId: "policies.working_memory.enabled",
    i18nKey: "context_gates.wm_enabled",
    valueKey: "wmEnabled",
    kind: "switch",
    effectiveDefault: true,
  },
  {
    fieldId: "policies.working_memory.threshold_pct",
    i18nKey: "context_gates.wm_threshold_pct",
    valueKey: "wmThresholdPct",
    kind: "percent",
    effectiveDefault: 0.7,
  },
  {
    fieldId: "policies.working_memory.max_recent_turns",
    i18nKey: "context_gates.wm_max_recent_turns",
    valueKey: "wmMaxRecentTurns",
    kind: "number",
    effectiveDefault: 20,
    min: 1,
  },
  {
    fieldId: "policies.working_memory.keep_first_turn",
    i18nKey: "context_gates.wm_keep_first_turn",
    valueKey: "wmKeepFirstTurn",
    kind: "switch",
    effectiveDefault: true,
  },
];

// ③ context_compression — the last gate: an LLM summarizes the dropped
// middle. i18nKey prefix ``cc_``. ``max_turns``/``max_tokens`` are the old
// coarse pre-three-gates cap (effectiveDefault null = unset means off, never
// a numeric fallback).
const CONTEXT_COMPRESSION_DEFS: readonly FieldDef[] = [
  {
    fieldId: "policies.context_compression.enabled",
    i18nKey: "context_gates.cc_enabled",
    valueKey: "ccEnabled",
    kind: "switch",
    effectiveDefault: true,
  },
  {
    fieldId: "policies.context_compression.threshold_pct",
    i18nKey: "context_gates.cc_threshold_pct",
    valueKey: "ccThresholdPct",
    kind: "percent",
    effectiveDefault: 0.7,
  },
  {
    fieldId: "policies.context_compression.head_keep",
    i18nKey: "context_gates.cc_head_keep",
    valueKey: "ccHeadKeep",
    kind: "number",
    effectiveDefault: 4,
    min: 0,
  },
  {
    fieldId: "policies.context_compression.tail_keep",
    i18nKey: "context_gates.cc_tail_keep",
    valueKey: "ccTailKeep",
    kind: "number",
    effectiveDefault: 6,
    min: 0,
  },
  {
    fieldId: "policies.context_compression.flush_before_compaction",
    i18nKey: "context_gates.cc_flush_before_compaction",
    valueKey: "ccFlushBeforeCompaction",
    kind: "switch",
    effectiveDefault: true,
  },
  {
    fieldId: "policies.context_compression.max_passes",
    i18nKey: "context_gates.cc_max_passes",
    valueKey: "ccMaxPasses",
    kind: "number",
    effectiveDefault: 3,
    min: 1,
  },
  {
    fieldId: "policies.context_compression.max_turns",
    i18nKey: "context_gates.cc_max_turns",
    valueKey: "ccMaxTurns",
    kind: "number",
    effectiveDefault: null,
    min: 1,
  },
  {
    fieldId: "policies.context_compression.max_tokens",
    i18nKey: "context_gates.cc_max_tokens",
    valueKey: "ccMaxTokens",
    kind: "number",
    effectiveDefault: null,
    min: 1,
  },
  {
    fieldId: "policies.context_compression.pressure_feedback",
    i18nKey: "context_gates.cc_pressure_feedback",
    valueKey: "ccPressureFeedback",
    kind: "switch",
    effectiveDefault: true,
  },
  {
    fieldId: "policies.context_compression.pressure_warn_pct",
    i18nKey: "context_gates.cc_pressure_warn_pct",
    valueKey: "ccPressureWarnPct",
    kind: "percent",
    effectiveDefault: 0.75,
  },
];

// ④ tool_output_budget — sibling of the three gates (not itself a context
// gate, but the same "policies" block family): per-agent master switch for
// the tool-output-budget feature. i18nKey prefix ``budget_``.
const TOOL_OUTPUT_BUDGET_DEFS: readonly FieldDef[] = [
  {
    fieldId: "policies.tool_output_budget.enabled",
    i18nKey: "context_gates.budget_enabled",
    valueKey: "budgetEnabled",
    kind: "switch",
    effectiveDefault: true,
  },
];

export function ContextGatesSection({
  formData,
  onChange,
}: ContextGatesSectionProps) {
  const { t } = useTranslation();
  const gates = readContextGates(formData);
  const values = gates as Record<string, number | boolean | undefined>;

  const handlePatch = (patch: Partial<ContextGatesFields>): void => {
    onChange(patchContextGates(formData, patch));
  };

  return (
    <div data-testid="context-gates-section" style={{ maxWidth: 760 }}>
      <Text type="secondary" style={{ display: "block", marginBottom: 16 }}>
        {t("context_gates.group_intro")}
      </Text>
      <Collapse
        defaultActiveKey={["tool_result_prune"]}
        items={[
          {
            key: "tool_result_prune",
            label: t("context_gates.panel_tool_result_prune"),
            forceRender: true,
            children: (
              <PolicyFieldList
                defs={TOOL_RESULT_PRUNE_DEFS}
                values={values}
                onPatch={handlePatch}
              />
            ),
          },
          {
            key: "working_memory",
            label: t("context_gates.panel_working_memory"),
            forceRender: true,
            children: (
              <PolicyFieldList
                defs={WORKING_MEMORY_DEFS}
                values={values}
                onPatch={handlePatch}
              />
            ),
          },
          {
            key: "context_compression",
            label: t("context_gates.panel_context_compression"),
            forceRender: true,
            children: (
              <PolicyFieldList
                defs={CONTEXT_COMPRESSION_DEFS}
                values={values}
                onPatch={handlePatch}
              />
            ),
          },
          {
            key: "tool_output_budget",
            label: t("context_gates.panel_tool_output_budget"),
            forceRender: true,
            children: (
              <PolicyFieldList
                defs={TOOL_OUTPUT_BUDGET_DEFS}
                values={values}
                onPatch={handlePatch}
              />
            ),
          },
        ]}
      />
    </div>
  );
}
