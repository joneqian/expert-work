/**
 * SecuritySection — "安全与防护" (Security) group, Task 3 of PR3
 * (agent-config-page redesign). The group already had real content via the
 * existing "defenses"/"governance" FormView sections (prompt-injection /
 * output-screen / output-judge / output-dlp / action-screen switches, plus
 * the approval gate / dynamic-workers / advanced knobs) — this component
 * embeds that FormView FIRST, unchanged, then adds the two curated panels
 * the group didn't have a home for before: sandbox egress
 * (spec.sandbox.network) and the tool-use-enforcement knob
 * (policies.tool_use_enforcement). Both new panels start collapsed —
 * defenses/governance remain the group's primary content, so this pane
 * doesn't dump everything open at once (mirrors ``ContextGatesSection``'s
 * "don't fatigue" rule, just applied to ALL new panels here since neither is
 * more load-bearing than the other).
 *
 * Rendering the two new panels is delegated to ``PolicyFieldList`` (Task 1's
 * FieldDef config-array pattern); this component only wires the
 * ``readSecurity``/``patchSecurity`` pair (form_model.ts, Task 2) to it. A
 * closing note flags the three ``policies`` sub-blocks (rate limiting / PII
 * / security policy) that are still a free-form dict — the backend schema
 * for them isn't finalized, so they stay YAML-only for now.
 */
import { Collapse, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { FormView } from "../FormView";
import { PolicyFieldList, type FieldDef } from "./field_defs";
import {
  patchSecurity,
  readSecurity,
  type SecurityFields,
} from "../form_model";

const { Text } = Typography;

interface SecuritySectionProps {
  formData: unknown;
  onChange: (data: unknown) => void;
}

// ① spec.sandbox.network — the sandbox's outbound network policy. i18nKey
// prefixes mirror SecurityFields' egress/allowlist/denylist value keys.
const NETWORK_DEFS: readonly FieldDef[] = [
  {
    fieldId: "sandbox.network.egress",
    i18nKey: "security_gates.egress",
    valueKey: "egress",
    kind: "select",
    effectiveDefault: "proxy",
    options: ["proxy", "direct", "none"],
    optionLabelKey: "security_gates.egress_opt",
  },
  {
    fieldId: "sandbox.network.allowlist",
    i18nKey: "security_gates.allowlist",
    valueKey: "allowlist",
    kind: "tags",
    effectiveDefault: [],
  },
  {
    fieldId: "sandbox.network.denylist",
    i18nKey: "security_gates.denylist",
    valueKey: "denylist",
    kind: "tags",
    effectiveDefault: [],
  },
];

// ② policies.tool_use_enforcement — whether the tool-use-enforcement block
// is appended to the system prompt (and for which model families).
const ENFORCE_DEFS: readonly FieldDef[] = [
  {
    fieldId: "policies.tool_use_enforcement",
    i18nKey: "security_gates.enforce",
    valueKey: "toolUseEnforcement",
    kind: "select",
    effectiveDefault: "auto",
    options: ["auto", "on", "off"],
    optionLabelKey: "security_gates.enforce_opt",
  },
];

export function SecuritySection({
  formData,
  onChange,
}: SecuritySectionProps) {
  const { t } = useTranslation();
  const security = readSecurity(formData);
  // NETWORK_DEFS mixes a select (string) and two tags (string[]) fields;
  // ENFORCE_DEFS is select-only (string) — cast ``readSecurity``'s output to
  // each panel's narrower value domain, mirroring how ContextGatesSection
  // casts its (number|boolean)-only reader output for PolicyFieldList.
  const networkValues = security as Record<
    string,
    string | readonly string[] | undefined
  >;
  const enforceValues = security as Record<string, string | undefined>;

  const handlePatch = (patch: Partial<SecurityFields>): void => {
    onChange(patchSecurity(formData, patch));
  };

  return (
    <div data-testid="security-section" style={{ maxWidth: 760 }}>
      <FormView
        formData={formData}
        onChange={onChange}
        sections={["defenses", "governance"]}
      />
      <Collapse
        defaultActiveKey={[]}
        style={{ marginTop: 24 }}
        items={[
          {
            key: "network",
            label: t("security_gates.panel_network"),
            forceRender: true,
            children: (
              <>
                <Text
                  type="secondary"
                  style={{ display: "block", marginBottom: 16 }}
                >
                  {t("security_gates.group_intro")}
                </Text>
                <PolicyFieldList
                  defs={NETWORK_DEFS}
                  values={networkValues}
                  onPatch={handlePatch}
                />
              </>
            ),
          },
          {
            key: "enforce",
            label: t("security_gates.panel_enforce"),
            forceRender: true,
            children: (
              <PolicyFieldList
                defs={ENFORCE_DEFS}
                values={enforceValues}
                onPatch={handlePatch}
              />
            ),
          },
        ]}
      />
      <Text
        type="secondary"
        data-testid="security-gates-dict-note"
        style={{ display: "block", marginTop: 16 }}
      >
        {t("security_gates.dict_note")}
      </Text>
    </div>
  );
}
