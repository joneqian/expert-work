/**
 * TraceView — Batch 4b Task 4. Renders a run's normalized Langfuse trace
 * (Task 3's `RunTrace`/`TraceSpan` facade DTO) as the wireframe v4
 * two-pane view: a left indented operation tree + a right time-axis
 * waterfall, row-aligned, with a below-the-fold detail panel for
 * whichever row is selected. See
 * docs/superpowers/specs/2026-07-11-batch4b-wireframe.html (`.wf` /
 * `.rw` / `.tw` / `.tdot` / `.gbar` / `.detail` / `.state`) for the
 * authoritative visual structure this transcribes.
 *
 * Scope notes (kept close to the Task description's explicit field
 * list rather than every wireframe pixel — see task-4-report.md):
 *  - Span `label`/`detail` are opaque passthrough text — the facade
 *    (control_plane/api/trace_facade.py `_classify`) already emits
 *    human-readable labels ("LLM 调用"/"工具调用"/…) and `detail` is raw
 *    data (tool name / call purpose), not something this component
 *    translates or reformats.
 *  - The detail panel's meta row sticks to the Task description's
 *    explicit field list (kind/latency/model/tokens/cost) — `TraceSpan`
 *    carries no absolute wall-clock timestamp, so the wireframe's
 *    "09:03:12 → …" range chip is dropped rather than fabricated.
 *  - Per-row subtree collapse (the wireframe's `tog()`) isn't
 *    implemented — every row always renders; only the detail panel's
 *    input/output sections are collapsible, per the Task description
 *    ("io 各可折叠").
 *  - The axis's endless background gridlines (`.axis .g`, a CSS
 *    `bottom:-2000px` hack) and the `.trace-sum` / `.legend` strips are
 *    skipped — they're outside the "核心结构" list in the task brief.
 */
import { useState, type KeyboardEvent } from "react";
import { RefreshCw, X } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { RunTrace, TraceSpan, TraceStatus } from "../../../api/trace_facade";
import { fmtDuration } from "./duration_format";
import { buildRows, isWideBar, type TraceRowData } from "./trace_tree";

const ACCENT = "var(--ew-text-info, #4c8dff)";
const SUCCESS = "var(--ew-text-success, #3ecf8e)";
const WARNING = "var(--ew-text-warning, #e8a33d)";
const DANGER = "var(--ew-text-danger, #f0616d)";
const PURPLE = "var(--ew-accent-violet, #b18cff)";
const MUTED = "var(--ew-text-tertiary)";
const LANE = "300px";

export interface TraceViewProps {
  trace: RunTrace;
  /** Called when the user clicks "Refresh" on the `not_ready` degraded
   *  state — the caller should trigger a refetch of `trace` (see
   *  PlaygroundTab.tsx). Omitted → the refresh button doesn't render. */
  onRefresh?: () => void;
}

export function TraceView({ trace, onRefresh }: TraceViewProps) {
  // `status: "ok"` doesn't type-narrow `trace`/`spans` into defined (both
  // stay optional on RunTrace) — guard defensively rather than asserting,
  // degrading to "unavailable" on a malformed ok payload instead of crashing.
  const spans = trace.spans;
  // The waterfall renders nothing unless there's at least one root span
  // (`parentId === null`) to start the tree walk from. Two ingestion-in-
  // progress shapes have spans yet no root: zero spans, or child spans that
  // landed before their session-root parent (every `parentId` dangles). Both
  // must surface as refreshable `not_ready`, never a bare axis with no bars.
  const hasRoot = Array.isArray(spans) && spans.some((s) => s.parentId === null);
  if (trace.status !== "ok" || !trace.trace || !spans || !hasRoot) {
    // A genuinely malformed ok (missing trace/spans) degrades to `unavailable`;
    // an ok trace that's simply still ingesting (no root yet) → `not_ready`.
    const status: Exclude<TraceStatus, "ok"> =
      trace.status !== "ok"
        ? trace.status
        : trace.trace && spans
          ? "not_ready"
          : "unavailable";
    return (
      <div data-testid="trace-view">
        <TraceStateCard status={status} onRefresh={onRefresh} />
      </div>
    );
  }

  return (
    <div data-testid="trace-view">
      <TraceTree spans={spans} totalMs={trace.trace.latencyMs} />
    </div>
  );
}


