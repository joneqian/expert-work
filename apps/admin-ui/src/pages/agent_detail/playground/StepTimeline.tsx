/**
 * StepTimeline — renders a turn's `TimelineItem[]` (Task 1's `parseTimeline`
 * output) as the wireframe's typed execution-trace axis: agent steps as big
 * cards (per-step reasoning + nested tool cards / final answer), memory /
 * planner / reflect channels as light collapsible rows, and
 * compaction / retry / error / approval / end as axis markers — all in
 * arrival order. See docs/superpowers/specs/2026-07-10-batch3-wireframe.html
 * (`.timeline` / `.step` / `.node-row` / `.marker` / `.legend`) for the
 * authoritative visual structure this transcribes.
 */
import { useState, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";

import type { AgentStep, AuxNodeItem, MarkerItem, TimelineItem } from "../../../api/timeline";
import { ToolCallCard } from "../../../components/ToolTimeline";

// Semantic colors as CSS vars with wireframe-matching hex fallbacks — same
// `var(--ew-*, #hex)` convention Batch 1/2 established (ToolTimeline.tsx /
// AgentStatePanels.tsx already use `--ew-text-danger` this way).
const DANGER = "var(--ew-text-danger, #cf1322)";
const SUCCESS = "var(--ew-text-success, #3ecf8e)";
const WARNING = "var(--ew-text-warning, #e8a33d)";
const PURPLE = "var(--ew-accent-violet, #a855f7)";
const INFO = "var(--ew-text-info, #4c8dff)";

export interface StepTimelineProps {
  items: readonly TimelineItem[];
}

export function StepTimeline({ items }: StepTimelineProps) {
  if (items.length === 0) return null;

  return (
    <div>
      <div data-testid="step-timeline" style={{ position: "relative", paddingLeft: 26 }}>
        <span
          aria-hidden
          style={{
            position: "absolute",
            left: 9,
            top: 4,
            bottom: 4,
            width: 2,
            background: "var(--ew-border-default)",
            borderRadius: 2,
          }}
        />
        {items.map((item) => {
          switch (item.kind) {
            case "agent":
              return <AgentStepCard key={item.seq} item={item} />;
            case "compaction":
            case "retry":
            case "error":
            case "approval":
            case "end":
              return <MarkerRow key={item.seq} item={item} />;
            default:
              return <AuxNodeRow key={item.seq} item={item} />;
          }
        })}
      </div>
      <Legend />
    </div>
  );
}

function AgentStepCard({ item }: { item: AgentStep }) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(item.hasError);
  const isFinal = item.tools.length === 0;
  const toggle = (): void => setExpanded((v) => !v);
  const onHeadKeyDown = (e: KeyboardEvent): void => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      toggle();
    }
  };

  const caretSuffix =
    item.tools.length > 0
      ? t(item.tools.length === 1 ? "playground.tool_count_one" : "playground.tool_count_other", {
          count: item.tools.length,
        })
      : isFinal && item.content
        ? t("playground.tl_final_answer")
        : "";

  return (
    <div style={{ position: "relative", marginBottom: 10 }}>
      <span
        aria-hidden
        style={{
          position: "absolute",
          left: -22,
          top: 13,
          width: 11,
          height: 11,
          borderRadius: "50%",
          background: item.hasError
            ? `color-mix(in srgb, ${DANGER} 30%, var(--ew-surface-base))`
            : "var(--ew-surface-base)",
          border: `2px solid ${item.hasError ? DANGER : "var(--ew-border-strong)"}`,
        }}
      />
      <div
        data-testid="step-card"
        style={{
          border: `1px solid ${
            item.hasError
              ? `color-mix(in srgb, ${DANGER} 40%, var(--ew-border-subtle))`
              : "var(--ew-border-subtle)"
          }`,
          borderRadius: 6,
          background: "var(--ew-surface-base)",
          overflow: "hidden",
        }}
      >
        <div
          data-testid="step-head"
          role="button"
          tabIndex={0}
          onClick={toggle}
          onKeyDown={onHeadKeyDown}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "8px 11px",
            cursor: "pointer",
            flexWrap: "wrap",
            background: item.hasError ? `color-mix(in srgb, ${DANGER} 8%, transparent)` : undefined,
          }}
        >
          <span style={{ fontWeight: 650, fontSize: 13 }}>
            {t("playground.tl_step", { n: item.stepCount ?? "?" })}
          </span>
          <span
            style={{ fontFamily: "var(--ew-font-mono)", fontSize: 11, color: "var(--ew-text-tertiary)" }}
          >
            {item.node}
          </span>
          <div
            style={{
              display: "flex",
              gap: 6,
              flexWrap: "wrap",
              alignItems: "center",
              fontSize: 11,
              color: "var(--ew-text-secondary)",
            }}
          >
            {item.model && <span>{item.model}</span>}
            {item.hasError ? (
              <span style={{ color: DANGER }}>{t("playground.tl_tool_failed")}</span>
            ) : (
              item.finishReason && <span>{t("playground.tl_finish", { reason: item.finishReason })}</span>
            )}
            <span>{item.totalTokens} tok</span>
          </div>
          <span style={{ marginLeft: "auto", color: "var(--ew-text-tertiary)", fontSize: 11 }}>
            {expanded ? "▾" : caretSuffix ? `▸ ${caretSuffix}` : "▸"}
          </span>
        </div>
        {expanded && (
          <div style={{ padding: "2px 11px 11px", borderTop: "1px solid var(--ew-border-subtle)" }}>
            {item.reasoning && (
              <div
                style={{
                  marginTop: 8,
                  borderLeft: `2px solid color-mix(in srgb, ${PURPLE} 55%, var(--ew-border-subtle))`,
                  paddingLeft: 10,
                }}
              >
                <div
                  style={{
                    fontSize: 10.5,
                    letterSpacing: "0.05em",
                    textTransform: "uppercase",
                    color: PURPLE,
                  }}
                >
                  {t("playground.tl_reasoning")}
                </div>
                <p style={{ margin: "3px 0 0", fontSize: 12, color: "var(--ew-text-secondary)", fontStyle: "italic" }}>
                  {item.reasoning}
                </p>
              </div>
            )}
            {item.tools.map((tool) => (
              <div key={tool.id} style={{ marginTop: 8 }}>
                <ToolCallCard entry={tool} />
              </div>
            ))}
            {isFinal && item.content && (
              <div style={{ paddingTop: 8, fontSize: 13, color: "var(--ew-text-secondary)" }}>
                {item.content}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function AuxNodeRow({ item }: { item: AuxNodeItem }) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const warn = item.tone === "warn";
  const toggle = (): void => setExpanded((v) => !v);
  const onKeyDown = (e: KeyboardEvent): void => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      toggle();
    }
  };
  const dotColor =
    item.kind === "planner"
      ? INFO
      : item.kind === "reflect"
        ? warn
          ? WARNING
          : SUCCESS
        : item.kind === "memory_recall" || item.kind === "memory_writeback"
          ? SUCCESS
          : "var(--ew-border-strong)";
  const tailLabel = item.kind === "reflect" ? t("playground.tl_critique") : t("playground.tl_expand");

  return (
    <div style={{ position: "relative", marginBottom: 10 }}>
      <span
        aria-hidden
        style={{
          position: "absolute",
          left: -22,
          top: 11,
          width: 9,
          height: 9,
          borderRadius: "50%",
          background: "var(--ew-surface-base)",
          border: `2px solid ${dotColor}`,
        }}
      />
      <div
        data-testid="node-row"
        role="button"
        tabIndex={0}
        onClick={toggle}
        onKeyDown={onKeyDown}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          flexWrap: "wrap",
          padding: "7px 11px",
          borderRadius: 6,
          fontSize: 12.5,
          cursor: "pointer",
          border: `1px solid ${
            warn ? `color-mix(in srgb, ${WARNING} 40%, var(--ew-border-subtle))` : "var(--ew-border-subtle)"
          }`,
          background: warn ? `color-mix(in srgb, ${WARNING} 7%, transparent)` : "var(--ew-surface-base)",
        }}
      >
        <span
          style={{ fontFamily: "var(--ew-font-mono)", fontSize: 11, color: "var(--ew-text-tertiary)" }}
        >
          {item.kind}
        </span>
        <span>{item.summary}</span>
        <span style={{ marginLeft: "auto", color: "var(--ew-text-tertiary)", fontSize: 11 }}>
          {expanded ? "▾" : "▸"} {tailLabel}
        </span>
      </div>
      {expanded && (
        <div
          style={{
            marginTop: 6,
            padding: "8px 11px",
            border: "1px solid var(--ew-border-subtle)",
            borderRadius: 6,
            background: "var(--ew-surface-raised)",
            fontSize: 12,
          }}
        >
          <AuxNodeDetail item={item} />
        </div>
      )}
    </div>
  );
}

