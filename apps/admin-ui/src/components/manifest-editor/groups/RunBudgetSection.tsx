/**
 * RunBudgetSection — pilot "运行预算与超时" (Run Budget & Timeouts) group,
 * Task 6 of the agent-config-page redesign (PR1). The first ``budget`` group
 * pane that renders real controls instead of the group-pending hint: five
 * knobs that live in three different manifest locations
 * (workflow.max_iterations / policies.max_no_progress+run_deadline_s /
 * top-level spec.stream_deadline_s+idle_timeout_s) behind one screen.
 *
 * Rendering itself is delegated to ``PolicyFieldList`` (Task 1's FieldDef
 * config-array pattern) — ``RUN_BUDGET_DEFS`` below is the data table that
 * used to be five hand-written ``FieldRow`` blocks; this component now only
 * wires the ``readRunBudget``/``patchRunBudget`` pair (form_model.ts) to it.
 *
 * Clearing an InputNumber (antd emits ``null``) reverts that field to the
 * platform default: the patch carries an explicit ``undefined``, which
 * ``patchRunBudget`` treats as "delete this key" (dropping the parent block
 * too when that empties it) rather than writing the default value into the
 * manifest. A switch field would follow the same rule (value === default →
 * delete the key) but this pilot group has none yet.
 */
import { PolicyFieldList, type FieldDef } from "./field_defs";
import { patchRunBudget, readRunBudget } from "../form_model";

interface RunBudgetSectionProps {
  formData: unknown;
  onChange: (data: unknown) => void;
}

// Display-layer effective defaults (agent_spec.py WorkflowSpec/PolicySpec/
// AgentSpecBody) live in each def's ``effectiveDefault`` — a stored value of
// ``undefined`` means the backend applies these. i18nKey mirrors the existing
// ``run_budget.*`` locale keys (already four-key complete per field).
const RUN_BUDGET_DEFS: readonly FieldDef[] = [
  {
    fieldId: "workflow.max_iterations",
    i18nKey: "run_budget.max_iterations",
    valueKey: "maxIterations",
    kind: "number",
    effectiveDefault: 30,
    min: 1,
  },
  {
    fieldId: "policies.max_no_progress",
    i18nKey: "run_budget.max_no_progress",
    valueKey: "maxNoProgress",
    kind: "number",
    effectiveDefault: 0,
    min: 0,
  },
  {
    fieldId: "policies.run_deadline_s",
    i18nKey: "run_budget.run_deadline",
    valueKey: "runDeadlineS",
    kind: "number",
    effectiveDefault: 0,
    min: 0,
    max: 86400,
  },
  {
    fieldId: "spec.stream_deadline_s",
    i18nKey: "run_budget.stream_deadline",
    valueKey: "streamDeadlineS",
    kind: "number",
    effectiveDefault: 180,
    min: 0,
    max: 3600,
  },
  {
    fieldId: "spec.idle_timeout_s",
    i18nKey: "run_budget.idle_timeout",
    valueKey: "idleTimeoutS",
    kind: "number",
    effectiveDefault: 45,
    min: 0,
    max: 600,
  },
];

export function RunBudgetSection({ formData, onChange }: RunBudgetSectionProps) {
  const budget = readRunBudget(formData);

  const handlePatch = (patch: Record<string, number | boolean | undefined>): void => {
    onChange(patchRunBudget(formData, patch));
  };

  return (
    <div data-testid="run-budget-section" style={{ maxWidth: 760 }}>
      <PolicyFieldList
        defs={RUN_BUDGET_DEFS}
        values={budget as Record<string, number | boolean | undefined>}
        onPatch={handlePatch}
      />
    </div>
  );
}
