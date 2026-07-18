/**
 * RunBudgetSection — pilot "运行预算与超时" (Run Budget & Timeouts) group,
 * Task 6 of the agent-config-page redesign (PR1). The first ``budget`` group
 * pane that renders real controls instead of the group-pending hint: five
 * knobs that live in three different manifest locations
 * (workflow.max_iterations / policies.max_no_progress+run_deadline_s /
 * top-level spec.stream_deadline_s+idle_timeout_s) behind one screen, each
 * rendered as a ``FieldRow`` (label + control + default badge, one-line
 * brief, collapsible impact note).
 *
 * Reads/writes go through the single ``readRunBudget``/``patchRunBudget``
 * pair (form_model.ts) so this component stays presentation-only. Per-field
 * copy is resolved here (react-i18next) and handed to ``FieldRow`` as plain
 * strings — ``FieldRow`` itself renders none of it.
 *
 * Clearing an InputNumber (antd emits ``null``) reverts that field to the
 * platform default: the patch carries an explicit ``undefined``, which
 * ``patchRunBudget`` treats as "delete this key" (dropping the parent block
 * too when that empties it) rather than writing the default value into the
 * manifest.
 */
import { InputNumber } from "antd";
import { useTranslation } from "react-i18next";

import { FieldRow } from "../FieldRow";
import { patchRunBudget, readRunBudget, type RunBudgetFields } from "../form_model";

interface RunBudgetSectionProps {
  formData: unknown;
  onChange: (data: unknown) => void;
}

// Display-layer effective defaults (agent_spec.py WorkflowSpec/PolicySpec/
// AgentSpecBody) — a stored value of ``undefined`` means the backend applies
// these. Kept here (not in form_model.ts) since they're presentation
// concerns: which value the badge/control shows when the manifest is silent.
const DEFAULTS = {
  maxIterations: 30,
  maxNoProgress: 0,
  runDeadlineS: 0,
  streamDeadlineS: 180,
  idleTimeoutS: 45,
} as const satisfies Required<RunBudgetFields>;

function isAtDefault(raw: number | undefined, def: number): boolean {
  return raw === undefined || raw === def;
}

export function RunBudgetSection({ formData, onChange }: RunBudgetSectionProps) {
  const { t } = useTranslation();
  const budget = readRunBudget(formData);

  const patch = (field: keyof RunBudgetFields, value: number | null): void => {
    onChange(patchRunBudget(formData, { [field]: value ?? undefined }));
  };

  const maxIterationsAtDefault = isAtDefault(
    budget.maxIterations,
    DEFAULTS.maxIterations,
  );
  const maxNoProgressAtDefault = isAtDefault(
    budget.maxNoProgress,
    DEFAULTS.maxNoProgress,
  );
  const runDeadlineAtDefault = isAtDefault(
    budget.runDeadlineS,
    DEFAULTS.runDeadlineS,
  );
  const streamDeadlineAtDefault = isAtDefault(
    budget.streamDeadlineS,
    DEFAULTS.streamDeadlineS,
  );
  const idleTimeoutAtDefault = isAtDefault(
    budget.idleTimeoutS,
    DEFAULTS.idleTimeoutS,
  );

  return (
    <div data-testid="run-budget-section" style={{ maxWidth: 760 }}>
      <FieldRow
        fieldId="workflow.max_iterations"
        label={t("run_budget.max_iterations_label")}
        brief={t("run_budget.max_iterations_brief")}
        impact={t("run_budget.max_iterations_impact")}
        defaultValue={
          maxIterationsAtDefault
            ? t("run_budget.max_iterations_default")
            : String(budget.maxIterations)
        }
        isDefault={maxIterationsAtDefault}
      >
        <InputNumber
          min={1}
          value={budget.maxIterations ?? DEFAULTS.maxIterations}
          aria-label={t("run_budget.max_iterations_label")}
          onChange={(v) => patch("maxIterations", v)}
        />
      </FieldRow>

      <FieldRow
        fieldId="policies.max_no_progress"
        label={t("run_budget.max_no_progress_label")}
        brief={t("run_budget.max_no_progress_brief")}
        impact={t("run_budget.max_no_progress_impact")}
        defaultValue={
          maxNoProgressAtDefault
            ? t("run_budget.max_no_progress_default")
            : String(budget.maxNoProgress)
        }
        isDefault={maxNoProgressAtDefault}
      >
        <InputNumber
          min={0}
          value={budget.maxNoProgress ?? DEFAULTS.maxNoProgress}
          aria-label={t("run_budget.max_no_progress_label")}
          onChange={(v) => patch("maxNoProgress", v)}
        />
      </FieldRow>

      <FieldRow
        fieldId="policies.run_deadline_s"
        label={t("run_budget.run_deadline_label")}
        brief={t("run_budget.run_deadline_brief")}
        impact={t("run_budget.run_deadline_impact")}
        defaultValue={
          runDeadlineAtDefault
            ? t("run_budget.run_deadline_default")
            : String(budget.runDeadlineS)
        }
        isDefault={runDeadlineAtDefault}
      >
        <InputNumber
          min={0}
          max={86400}
          value={budget.runDeadlineS ?? DEFAULTS.runDeadlineS}
          aria-label={t("run_budget.run_deadline_label")}
          onChange={(v) => patch("runDeadlineS", v)}
        />
      </FieldRow>

      <FieldRow
        fieldId="spec.stream_deadline_s"
        label={t("run_budget.stream_deadline_label")}
        brief={t("run_budget.stream_deadline_brief")}
        impact={t("run_budget.stream_deadline_impact")}
        defaultValue={
          streamDeadlineAtDefault
            ? t("run_budget.stream_deadline_default")
            : String(budget.streamDeadlineS)
        }
        isDefault={streamDeadlineAtDefault}
      >
        <InputNumber
          min={0}
          max={3600}
          value={budget.streamDeadlineS ?? DEFAULTS.streamDeadlineS}
          aria-label={t("run_budget.stream_deadline_label")}
          onChange={(v) => patch("streamDeadlineS", v)}
        />
      </FieldRow>

      <FieldRow
        fieldId="spec.idle_timeout_s"
        label={t("run_budget.idle_timeout_label")}
        brief={t("run_budget.idle_timeout_brief")}
        impact={t("run_budget.idle_timeout_impact")}
        defaultValue={
          idleTimeoutAtDefault
            ? t("run_budget.idle_timeout_default")
            : String(budget.idleTimeoutS)
        }
        isDefault={idleTimeoutAtDefault}
      >
        <InputNumber
          min={0}
          max={600}
          value={budget.idleTimeoutS ?? DEFAULTS.idleTimeoutS}
          aria-label={t("run_budget.idle_timeout_label")}
          onChange={(v) => patch("idleTimeoutS", v)}
        />
      </FieldRow>
    </div>
  );
}
