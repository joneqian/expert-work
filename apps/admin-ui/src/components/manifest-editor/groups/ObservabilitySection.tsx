/**
 * ObservabilitySection — "触发器与可观测" (Triggers & Observability) group,
 * Task 2 of PR7 (agent-config-page redesign), plus Task 8b's erratum fix.
 * Two live fields (``spec.cache.enabled`` — the per-agent LLM response cache
 * opt-out; ``policies.trajectory_recording`` — whether a finished run's full
 * transcript is archived for quality evaluation and future fine-tuning,
 * restored by Task 8b after Task 4 mistook it for a declarative field) plus
 * two note blocks: manifest ``triggers`` are declared but not wired up (the
 * trigger-management API, /v1/triggers, is the real path — see
 * ``triggers_note``), and observability's ``trace``/``log_level``/
 * ``redact_fields`` ARE declarative-only: they pass schema validation but the
 * runtime never reads them (see ``declarative_note``). Unlike its curated
 * siblings this group has no existing FormView sections to embed — it was
 * the generic pending-hint placeholder until now. Flat pane, no ``Collapse``
 * (mirrors ``SandboxSection`` — content is short enough to lay out flat).
 */
import { Typography } from "antd";
import { useTranslation } from "react-i18next";

import { PolicyFieldList, type FieldDef } from "./field_defs";
import { patchObservability, readObservability } from "../form_model";

const { Text } = Typography;

interface ObservabilitySectionProps {
  formData: unknown;
  onChange: (data: unknown) => void;
}

// ① spec.cache.enabled — the per-agent LLM response cache opt-out. A switch,
// so PolicyFieldList shows no default badge (existing switch semantics — see
// field_defs.tsx). Default true mirrors the backend's default_factory
// (CacheSpec absent ⇒ enabled).
// ② policies.trajectory_recording — whether a finished run's full transcript
// (user input, agent replies, tool calls) is serialized to object storage
// for quality evaluation / future fine-tuning. Default true mirrors the
// backend default (absent key ⇒ recording on); privacy-sensitive agents
// should flip it off.
const OBSERVABILITY_DEFS: readonly FieldDef[] = [
  {
    fieldId: "cache.enabled",
    i18nKey: "observability_group.resp_cache",
    valueKey: "responseCacheEnabled",
    kind: "switch",
    effectiveDefault: true,
  },
  {
    fieldId: "policies.trajectory_recording",
    i18nKey: "observability_group.traj_rec",
    valueKey: "trajectoryRecording",
    kind: "switch",
    effectiveDefault: true,
  },
];

export function ObservabilitySection({
  formData,
  onChange,
}: ObservabilitySectionProps) {
  const { t } = useTranslation();
  const observability = readObservability(formData);

  const handlePatch = (patch: Record<string, boolean | undefined>): void => {
    onChange(patchObservability(formData, patch));
  };

  return (
    <div data-testid="observability-section" style={{ maxWidth: 760 }}>
      <PolicyFieldList
        defs={OBSERVABILITY_DEFS}
        values={observability as Record<string, boolean | undefined>}
        onPatch={handlePatch}
      />
      <Text
        type="secondary"
        data-testid="observability-triggers-note"
        style={{ display: "block", marginTop: 24 }}
      >
        {t("observability_group.triggers_note")}
      </Text>
      <Text
        type="secondary"
        data-testid="observability-declarative-note"
        style={{ display: "block", marginTop: 16 }}
      >
        {t("observability_group.declarative_note")}
      </Text>
    </div>
  );
}
