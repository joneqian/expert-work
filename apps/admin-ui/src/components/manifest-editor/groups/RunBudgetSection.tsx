/**
 * RunBudgetSection — pilot "运行预算与超时" (Run Budget & Timeouts) group,
 * Task 6 of the agent-config-page redesign (PR1). The first ``budget`` group
 * pane that renders real controls instead of the group-pending hint: six
 * knobs that live in three different manifest locations
 * (workflow.max_iterations+type / policies.max_no_progress+run_deadline_s /
 * top-level spec.stream_deadline_s+idle_timeout_s) behind one screen.
 *
 * Rendering itself is delegated to ``PolicyFieldTable`` (a later redesign
 * PR's table-layout renderer over Task 1's FieldDef config-array pattern) —
 * ``RUN_BUDGET_DEFS`` below is the data table that used to be five
 * hand-written ``FieldRow`` blocks; this component now only wires the
 * ``readRunBudget``/``patchRunBudget`` pair (form_model.ts) to it.
 *
 * Clearing an InputNumber (antd emits ``null``) reverts that field to the
 * platform default: the patch carries an explicit ``undefined``, which
 * ``patchRunBudget`` treats as "delete this key" (dropping the parent block
 * too when that empties it) rather than writing the default value into the
 * manifest. A select field follows the same rule (selecting back to
 * ``effectiveDefault`` → the patch carries ``undefined`` → key deleted) —
 * see ``workflow.type`` below, PR8 Task 1 (field_defs.tsx's select kind,
 * PR3 Task 1).
 *
 * A closing note (PR8 Task 1) flags ``workflow``'s other two YAML-only keys
 * (``early_stop``/``builder``) that pass schema validation but the runtime
 * never reads — left untouched, so authoring them by hand is harmless.
 */
import { Typography } from "antd";
import { useTranslation } from "react-i18next";

import { PolicyFieldTable, type FieldDef } from "./field_defs";
import {
  patchRunBudget,
  readRunBudget,
  type RunBudgetFields,
} from "../form_model";

const { Text } = Typography;

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
    fieldId: "workflow.type",
    i18nKey: "run_budget.wf_type",
    valueKey: "workflowType",
    kind: "select",
    effectiveDefault: "react",
    options: ["react", "plan_execute", "custom"],
    optionLabelKey: "run_budget.wf_type_opt",
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
    fieldId: "policies.token_budget",
    i18nKey: "run_budget.token_budget",
    valueKey: "tokenBudget",
    kind: "number",
    effectiveDefault: 0,
    min: 0,
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
  const { t } = useTranslation();
  const budget = readRunBudget(formData);

  const handlePatch = (patch: Partial<RunBudgetFields>): void => {
    onChange(patchRunBudget(formData, patch));
  };

  return (
    <div data-testid="run-budget-section" style={{ maxWidth: 760 }}>
      <PolicyFieldTable
        groups={[{ defs: RUN_BUDGET_DEFS }]}
        values={budget as Record<string, number | string | undefined>}
        onPatch={handlePatch}
      />
      <Text
        type="secondary"
        data-testid="budget-workflow-note"
        style={{ display: "block", marginTop: 16 }}
      >
        {t("run_budget.workflow_note")}
      </Text>
    </div>
  );
}
