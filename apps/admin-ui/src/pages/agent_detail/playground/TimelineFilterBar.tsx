/**
 * TimelineFilterBar — controlled type-filter chips + search input + count
 * display sitting above `StepTimeline`. Transcribes the wireframe's
 * `.filter-bar` (type chips with `aria-pressed`, `.search` input, `.count`)
 * — see docs/superpowers/specs/2026-07-10-batch3-wireframe.html. Stateless:
 * the parent (Task 5) owns `type` / `query` state and computes `count`.
 */
import { useTranslation } from "react-i18next";

import type { TimelineFilter } from "../../../api/timeline_filter";

export interface TimelineFilterBarProps {
  type: TimelineFilter;
  query: string;
  onType: (t: TimelineFilter) => void;
  onQuery: (q: string) => void;
  count: string;
}

const FILTER_TYPES: readonly TimelineFilter[] = ["all", "tool", "error", "retry"];

const FILTER_LABEL_KEY: Record<TimelineFilter, string> = {
  all: "playground.tl_filter_all",
  tool: "playground.tl_filter_tool",
  error: "playground.tl_filter_error",
  retry: "playground.tl_filter_retry",
};

// Same `var(--ew-*, #hex)` convention StepTimeline.tsx / ToolTimeline.tsx use.
const DANGER = "var(--ew-text-danger, #cf1322)";
const INFO = "var(--ew-text-info, #4c8dff)";

export function TimelineFilterBar({ type, query, onType, onQuery, count }: TimelineFilterBarProps) {
  const { t } = useTranslation();

  return (
    <div
      data-testid="timeline-filter-bar"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        flexWrap: "wrap",
        padding: "8px 10px",
        margin: "2px 0 12px",
        background: "var(--ew-surface-raised)",
        border: "1px solid var(--ew-border-subtle)",
        borderRadius: 6,
      }}
    >
      {FILTER_TYPES.map((f) => {
        const active = f === type;
        const tone = f === "error" ? DANGER : INFO;
        return (
          <button
            key={f}
            type="button"
            data-testid={`timeline-filter-chip-${f}`}
            aria-pressed={active}
            onClick={() => onType(f)}
            style={{
              font: "inherit",
              fontSize: 12,
              padding: "2px 9px",
              borderRadius: 999,
              cursor: "pointer",
              border: `1px solid ${
                active ? `color-mix(in srgb, ${tone} 45%, var(--ew-border-strong))` : "var(--ew-border-strong)"
              }`,
              background: active ? `color-mix(in srgb, ${tone} 20%, transparent)` : "transparent",
              color: active ? tone : "var(--ew-text-secondary)",
            }}
          >
            {t(FILTER_LABEL_KEY[f])}
          </button>
        );
      })}
      <div
        data-testid="timeline-filter-search"
        style={{
          flex: 1,
          minWidth: 150,
          display: "flex",
          alignItems: "center",
          gap: 6,
          background: "var(--ew-surface-base)",
          border: "1px solid var(--ew-border-subtle)",
          borderRadius: 5,
          padding: "3px 8px",
          color: "var(--ew-text-tertiary)",
        }}
      >
        <span aria-hidden>🔍</span>
        <input
          data-testid="timeline-filter-query"
          value={query}
          placeholder={t("playground.tl_search_placeholder")}
          onChange={(e) => onQuery(e.target.value)}
          style={{
            flex: 1,
            background: "transparent",
            border: 0,
            outline: 0,
            color: "var(--ew-text-primary)",
            font: "inherit",
            fontSize: 12,
          }}
        />
      </div>
      <span
        data-testid="timeline-filter-count"
        style={{
          color: "var(--ew-text-tertiary)",
          fontSize: 12,
          fontVariantNumeric: "tabular-nums",
          whiteSpace: "nowrap",
        }}
      >
        {count}
      </span>
    </div>
  );
}
