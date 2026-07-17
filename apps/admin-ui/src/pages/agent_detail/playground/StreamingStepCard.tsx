/**
 * StreamingStepCard — a synthetic, live step card for the step currently being
 * streamed token-by-token (流式 epic 子项目 3a). Rendered by StepTimeline for a
 * step that has live tokens but no authoritative `AgentStep` card yet. Text is
 * plain (`pre-wrap`), never markdown — markdown reflow on every token is janky
 * and partial fences render oddly; the authoritative card renders markdown once
 * the `updates` frame settles the step.
 */
import { Typography } from "antd";
import { useTranslation } from "react-i18next";

import { fmtDuration } from "./duration_format";

const { Text } = Typography;

const STREAMING = "var(--ew-accent-violet, #a855f7)";
const DANGER = "var(--ew-text-danger, #cf1322)";

export interface StreamingStepCardProps {
  step: number;
  text: string;
  interrupted: boolean;
  ttftMs: number | null;
}

export function StreamingStepCard({ step, text, interrupted, ttftMs }: StreamingStepCardProps) {
  const { t } = useTranslation();
  const accent = interrupted ? DANGER : STREAMING;
  return (
    <div
      data-testid="streaming-step-card"
      data-step={step}
      style={{
        border: `1px solid ${accent}`,
        borderRadius: 8,
        padding: "8px 12px",
        marginBottom: 8,
        background: "var(--ew-bg-elevated, transparent)",
      }}
    >
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 6 }}>
        <span style={{ fontWeight: 600 }}>{t("playground.tl_step", { n: step })}</span>
        {interrupted ? (
          <span data-testid="interrupted-badge" style={{ color: DANGER, fontSize: 12 }}>
            {t("playground.interrupted_badge")}
          </span>
        ) : (
          <span data-testid="streaming-badge" style={{ color: STREAMING, fontSize: 12 }}>
            {t("playground.streaming_badge")}
          </span>
        )}
        {ttftMs !== null && (
          <span data-testid="ttft-badge" style={{ color: "var(--ew-text-secondary, #888)", fontSize: 12 }}>
            {t("playground.ttft", { d: fmtDuration(ttftMs) })}
          </span>
        )}
      </div>
      <Text style={{ whiteSpace: "pre-wrap", fontSize: 13 }}>{text}</Text>
    </div>
  );
}
