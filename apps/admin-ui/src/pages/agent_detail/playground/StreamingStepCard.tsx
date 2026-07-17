/**
 * StreamingStepCard — a synthetic, live step card for the step currently being
 * streamed token-by-token (流式 epic 子项目 3a content + 3b reasoning/tool_args).
 * Rendered by StepTimeline for a step that has live tokens but no authoritative
 * `AgentStep` card yet. Text is plain (`pre-wrap`), never markdown — markdown
 * reflow on every token is janky; the authoritative card renders markdown (and
 * the tool call arguments) once the `updates` frame settles the step.
 */
import { useState, type KeyboardEvent } from "react";
import { Typography } from "antd";
import { useTranslation } from "react-i18next";

import { fmtDuration } from "./duration_format";
import type { LiveStep } from "./useTokenStream";

const { Text } = Typography;

const ACCENT = "var(--ew-accent-violet, #a855f7)";
const DANGER = "var(--ew-text-danger, #cf1322)";

export interface StreamingStepCardProps {
  step: number;
  live: LiveStep;
  interrupted: boolean;
  ttftMs: number | null;
}

export function StreamingStepCard({ step, live, interrupted, ttftMs }: StreamingStepCardProps) {
  const { t } = useTranslation();
  const accent = interrupted ? DANGER : ACCENT;
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
          <span data-testid="streaming-badge" style={{ color: ACCENT, fontSize: 12 }}>
            {t("playground.streaming_badge")}
          </span>
        )}
        {ttftMs !== null && (
          <span data-testid="ttft-badge" style={{ color: "var(--ew-text-secondary, #888)", fontSize: 12 }}>
            {t("playground.ttft", { d: fmtDuration(ttftMs) })}
          </span>
        )}
      </div>
      {live.reasoning !== "" && (
        <ReasoningRegion reasoning={live.reasoning} reasoningMs={live.reasoningMs} />
      )}
      {live.toolNames.size > 0 && <ToolChips toolNames={live.toolNames} />}
      {live.content !== "" && <Text style={{ whiteSpace: "pre-wrap", fontSize: 13 }}>{live.content}</Text>}
    </div>
  );
}

function ReasoningRegion({ reasoning, reasoningMs }: { reasoning: string; reasoningMs: number | null }) {
  const { t } = useTranslation();
  // Auto-expand while still reasoning (reasoningMs === null); auto-collapse to a
  // "Thought for Xs" summary once the duration is known (content started / step
  // settled). A manual click overrides the auto behaviour thereafter.
  const [override, setOverride] = useState<boolean | null>(null);
  const expanded = override ?? reasoningMs === null;
  const label =
    reasoningMs === null
      ? t("playground.streaming_reasoning_label")
      : t("playground.reasoning_summary", { d: fmtDuration(reasoningMs) });
  const toggle = (): void => setOverride(!expanded);
  const onKeyDown = (e: KeyboardEvent): void => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      toggle();
    }
  };
  return (
    <div
      data-testid="reasoning-region"
      style={{
        marginBottom: 6,
        borderLeft: `2px solid color-mix(in srgb, ${ACCENT} 55%, var(--ew-border-subtle))`,
        paddingLeft: 10,
      }}
    >
      <div
        data-testid="reasoning-summary"
        role="button"
        tabIndex={0}
        onClick={toggle}
        onKeyDown={onKeyDown}
        style={{ cursor: "pointer", fontSize: 11, color: ACCENT, display: "flex", gap: 6, alignItems: "center" }}
      >
        <span>💭 {label}</span>
        <span style={{ color: "var(--ew-text-tertiary)" }}>{expanded ? "▾" : "▸"}</span>
      </div>
      {expanded && (
        <p
          style={{
            margin: "3px 0 0",
            fontSize: 12,
            color: "var(--ew-text-secondary)",
            fontStyle: "italic",
            whiteSpace: "pre-wrap",
          }}
        >
          {reasoning}
        </p>
      )}
    </div>
  );
}

function ToolChips({ toolNames }: { toolNames: ReadonlyMap<number, string> }) {
  const chips = [...toolNames].sort(([a], [b]) => a - b);
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 6 }}>
      {chips.map(([index, name]) => (
        <span
          key={index}
          data-testid="tool-chip"
          style={{
            fontFamily: "var(--ew-font-mono)",
            fontSize: 11,
            padding: "1px 7px",
            borderRadius: 4,
            background: `color-mix(in srgb, ${ACCENT} 14%, transparent)`,
            color: ACCENT,
          }}
        >
          🔧 {name}
        </span>
      ))}
    </div>
  );
}