function TraceTree({ spans, totalMs }: { spans: readonly TraceSpan[]; totalMs: number }) {
  const { t } = useTranslation();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const rows = buildRows(spans);
  const selected = spans.find((s) => s.id === selectedId) ?? null;

  return (
    <div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: `${LANE} 1fr`,
          background: "var(--ew-surface-raised)",
          borderBottom: "1px solid var(--ew-border-strong)",
        }}
      >
        <div
          style={{
            padding: "6px 12px",
            fontSize: 11,
            letterSpacing: "0.05em",
            textTransform: "uppercase",
            color: MUTED,
          }}
        >
          {t("playground.tr_col_action")}
        </div>
        <div style={{ padding: "6px 12px" }}>
          <Axis totalMs={totalMs} />
        </div>
      </div>
      {rows.map((row) => (
        <TraceRow
          key={row.span.id}
          row={row}
          totalMs={totalMs}
          selected={row.span.id === selectedId}
          onSelect={() => setSelectedId(row.span.id)}
        />
      ))}
      {selected && <TraceDetail span={selected} onClose={() => setSelectedId(null)} />}
    </div>
  );
}

function Axis({ totalMs }: { totalMs: number }) {
  const fractions = [0, 0.25, 0.5, 0.75, 1];
  return (
    <div style={{ position: "relative", height: 14 }}>
      {fractions.map((f, i) => (
        <span
          key={f}
          style={{
            position: "absolute",
            left: `${f * 100}%`,
            top: 0,
            fontFamily: "var(--ew-font-mono)",
            fontSize: 10,
            color: MUTED,
            fontVariantNumeric: "tabular-nums",
            transform:
              i === 0 ? undefined : i === fractions.length - 1 ? "translateX(-100%)" : "translateX(-50%)",
          }}
        >
          {fmtDuration(Math.round(f * totalMs))}
        </span>
      ))}
    </div>
  );
}

function kindDotColor(kind: TraceSpan["kind"]): string {
  if (kind === "llm") return ACCENT;
  if (kind === "tool") return PURPLE;
  return MUTED;
}

function kindBarColor(kind: TraceSpan["kind"]): string {
  if (kind === "llm") return `color-mix(in srgb, ${ACCENT} 62%, transparent)`;
  if (kind === "tool") return PURPLE;
  return `color-mix(in srgb, ${MUTED} 45%, transparent)`;
}

function TreeGuideCell({ variant }: { variant: "v" | "elbow" | "blank" }) {
  return (
    <span aria-hidden style={{ position: "relative", width: 17, flex: "0 0 auto", alignSelf: "stretch" }}>
      {variant === "v" && (
        <span
          style={{
            position: "absolute",
            left: 8,
            top: 0,
            bottom: 0,
            width: 1,
            background: "var(--ew-border-strong)",
          }}
        />
      )}
      {variant === "elbow" && (
        <>
          <span
            style={{
              position: "absolute",
              left: 8,
              top: 0,
              height: "50%",
              width: 1,
              background: "var(--ew-border-strong)",
            }}
          />
          <span
            style={{
              position: "absolute",
              left: 8,
              top: "50%",
              width: 9,
              height: 1,
              background: "var(--ew-border-strong)",
            }}
          />
        </>
      )}
    </span>
  );
}

function GanttBar({ span, totalMs }: { span: TraceSpan; totalMs: number }) {
  const leftPct = totalMs > 0 ? (span.startMs / totalMs) * 100 : 0;
  const widthPct = totalMs > 0 ? (span.latencyMs / totalMs) * 100 : 0;
  const wide = isWideBar(widthPct);
  return (
    <div
      data-testid="trace-bar"
      style={{
        position: "absolute",
        left: `${leftPct}%`,
        width: `${widthPct}%`,
        minWidth: 3,
        height: 15,
        borderRadius: 3,
        display: "flex",
        alignItems: "center",
        background: kindBarColor(span.kind),
      }}
    >
      <span
        style={{
          fontFamily: "var(--ew-font-mono)",
          fontVariantNumeric: "tabular-nums",
          fontSize: 11,
          whiteSpace: "nowrap",
          color: wide ? "var(--ew-text-primary)" : "var(--ew-text-secondary)",
          marginLeft: wide ? 6 : "calc(100% + 6px)",
        }}
      >
        {fmtDuration(span.latencyMs)}
      </span>
    </div>
  );
}