interface MemoryDetailItem {
  id: string;
  kind: string;
  content: string;
  importance?: number;
  confidence?: number;
}

function record(v: unknown): Record<string, unknown> {
  return v !== null && typeof v === "object" ? (v as Record<string, unknown>) : {};
}

function asMemories(detail: Record<string, unknown>): MemoryDetailItem[] {
  const raw = detail.memories;
  if (!Array.isArray(raw)) return [];
  return raw.map((m, i) => {
    const o = record(m);
    return {
      id: typeof o.id === "string" ? o.id : String(i),
      kind: typeof o.kind === "string" ? o.kind : "",
      content: typeof o.content === "string" ? o.content : "",
      importance: typeof o.importance === "number" ? o.importance : undefined,
      confidence: typeof o.confidence === "number" ? o.confidence : undefined,
    };
  });
}

function planGoal(detail: Record<string, unknown>): string | null {
  const p = record(detail.plan);
  const goal = p.goal ?? p.objective;
  return typeof goal === "string" && goal.trim() !== "" ? goal : null;
}

function planSteps(detail: Record<string, unknown>): string[] {
  const p = record(detail.plan);
  const steps = Array.isArray(p.steps) ? p.steps : [];
  return steps.map((s) => {
    if (typeof s === "string") return s;
    const so = record(s);
    const text = so.description ?? so.text ?? so.title;
    return typeof text === "string" ? text : JSON.stringify(s);
  });
}

