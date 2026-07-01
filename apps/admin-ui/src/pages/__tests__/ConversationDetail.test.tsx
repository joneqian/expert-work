/**
 * ConversationDetail tests — the conversation-centric operations view.
 *
 * ``getConversation`` is stubbed; the page renders under a routed
 * MemoryRouter so ``useParams`` resolves ``:threadId``. Asserts the
 * summary rollup + the run list drill-down target.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import "../../i18n";

import * as convoSdk from "../../api/conversations";
import { ConversationDetail } from "../ConversationDetail";
import type { ConversationDetail as ConversationDetailModel } from "../../api/conversations";

const THREAD_ID = "44444444-4444-4444-4444-444444444444";

const CONVO: ConversationDetailModel = {
  thread_id: THREAD_ID,
  tenant_id: "22222222-2222-2222-2222-222222222222",
  user_id: "88888888-8888-8888-8888-888888888888",
  agent_name: "code-reviewer",
  agent_version: "1.0.0",
  title: "refund question",
  status: "active",
  created_at: "2026-06-30T12:00:00Z",
  updated_at: "2026-06-30T12:05:00Z",
  run_count: 2,
  error_count: 1,
  pending_count: 0,
  last_run_at: "2026-06-30T12:05:00Z",
  tokens: {
    input_tokens: 150,
    output_tokens: 30,
    cache_creation_tokens: 0,
    cache_read_tokens: 0,
    total_tokens: 180,
    llm_calls: 2,
    models: ["claude-sonnet-4-5"],
  },
  runs: [
    {
      run_id: "33333333-3333-3333-3333-333333333333",
      thread_id: THREAD_ID,
      user_id: "88888888-8888-8888-8888-888888888888",
      status: "success",
      is_resume: false,
      error: null,
      created_at: "2026-06-30T12:00:00Z",
      updated_at: "2026-06-30T12:01:00Z",
      finished_at: "2026-06-30T12:01:00Z",
      trace_id: "tr-1",
      tokens: null,
    },
    {
      run_id: "33333333-3333-3333-3333-333333333334",
      thread_id: THREAD_ID,
      user_id: "88888888-8888-8888-8888-888888888888",
      status: "error",
      is_resume: false,
      error: "boom",
      created_at: "2026-06-30T12:05:00Z",
      updated_at: "2026-06-30T12:05:30Z",
      finished_at: "2026-06-30T12:05:30Z",
      trace_id: "tr-2",
      tokens: null,
    },
  ],
};

function renderPage() {
  return render(
    <MemoryRouter initialEntries={[`/conversations/${THREAD_ID}`]}>
      <Routes>
        <Route path="/conversations/:threadId" element={<ConversationDetail />} />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => vi.restoreAllMocks());

describe("ConversationDetail", () => {
  it("renders the conversation summary + its run list", async () => {
    vi.spyOn(convoSdk, "getConversation").mockResolvedValue(CONVO);

    renderPage();

    await waitFor(() =>
      expect(screen.getByTestId("conversation-detail-root")).toBeInTheDocument(),
    );
    // Summary token rollup.
    expect(screen.getByTestId("conversation-tokens")).toBeInTheDocument();
    expect(screen.getByText("claude-sonnet-4-5")).toBeInTheDocument();
    // Run list with the failed run's error surfaced.
    expect(screen.getByTestId("conversation-runs-table")).toBeInTheDocument();
    expect(
      screen.getByTestId("conversation-run-error-33333333-3333-3333-3333-333333333334"),
    ).toBeInTheDocument();
  });

  it("surfaces SDK errors in an alert", async () => {
    vi.spyOn(convoSdk, "getConversation").mockRejectedValue(new Error("boom"));
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId("conversation-detail-error")).toBeInTheDocument(),
    );
  });
});
