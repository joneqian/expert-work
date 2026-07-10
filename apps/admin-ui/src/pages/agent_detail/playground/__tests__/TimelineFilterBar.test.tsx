import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import "../../../../i18n";

import { TimelineFilterBar } from "../TimelineFilterBar";
import type { TimelineFilter } from "../../../../api/timeline_filter";

// Test-time i18n resolves to English (jsdom's navigator.language, per
// src/i18n's LanguageDetector — see StepTimeline.test.tsx precedent), so
// assertions target testids/attrs rather than locale-specific chip text.

const CHIP_TESTIDS: Record<TimelineFilter, string> = {
  all: "timeline-filter-chip-all",
  tool: "timeline-filter-chip-tool",
  error: "timeline-filter-chip-error",
  retry: "timeline-filter-chip-retry",
};

describe("TimelineFilterBar", () => {
  it("renders all four type chips with only the active one aria-pressed", () => {
    render(
      <TimelineFilterBar
        type="tool"
        query=""
        onType={() => {}}
        onQuery={() => {}}
        count="9 项 · 2 工具 · 1 失败"
      />,
    );
    expect(screen.getByTestId("timeline-filter-chip-all")).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByTestId("timeline-filter-chip-tool")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("timeline-filter-chip-error")).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByTestId("timeline-filter-chip-retry")).toHaveAttribute("aria-pressed", "false");
  });

  it.each(["all", "tool", "error", "retry"] as const)(
    "calls onType(%s) when that chip is clicked",
    (filter) => {
      const onType = vi.fn();
      render(
        <TimelineFilterBar type="all" query="" onType={onType} onQuery={() => {}} count="9 项" />,
      );
      fireEvent.click(screen.getByTestId(CHIP_TESTIDS[filter]));
      expect(onType).toHaveBeenCalledWith(filter);
    },
  );

  it("calls onQuery with the typed search text", () => {
    const onQuery = vi.fn();
    render(
      <TimelineFilterBar type="all" query="" onType={() => {}} onQuery={onQuery} count="9 项 · 2 工具 · 1 失败" />,
    );
    fireEvent.change(screen.getByTestId("timeline-filter-query"), { target: { value: "exec_python" } });
    expect(onQuery).toHaveBeenCalledWith("exec_python");
  });

  it("reflects the query prop in the input's value", () => {
    render(<TimelineFilterBar type="all" query="timeout" onType={() => {}} onQuery={() => {}} count="9 项" />);
    expect(screen.getByTestId("timeline-filter-query")).toHaveValue("timeout");
  });

  it("displays the pre-formatted count string", () => {
    render(
      <TimelineFilterBar
        type="all"
        query=""
        onType={() => {}}
        onQuery={() => {}}
        count="9 项 · 2 工具 · 1 失败"
      />,
    );
    expect(screen.getByTestId("timeline-filter-count")).toHaveTextContent("9 项 · 2 工具 · 1 失败");
  });
});
