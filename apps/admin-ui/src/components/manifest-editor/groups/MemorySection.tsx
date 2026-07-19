/**
 * MemorySection — "记忆" (Memory) group, Task 2 of PR5 (agent-config-page
 * redesign). The group already had real content via the existing "memory"
 * FormView section (on/off toggle, retrieve_top_k, write-back + the
 * verify-reads/write_min_importance/reconcile_writes/recall_mode advanced
 * panel) — this component embeds that FormView FIRST, unchanged, then adds
 * two curated panels the group didn't have a home for before: ① injection
 * budgets (spec.memory.long_term.injection_token_budget /
 * correction_token_budget) and ② background consolidation
 * (policies.memory_consolidation.enabled). Both start collapsed (mirrors
 * ``SecuritySection``'s "don't fatigue" rule — the embedded FormView section
 * remains the group's primary content).
 *
 * The injection-budget panel is gated on ``readMemoryOn(formData)``: a
 * budget over an absent ``memory.long_term`` block is meaningless (nothing
 * to inject), and ``patchMemoryBudgets`` materializes ``long_term: {}`` the
 * moment a budget key is patched — rendering the panel while memory is off
 * would silently reactivate memory the moment a user touched either field.
 * The panel is therefore not merely disabled but absent from the DOM while
 * memory is off. The consolidation panel has no such gate: it's a
 * control-plane background job (``policies.memory_consolidation``), wholly
 * independent of whether ``memory.long_term`` is declared.
 *
 * Rendering both panels is delegated to ``PolicyFieldList`` (Task 1's
 * FieldDef config-array pattern); this component only wires the
 * ``readMemoryBudgets``/``patchMemoryBudgets`` and
 * ``readConsolidation``/``patchConsolidation`` pairs (form_model.ts, Task 1)
 * to it. A closing note flags ``memory.short_term`` and
 * ``dynamic_context.inject_memory`` as reserved fields — schema-valid but
 * not read at runtime.
 */
import { Collapse, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { FormView } from "../FormView";
import { PolicyFieldList, type FieldDef } from "./field_defs";
import {
  patchConsolidation,
  patchMemoryBudgets,
  readConsolidation,
  readMemoryBudgets,
  readMemoryOn,
  type ConsolidationFields,
  type MemoryBudgetFields,
} from "../form_model";

const { Text } = Typography;

interface MemorySectionProps {
  formData: unknown;
  onChange: (data: unknown) => void;
}

// ① spec.memory.long_term.{injection,correction}_token_budget — the two
// token ceilings around recalled-memory injection.
const BUDGET_DEFS: readonly FieldDef[] = [
  {
    fieldId: "memory.long_term.injection_token_budget",
    i18nKey: "memory_group.inj_budget",
    valueKey: "injectionTokenBudget",
    kind: "number",
    effectiveDefault: 2000,
    min: 100,
    max: 100000,
  },
  {
    fieldId: "memory.long_term.correction_token_budget",
    i18nKey: "memory_group.corr_budget",
    valueKey: "correctionTokenBudget",
    kind: "number",
    effectiveDefault: 500,
    min: 0,
    max: 100000,
  },
];

// ② policies.memory_consolidation.enabled — the background consolidator's
// per-agent master switch.
const CONSOLIDATION_DEFS: readonly FieldDef[] = [
  {
    fieldId: "policies.memory_consolidation.enabled",
    i18nKey: "memory_group.consolidation",
    valueKey: "consolidationEnabled",
    kind: "switch",
    effectiveDefault: true,
  },
];

export function MemorySection({ formData, onChange }: MemorySectionProps) {
  const { t } = useTranslation();
  const memoryOn = readMemoryOn(formData);
  const budgetValues = readMemoryBudgets(formData) as Record<
    string,
    number | undefined
  >;
  const consolidationValues = readConsolidation(formData) as Record<
    string,
    boolean | undefined
  >;

  const handleBudgetPatch = (patch: Partial<MemoryBudgetFields>): void => {
    onChange(patchMemoryBudgets(formData, patch));
  };
  const handleConsolidationPatch = (
    patch: Partial<ConsolidationFields>,
  ): void => {
    onChange(patchConsolidation(formData, patch));
  };

  return (
    <div data-testid="memory-section" style={{ maxWidth: 760 }}>
      <FormView
        formData={formData}
        onChange={onChange}
        sections={["memory"]}
      />
      <Collapse
        defaultActiveKey={[]}
        style={{ marginTop: 24 }}
        items={[
          ...(memoryOn
            ? [
                {
                  key: "injection",
                  label: t("memory_group.panel_injection"),
                  forceRender: true,
                  children: (
                    <PolicyFieldList
                      defs={BUDGET_DEFS}
                      values={budgetValues}
                      onPatch={handleBudgetPatch}
                    />
                  ),
                },
              ]
            : []),
          {
            key: "consolidation",
            label: t("memory_group.panel_consolidation"),
            forceRender: true,
            children: (
              <>
                <PolicyFieldList
                  defs={CONSOLIDATION_DEFS}
                  values={consolidationValues}
                  onPatch={handleConsolidationPatch}
                />
                <Text
                  type="secondary"
                  style={{ display: "block", marginTop: 8 }}
                >
                  {t("memory_group.aux_model_note")}
                </Text>
              </>
            ),
          },
        ]}
      />
      <Text
        type="secondary"
        data-testid="memory-reserved-note"
        style={{ display: "block", marginTop: 16 }}
      >
        {t("memory_group.reserved_note")}
      </Text>
    </div>
  );
}
