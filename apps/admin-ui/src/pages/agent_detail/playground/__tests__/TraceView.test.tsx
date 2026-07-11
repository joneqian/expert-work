/**
 * TraceView tests — Batch 4b Task 4.
 *
 * Test-time i18n resolves to English (jsdom's navigator.language, per
 * src/i18n's LanguageDetector — see StepTimeline.test.tsx / TimelineFilterBar
 * .test.tsx precedent), so state-text assertions use the English copy.
 */
import { describe, expect, it } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import "../../../../i18n";

import { TraceView } from "../TraceView";
import type { RunTrace, TraceSpan } from "../../../../api/trace_facade";

function makeSpan(
  over: Partial<TraceSpan> & Pick<TraceSpan, "id" | "parentId" | "kind" | "label">,
): TraceSpan {
  return {
    detail: null,
    startMs: 0,
    latencyMs: 0,
    model: null,
    inputTokens: null,
    outputTokens: null,
    costUsd: null,
    input: null,
    output: null,
    ...over,
  };
}

const root = makeSpan({
  id: "r0",
  parentId: null,
  kind: "session",
  label: "会话运行",
  latencyMs: 10000,
});
const llm = makeSpan({
  id: "r1",
  parentId: "r0",
  kind: "llm",
  label: "LLM 调用",
  detail: "主推理",
  startMs: 100,
  latencyMs: 2600,
  model: "glm-4.6",
  inputTokens: 120,
  outputTokens: 340,
  costUsd: 0.0021,
  input: "user: 帮我查天气",
  output: '{"reply":"晴天"}',
});
const tool = makeSpan({
  id: "r2",
  parentId: "r0",
  kind: "tool",
  label: "工具调用",
  detail: "get_weather",
  startMs: 2800,
  latencyMs: 160,
});

function okTrace(spans: TraceSpan[] = [root, llm, tool]): RunTrace {
  return {
    status: "ok",
    trace: { name: "trace-1", latencyMs: 10000, totalCostUsd: 0.0021, spanCount: spans.length },
    spans,
  };
}

describe("TraceView", () => {
  it("renders one row per span (tree order), each span's label/detail + fmtDuration, a waterfall bar per row, and a model chip only where the span has one", () => {
    render(<TraceView trace={okTrace()} />);
    expect(screen.getByTestId("trace-view")).toBeInTheDocument();

    const rows = screen.getAllByTestId("trace-row");
    expect(rows).toHaveLength(3);

    expect(screen.getByText("会话运行")).toBeInTheDocument();
    expect(screen.getByText(/LLM 调用/)).toBeInTheDocument();
    expect(screen.getByText(/主推理/)).toBeInTheDocument();
    expect(screen.getByText(/工具调用/)).toBeInTheDocument();
    expect(screen.getByText(/get_weather/)).toBeInTheDocument();

    // fmtDuration strings on each row's waterfall bar (scoped — the axis
    // header also renders "10.0s" at its 100% tick).
    const bars = screen.getAllByTestId("trace-bar");
    expect(bars).toHaveLength(3);
    expect(bars[0]).toHaveTextContent("10.0s");
    expect(bars[1]).toHaveTextContent("2.6s");
    expect(bars[2]).toHaveTextContent("160ms");

    // Only the llm span carries a model/cost — session + tool rows get no chip.
    expect(screen.getAllByTestId("trace-model-chip")).toHaveLength(1);
    expect(screen.getByTestId("trace-model-chip")).toHaveTextContent("glm-4.6");
    expect(screen.getAllByTestId("trace-cost-chip")).toHaveLength(1);
  });

  it("selecting a row opens the detail panel with that span's input/output (collapsible, empty segments not rendered); the close button clears it", () => {
    render(<TraceView trace={okTrace()} />);
    const rows = screen.getAllByTestId("trace-row");

    fireEvent.click(rows[1]); // llm row — has both input + output
    const detail = screen.getByTestId("trace-detail");
    expect(within(detail).getByTestId("trace-io-input")).toHaveTextContent("帮我查天气");
    expect(within(detail).getByTestId("trace-io-output")).toHaveTextContent('"reply":"晴天"');

    // Collapse the input section — its body should disappear from the DOM.
    const inputHead = within(within(detail).getByTestId("trace-io-input")).getByRole("button");
    fireEvent.click(inputHead);
    expect(within(detail).getByTestId("trace-io-input")).not.toHaveTextContent("帮我查天气");

    // Switch to the tool row — its input/output are both null, so neither
    // io segment renders.
    fireEvent.click(rows[2]);
    const detail2 = screen.getByTestId("trace-detail");
    expect(within(detail2).queryByTestId("trace-io-input")).not.toBeInTheDocument();
    expect(within(detail2).queryByTestId("trace-io-output")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("trace-detail-close"));
    expect(screen.queryByTestId("trace-detail")).not.toBeInTheDocument();
  });

  it.each([
    ["not_ready", "Processing", true],
    ["unavailable", "Unavailable", false],
    ["no_trace", "No trace", false],
  ] as const)("renders the %s degraded state (no tree) with its status text", (status, title, expectRefresh) => {
    render(<TraceView trace={{ status }} />);
    expect(screen.getByTestId("trace-view")).toBeInTheDocument();
    expect(screen.getByText(title)).toBeInTheDocument();
    expect(screen.queryAllByTestId("trace-row")).toHaveLength(0);
    if (expectRefresh) {
      expect(screen.getByTestId("trace-refresh")).toBeInTheDocument();
    } else {
      expect(screen.queryByTestId("trace-refresh")).not.toBeInTheDocument();
    }
  });
});