function reflectCritique(detail: Record<string, unknown>): string {
  return typeof detail.critique === "string" ? detail.critique : "";
}

function AuxNodeDetail({ item }: { item: AuxNodeItem }) {
  const { t } = useTranslation();
  if (item.kind === "memory_recall" || item.kind === "memory_writeback") {
    const memories = asMemories(item.detail);
    return (
      <>
        {memories.map((m, i) => (
          <div
            key={m.id}
            style={{
              padding: "4px 0",
              borderTop: i === 0 ? "none" : "1px solid var(--ew-border-subtle)",
              color: "var(--ew-text-secondary)",
            }}
          >
            <span
              style={{
                fontSize: 10.5,
                padding: "0 6px",
                borderRadius: 4,
                background: `color-mix(in srgb, ${SUCCESS} 16%, transparent)`,
                color: SUCCESS,
                marginRight: 6,
              }}
            >
              {m.kind}
            </span>
            {m.content}{" "}
            <span style={{ fontFamily: "var(--ew-font-mono)", fontSize: 11 }}>
              {m.importance !== undefined && `· ${t("playground.tl_importance", { v: m.importance.toFixed(2) })} `}
              {m.confidence !== undefined && `· ${t("playground.tl_confidence", { v: m.confidence.toFixed(2) })}`}
            </span>
          </div>
        ))}
      </>
    );
  }
  if (item.kind === "planner") {
    const goal = planGoal(item.detail);
    const steps = planSteps(item.detail);
    return (
      <>
        {goal && <div style={{ color: "var(--ew-text-primary)" }}>{t("playground.tl_goal", { text: goal })}</div>}
        {steps.map((s, i) => (
          <div key={i} style={{ display: "flex", gap: 7, padding: "3px 0", color: "var(--ew-text-secondary)" }}>
            <span>{i + 1}.</span> <span>{s}</span>
          </div>
        ))}
      </>
    );
  }
  if (item.kind === "reflect") {
    return <div style={{ color: "var(--ew-text-secondary)" }}>{reflectCritique(item.detail)}</div>;
  }
  return null;
}

