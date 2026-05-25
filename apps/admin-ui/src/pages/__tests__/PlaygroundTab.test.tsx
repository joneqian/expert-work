/**
 * PlaygroundTab tests — Stream H.2 PR 3.
 *
 * Both async paths are mocked: ``createSession`` returns a stubbed
 * thread, ``streamRun`` is an async generator we drive frame-by-frame
 * from the test body. This keeps the network layer out of jsdom.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "../../i18n";

import { ApiError } from "../../api/client";
import * as sessionsSdk from "../../api/sessions";
import { PlaygroundTab } from "../agent_detail/PlaygroundTab";
import type { AgentDetailResponse } from "../../api/agents";
import type { SseEvent, ThreadMeta } from "../../api/sessions";

const sampleDetail: AgentDetailResponse = {
  record: {
    id: "11111111-1111-1111-1111-111111111111",
    tenant_id: "22222222-2222-2222-2222-222222222222",
    name: "demo-agent",
    version: "1.0.0",
    status: "active",
    spec_sha256: "abc",
    created_by: "u",
    created_at: "2026-05-25T00:00:00Z",
    updated_at: "2026-05-25T00:00:00Z",
    spec: {},
  },
};

const sampleThread: ThreadMeta = {
  thread_id: "33333333-3333-3333-3333-333333333333",
  tenant_id: "22222222-2222-2222-2222-222222222222",
  agent_name: "demo-agent",
  agent_version: "1.0.0",
  user_id: null,
  status: "active",
  created_by: "u",
  created_at: "2026-05-25T00:00:00Z",
  updated_at: "2026-05-25T00:00:00Z",
};

const createSessionMock = vi.spyOn(sessionsSdk, "createSession");
const streamRunMock = vi.spyOn(sessionsSdk, "streamRun");

beforeEach(() => {
  createSessionMock.mockReset();
  streamRunMock.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

function makeStream(events: SseEvent[]): AsyncGenerator<SseEvent, void, void> {
  return (async function* () {
    for (const e of events) yield e;
  })();
}

describe("PlaygroundTab", () => {
  it("creates a thread on mount and displays its id", async () => {
    createSessionMock.mockResolvedValue(sampleThread);
    render(<PlaygroundTab detail={sampleDetail} />);
    await waitFor(() => {
      expect(createSessionMock).toHaveBeenCalledWith({
        agent_name: "demo-agent",
        agent_version: "1.0.0",
      });
    });
    expect(await screen.findByText(/33333333-3333-3333/)).toBeInTheDocument();
    expect(screen.getByTestId("playground-empty-log")).toBeInTheDocument();
  });

  it("streams events from streamRun and renders them in the log", async () => {
    const user = userEvent.setup();
    createSessionMock.mockResolvedValue(sampleThread);
    streamRunMock.mockReturnValue(
      makeStream([
        {
          id: "1",
          event: "metadata",
          data: { run_id: "r-1" },
          rawData: "",
          receivedAt: "2026-05-25T00:00:01Z",
        },
        {
          id: "2",
          event: "updates",
          data: { agent: { messages: ["hi"] } },
          rawData: "",
          receivedAt: "2026-05-25T00:00:02Z",
        },
        {
          id: "3",
          event: "end",
          data: "ok",
          rawData: "ok",
          receivedAt: "2026-05-25T00:00:03Z",
        },
      ]),
    );
    render(<PlaygroundTab detail={sampleDetail} />);
    await screen.findByText(/33333333-3333-3333/);
    await user.type(screen.getByTestId("playground-input"), "hello");
    await user.click(screen.getByTestId("playground-run"));
    await screen.findByTestId("playground-event-metadata");
    await screen.findByTestId("playground-event-updates");
    await screen.findByTestId("playground-event-end");
    expect(screen.queryByTestId("playground-stop")).not.toBeInTheDocument();
  });

  it("shows a stream-failure alert when streamRun throws", async () => {
    const user = userEvent.setup();
    createSessionMock.mockResolvedValue(sampleThread);
    streamRunMock.mockImplementation(() => {
      return (async function* () {
        throw new Error("boom");
      })();
    });
    render(<PlaygroundTab detail={sampleDetail} />);
    await screen.findByText(/33333333-3333-3333/);
    await user.type(screen.getByTestId("playground-input"), "x");
    await user.click(screen.getByTestId("playground-run"));
    const alert = await screen.findByTestId("playground-stream-error");
    expect(alert).toHaveTextContent("boom");
  });

  it("shows a session-failure alert when createSession rejects", async () => {
    createSessionMock.mockRejectedValue(
      new ApiError("agent not active", "AGENT_NOT_FOUND", 422),
    );
    render(<PlaygroundTab detail={sampleDetail} />);
    const alert = await screen.findByTestId("playground-session-error");
    expect(alert).toHaveTextContent("AGENT_NOT_FOUND");
    expect(screen.getByTestId("playground-run")).toBeDisabled();
  });

  it("disables Run while the input is empty", async () => {
    createSessionMock.mockResolvedValue(sampleThread);
    render(<PlaygroundTab detail={sampleDetail} />);
    await screen.findByText(/33333333-3333-3333/);
    expect(screen.getByTestId("playground-run")).toBeDisabled();
  });
});