function TraceRow({
  row,
  totalMs,
  selected,
  onSelect,
}: {
  row: TraceRowData;
  totalMs: number;
  selected: boolean;
  onSelect: () => void;
}) {
  const { span, depth, continues } = row;

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>): void => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onSelect();
    }
  };

  return (
    <div
      data-testid="trace-row"
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={onKeyDown}
      style={{
        display: "grid",
        gridTemplateColumns: `${LANE} 1fr`,
        alignItems: "stretch",
        minHeight: 31,
        borderTop: "1px solid var(--ew-border-subtle)",
        cursor: "pointer",
        background: selected ? `color-mix(in srgb, ${ACCENT} 13%, transparent)` : undefined,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 7, padding: "0 12px 0 0", minWidth: 0 }}>
        {continues.map((cont, i) => (
          <TreeGuideCell key={i} variant={i === depth - 1 ? "elbow" : cont ? "v" : "blank"} />
        ))}
        <span aria-hidden style={{ width: 14, textAlign: "center", color: MUTED, fontSize: 10, flex: "0 0 auto" }}>
          ·
        </span>
        <span
          aria-hidden
          style={{ width: 8, height: 8, borderRadius: 2, flex: "0 0 auto", background: kindDotColor(span.kind) }}
        />
        <span
          style={{
            fontFamily: "var(--ew-font-mono)",
            fontSize: 12.5,
            color: "var(--ew-text-primary)",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
            minWidth: 0,
          }}
        >
          {span.label}
          {span.detail !== null && <span style={{ color: MUTED }}> · {span.detail}</span>}
        </span>
        <span style={{ display: "inline-flex", gap: 5, flex: "0 0 auto" }}>
          {span.model !== null && (
            <span
              data-testid="trace-model-chip"
              style={{
                fontSize: 11,
                fontFamily: "var(--ew-font-mono)",
                padding: "0 5px",
                borderRadius: 4,
                color: ACCENT,
                background: `color-mix(in srgb, ${ACCENT} 12%, transparent)`,
              }}
            >
              {span.model}
            </span>
          )}
          {span.costUsd !== null && (
            <span
              data-testid="trace-cost-chip"
              style={{
                fontSize: 11,
                fontFamily: "var(--ew-font-mono)",
                padding: "0 5px",
                borderRadius: 4,
                color: SUCCESS,
                background: `color-mix(in srgb, ${SUCCESS} 13%, transparent)`,
              }}
            >
              ${span.costUsd.toFixed(4)}
            </span>
          )}
        </span>
      </div>
      <div style={{ position: "relative", padding: "0 12px", display: "flex", alignItems: "center" }}>
        <GanttBar span={span} totalMs={totalMs} />
      </div>
    </div>
  );
}

function IoSection({
  testId,
  title,
  hint,
  content,
}: {
  testId: string;
  title: string;
  hint: string;
  content: string | null;
}) {
  const [expanded, setExpanded] = useState(true);
  if (content === null) return null;

  const toggle = (): void => setExpanded((v) => !v);
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>): void => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      toggle();
    }
  };

  return (
    <div data-testid={testId} style={{ borderBottom: "1px solid var(--ew-border-subtle)" }}>
      <div
        role="button"
        tabIndex={0}
        onClick={toggle}
        onKeyDown={onKeyDown}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "8px 13px",
          fontSize: 12,
          color: "var(--ew-text-secondary)",
          cursor: "pointer",
        }}
      >
        <span aria-hidden style={{ color: MUTED, fontSize: 10 }}>
          {expanded ? "▾" : "▸"}
        </span>
        {title}
        <span
          style={{
            fontSize: 10,
            color: MUTED,
            border: "1px solid var(--ew-border-subtle)",
            borderRadius: 999,
            padding: "0 6px",
          }}
        >
          {hint}
        </span>
      </div>
      {expanded && (
        <pre
          style={{
            margin: 0,
            padding: "0 13px 12px 30px",
            fontFamily: "var(--ew-font-mono)",
            fontSize: 12,
            lineHeight: 1.55,
            color: "var(--ew-text-secondary)",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            maxHeight: 180,
            overflow: "auto",
          }}
        >
          {content}
        </pre>
      )}
    </div>
  );
}

