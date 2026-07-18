/**
 * FieldRow — the per-field rendering contract for the agent-config-page
 * redesign (PR1, Task 4): label + control + default-value badge on one
 * line, a one-line brief always visible beneath it, and an optional impact
 * note that stays collapsed until expanded.
 *
 * Purely presentational: all copy (label/brief/impact) and the control
 * itself come from the caller — this component formats none of it, it just
 * lays the pieces out. Task 6 supplies the actual field copy when it wires
 * the pilot "运行预算与超时" group; this component takes no i18n dependency.
 */
import type { ReactNode } from "react";
import { Collapse, Tag } from "antd";

export interface FieldRowProps {
  /** manifest 路径,如 "workflow.max_iterations" → data-field-id */
  fieldId: string;
  label: string;
  /** 一行作用,永远可见 */
  brief: string;
  /** 展开的影响说明(调大/调小后果、生效条件) */
  impact?: string;
  /** 徽章文案;当前值===默认 → 灰"默认 <v>",否则蓝当前值 */
  defaultValue?: string;
  isDefault: boolean;
  /** 控件本体 */
  children: ReactNode;
}

export function FieldRow({
  fieldId,
  label,
  brief,
  impact,
  defaultValue,
  isDefault,
  children,
}: FieldRowProps) {
  return (
    <div data-field-id={fieldId} style={{ marginBottom: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ minWidth: 160, flexShrink: 0 }}>{label}</span>
        <span style={{ flex: 1 }}>{children}</span>
        {defaultValue !== undefined && (
          <Tag color={isDefault ? undefined : "blue"} bordered={false}>
            {isDefault ? `默认 ${defaultValue}` : defaultValue}
          </Tag>
        )}
      </div>
      <div style={{ fontSize: 12, color: "var(--ew-text-secondary)" }}>
        {brief}
      </div>
      {impact && (
        <Collapse
          ghost
          size="small"
          items={[
            {
              key: "impact",
              label: "影响说明",
              children: <div style={{ fontSize: 12 }}>{impact}</div>,
            },
          ]}
        />
      )}
    </div>
  );
}
