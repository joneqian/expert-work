/**
 * RunSummaryPanel tests — the run-detail "what happened" glance.
 */
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import "../../i18n";

import { RunSummaryPanel } from "../run_detail/RunSummaryPanel";
import type { RunDetail } from "../../api/runs";

function run(overrides: Partial<RunDetail> = {}): RunDetail {
  return {
    run_id: "r",
    thread_id: "t",
    status: "success",
    pending_approval: null,
    trace_id: "trace",
    created_at: "2026-05-26T08:00:00Z",
    finished_at: "2026-05-26T08:01:05Z", // +65s
    tokens: {
      input_tokens: 900,
      output_tokens: 100,
      cache_creation_tokens: 0,
      cache_read_tokens: 250,
      total_tokens: 1000,
      llm_calls: 4,
      models: ["m1", "m2"],
    },
    ...overrides,
  };
}

describe("RunSummaryPanel", () => {
  it("shows duration, compact tokens and the models used", () => {
    render(<RunSummaryPanel run={run()} />);
    expect(screen.getByTestId("run-summary")).toBeInTheDocument();
    expect(screen.getByText("1m 5s")).toBeInTheDocument(); // 65s
    expect(screen.getByText("1.0k")).toBeInTheDocument(); // total tokens
    expect(screen.getByText("m1")).toBeInTheDocument();
    expect(screen.getByText("m2")).toBeInTheDocument();
  });

  it("falls back when a run has no recorded token usage", () => {
    render(<RunSummaryPanel run={run({ tokens: null })} />);
    expect(screen.getByTestId("run-summary-no-tokens")).toBeInTheDocument();
  });
});