function TraceDetail({ span, onClose }: { span: TraceSpan; onClose: () => void }) {
  const { t } = useTranslation();
  const tokenParts: string[] = [];
  if (span.inputTokens !== null) tokenParts.push(`in ${span.inputTokens}`);
  if (span.outputTokens !== null) tokenParts.push(`out ${span.outputTokens}`);

  return (
    <div
      data-testid="trace-detail"
      style={{
        border: "1px solid var(--ew-border-strong)",
        borderTop: 0,
        borderRadius: "0 0 6px 6px",
        background: "var(--ew-surface-raised)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          flexWrap: "wrap",
          padding: "10px 13px",
          borderBottom: "1px solid var(--ew-border-subtle)",
        }}
      >
        <span style={{ display: "inline-flex", alignItems: "center", gap: 7, fontSize: 13, fontWeight: 600 }}>
          <span aria-hidden style={{ width: 8, height: 8, borderRadius: 2, background: kindDotColor(span.kind) }} />
          {span.label}
          {span.detail !== null && (
            <span style={{ color: MUTED, fontWeight: 400 }}> · {span.detail}</span>
          )}
        </span>
        <span
          style={{
            display: "flex",
            gap: 12,
            flexWrap: "wrap",
            fontFamily: "var(--ew-font-mono)",
            fontVariantNumeric: "tabular-nums",
            fontSize: 11.5,
            color: MUTED,
          }}
        >
          <span>{span.kind.toUpperCase()}</span>
          <span>
            {t("playground.tr_detail_latency")}{" "}
            <b style={{ color: "var(--ew-text-secondary)", fontWeight: 500 }}>{fmtDuration(span.latencyMs)}</b>
          </span>
          {span.model !== null && (
            <span>
              {t("playground.tr_detail_model")}{" "}
              <b style={{ color: "var(--ew-text-secondary)", fontWeight: 500 }}>{span.model}</b>
            </span>
          )}
          {tokenParts.length > 0 && (
            <span>
              {t("playground.tr_detail_tokens")}{" "}
              <b style={{ color: "var(--ew-text-secondary)", fontWeight: 500 }}>{tokenParts.join(" / ")}</b>
            </span>
          )}
          {span.costUsd !== null && (
            <span>
              {t("playground.tr_detail_cost")}{" "}
              <b style={{ color: "var(--ew-text-secondary)", fontWeight: 500 }}>${span.costUsd.toFixed(4)}</b>
            </span>
          )}
        </span>
        <button
          type="button"
          onClick={onClose}
          aria-label={t("playground.tr_detail_close")}
          data-testid="trace-detail-close"
          style={{
            marginLeft: "auto",
            border: 0,
            background: "transparent",
            color: MUTED,
            cursor: "pointer",
            padding: "0 4px",
          }}
        >
          <X size={14} strokeWidth={1.75} />
        </button>
      </div>
      <IoSection
        testId="trace-io-input"
        title={t("playground.tr_io_input")}
        hint={t("playground.tr_io_input_hint")}
        content={span.input}
      />
      <IoSection
        testId="trace-io-output"
        title={t("playground.tr_io_output")}
        hint={t("playground.tr_io_output_hint")}
        content={span.output}
      />
    </div>
  );
}

const STATE_TONE: Record<Exclude<TraceStatus, "ok">, string> = {
  not_ready: WARNING,
  unavailable: DANGER,
  no_trace: MUTED,
};

function TraceStateCard({
  status,
  onRefresh,
}: {
  status: Exclude<TraceStatus, "ok">;
  onRefresh?: () => void;
}) {
  const { t } = useTranslation();
  const copy: Record<Exclude<TraceStatus, "ok">, { title: string; msg: string }> = {
    not_ready: {
      title: t("playground.tr_state_not_ready_title"),
      msg: t("playground.tr_state_not_ready_msg"),
    },
    unavailable: {
      title: t("playground.tr_state_unavailable_title"),
      msg: t("playground.tr_state_unavailable_msg"),
    },
    no_trace: {
      title: t("playground.tr_state_no_trace_title"),
      msg: t("playground.tr_state_no_trace_msg"),
    },
  };
  const { title, msg } = copy[status];

  return (
    <div
      data-testid="trace-state"
      style={{
        border: "1px solid var(--ew-border-subtle)",
        borderRadius: 6,
        background: "var(--ew-surface-base)",
        padding: "14px 13px",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 12, marginBottom: 7 }}>
        <span
          aria-hidden
          style={{ width: 7, height: 7, borderRadius: "50%", flex: "0 0 auto", background: STATE_TONE[status] }}
        />
        <strong>{title}</strong>
      </div>
      <p style={{ margin: 0, color: "var(--ew-text-secondary)", fontSize: 12.5, lineHeight: 1.5 }}>{msg}</p>
      {status === "not_ready" && onRefresh && (
        <button
          type="button"
          data-testid="trace-refresh"
          onClick={onRefresh}
          style={{
            marginTop: 9,
            font: "inherit",
            fontSize: 12,
            border: "1px solid var(--ew-border-strong)",
            background: "var(--ew-surface-raised)",
            color: "var(--ew-text-secondary)",
            padding: "3px 11px",
            borderRadius: 5,
            cursor: "pointer",
            display: "inline-flex",
            alignItems: "center",
            gap: 5,
          }}
        >
          <RefreshCw size={12} strokeWidth={1.75} />
          {t("common.refresh")}
        </button>
      )}
    </div>
  );
}
