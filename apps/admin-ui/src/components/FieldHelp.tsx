/**
 * FieldHelp — a small "?" affordance shown after a form field's label. On hover
 * it reveals the field's meaning + a fill example. Reusable across every
 * field-based form (Agent template, MCP, Skill): drop ``<FieldHelp text={...} />``
 * next to any ``<label>`` or antd ``Form.Item`` label.
 *
 * The tooltip text supports newlines (rendered as line breaks) so a caller can
 * pass "含义…\n示例:…". Accessible: the trigger is a real button with an
 * aria-label, focusable + keyboard-reachable, and the tooltip is its
 * description.
 */
import { Tooltip } from "antd";
import { HelpCircle } from "lucide-react";
import { useTranslation } from "react-i18next";

interface FieldHelpProps {
  /** The help text — meaning + example. Newlines become line breaks. */
  text: string;
  /** Optional testid suffix, e.g. ``field-name`` → ``field-help-field-name``. */
  testId?: string;
}

export function FieldHelp({ text, testId }: FieldHelpProps) {
  const { t } = useTranslation();
  const title = text.split("\n").map((line, i) => (
    // Index key is fine — the lines are static for a given tooltip render.
    <div key={i}>{line}</div>
  ));
  return (
    <Tooltip title={title} mouseEnterDelay={0.2}>
      <button
        type="button"
        aria-label={t("common.field_help")}
        data-testid={testId ? `field-help-${testId}` : "field-help"}
        style={{
          display: "inline-flex",
          alignItems: "center",
          marginLeft: 6,
          padding: 0,
          border: "none",
          background: "none",
          cursor: "help",
          color: "var(--hx-text-tertiary, #888)",
          verticalAlign: "middle",
        }}
      >
        <HelpCircle size={13} strokeWidth={1.75} />
      </button>
    </Tooltip>
  );
}