const MARKER_TONE_COLOR: Record<MarkerItem["tone"], string> = {
  warn: WARNING,
  bad: DANGER,
  good: SUCCESS,
  pause: PURPLE,
};

function MarkerRow({ item }: { item: MarkerItem }) {
  const color = MARKER_TONE_COLOR[item.tone];
  const isCircle = item.tone === "good";
  return (
    <div style={{ position: "relative", marginBottom: 10 }}>
      <span
        aria-hidden
        style={{
          position: "absolute",
          left: isCircle ? -21 : -20,
          top: 7,
          width: isCircle ? 9 : 7,
          height: isCircle ? 9 : 7,
          borderRadius: isCircle ? "50%" : 0,
          transform: isCircle ? undefined : "rotate(45deg)",
          background: color,
        }}
      />
      <div
        data-testid="marker"
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 7,
          fontSize: 12,
          color: "var(--ew-text-secondary)",
          padding: "3px 0",
          flexWrap: "wrap",
        }}
      >
        <span
          style={{
            fontFamily: "var(--ew-font-mono)",
            fontSize: 11,
            padding: "1px 6px",
            borderRadius: 4,
            background: `color-mix(in srgb, ${color} 16%, transparent)`,
            color,
          }}
        >
          {item.kind}
        </span>
        {item.text}
      </div>
    </div>
  );
}

function legendDot(color: string, opts: { diamond?: boolean; filled?: boolean } = {}) {
  const { diamond = false, filled = false } = opts;
  return {
    width: diamond ? 8 : 10,
    height: diamond ? 8 : 10,
    borderRadius: diamond ? 0 : "50%",
    border: diamond ? undefined : `2px solid ${color}`,
    background: diamond
      ? color
      : filled
        ? `color-mix(in srgb, ${color} 30%, var(--ew-surface-base))`
        : undefined,
    transform: diamond ? "rotate(45deg)" : undefined,
  };
}

function Legend() {
  const { t } = useTranslation();
  const entries: { color: string; diamond?: boolean; filled?: boolean; label: string }[] = [
    { color: SUCCESS, label: t("playground.tl_legend_agent") },
    { color: SUCCESS, filled: true, label: t("playground.tl_legend_mem") },
    { color: INFO, label: t("playground.tl_legend_plan") },
    { color: WARNING, label: t("playground.tl_legend_reflect") },
    { color: WARNING, diamond: true, label: t("playground.tl_legend_marker") },
    { color: PURPLE, diamond: true, label: t("playground.tl_legend_approval") },
  ];
  return (
    <div
      data-testid="step-timeline-legend"
      style={{
        display: "flex",
        flexWrap: "wrap",
        gap: "6px 14px",
        marginTop: 14,
        paddingTop: 12,
        borderTop: "1px dashed var(--ew-border-subtle)",
      }}
    >
      {entries.map((it, i) => (
        <span
          key={i}
          style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11.5, color: "var(--ew-text-secondary)" }}
        >
          <span style={legendDot(it.color, { diamond: it.diamond, filled: it.filled })} />
          {it.label}
        </span>
      ))}
    </div>
  );
}
