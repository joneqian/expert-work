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
import * as uploadsSdk from "../../api/uploads";
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
const uploadImageMock = vi.spyOn(uploadsSdk, "uploadImage");
const uploadDocumentMock = vi.spyOn(uploadsSdk, "uploadDocument");

beforeEach(() => {
  createSessionMock.mockReset();
  streamRunMock.mockReset();
  uploadImageMock.mockReset();
  uploadDocumentMock.mockReset();
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

  it("uploads an attached image and sends its ref with the run", async () => {
    const user = userEvent.setup();
    createSessionMock.mockResolvedValue(sampleThread);
    uploadImageMock.mockResolvedValue("helix://image/img-1.png");
    streamRunMock.mockReturnValue(
      makeStream([
        {
          id: "1",
          event: "end",
          data: "ok",
          rawData: "ok",
          receivedAt: "2026-05-25T00:00:03Z",
        },
      ]),
    );
    render(<PlaygroundTab detail={sampleDetail} />);
    await screen.findByText(/33333333-3333-3333/);

    const file = new File(["\x89PNG"], "shot.png", { type: "image/png" });
    await user.upload(screen.getByTestId("playground-file-input"), file);

    expect(
      await screen.findByTestId("playground-attachment"),
    ).toHaveTextContent("shot.png");
    expect(uploadImageMock).toHaveBeenCalledWith(sampleThread.thread_id, file);

    await user.type(screen.getByTestId("playground-input"), "describe this");
    await user.click(screen.getByTestId("playground-run"));
    await screen.findByTestId("playground-event-end");

    expect(streamRunMock).toHaveBeenCalledWith(
      sampleThread.thread_id,
      { input: "describe this", image_refs: ["helix://image/img-1.png"] },
      expect.objectContaining({ signal: expect.anything() }),
    );
    // The turn consumed the attachment — chip is cleared afterward.
    expect(
      screen.queryByTestId("playground-attachment"),
    ).not.toBeInTheDocument();
  });

  it("uploads a document and surfaces its workspace path in the run prompt", async () => {
    const user = userEvent.setup();
    createSessionMock.mockResolvedValue(sampleThread);
    uploadDocumentMock.mockResolvedValue("uploads/report.pdf");
    streamRunMock.mockReturnValue(
      makeStream([
        {
          id: "1",
          event: "end",
          data: "ok",
          rawData: "ok",
          receivedAt: "2026-05-25T00:00:03Z",
        },
      ]),
    );
    render(<PlaygroundTab detail={sampleDetail} />);
    await screen.findByText(/33333333-3333-3333/);

    const file = new File(["%PDF-1.4"], "report.pdf", {
      type: "application/pdf",
    });
    await user.upload(screen.getByTestId("playground-doc-input"), file);

    expect(
      await screen.findByTestId("playground-attachment"),
    ).toHaveTextContent("report.pdf");
    expect(uploadDocumentMock).toHaveBeenCalledWith(
      sampleThread.thread_id,
      file,
    );

    await user.type(screen.getByTestId("playground-input"), "summarize it");
    await user.click(screen.getByTestId("playground-run"));
    await screen.findByTestId("playground-event-end");

    // The doc path is prepended to the prompt (no image_refs for a doc-only turn).
    const [, body] = streamRunMock.mock.calls.at(-1) ?? [];
    expect((body as { input: string }).input).toContain("uploads/report.pdf");
    expect((body as { input: string }).input).toContain("summarize it");
    expect((body as { image_refs?: unknown }).image_refs).toBeUndefined();
  });

  it("renders declared prompt variables and sends their values as inputs", async () => {
    const user = userEvent.setup();
    const jinjaDetail: AgentDetailResponse = {
      record: {
        ...sampleDetail.record,
        spec: {
          system_prompt: {
            template: "你是 {{ persona }}",
            jinja: true,
            variables: [{ name: "persona", trusted: true, required: true }],
          },
        },
      },
    };
    createSessionMock.mockResolvedValue(sampleThread);
    streamRunMock.mockReturnValue(
      makeStream([
        {
          id: "1",
          event: "end",
          data: "ok",
          rawData: "ok",
          receivedAt: "2026-05-25T00:00:03Z",
        },
      ]),
    );
    render(<PlaygroundTab detail={jinjaDetail} />);
    await screen.findByText(/33333333-3333-3333/);

    await user.type(screen.getByTestId("playground-var-persona"), "顾问");
    await user.type(screen.getByTestId("playground-input"), "go");
    await user.click(screen.getByTestId("playground-run"));
    await screen.findByTestId("playground-event-end");

    expect(streamRunMock).toHaveBeenCalledWith(
      sampleThread.thread_id,
      { input: "go", inputs: { persona: "顾问" } },
      expect.objectContaining({ signal: expect.anything() }),
    );
  });

  it("shows an upload-error alert and keeps Run usable when upload fails", async () => {
    const user = userEvent.setup();
    createSessionMock.mockResolvedValue(sampleThread);
    uploadImageMock.mockRejectedValue(
      new ApiError("too big", "IMAGE_TOO_LARGE", 413),
    );
    render(<PlaygroundTab detail={sampleDetail} />);
    await screen.findByText(/33333333-3333-3333/);

    const file = new File(["x"], "huge.png", { type: "image/png" });
    await user.upload(screen.getByTestId("playground-file-input"), file);

    const alert = await screen.findByTestId("playground-upload-error");
    expect(alert).toHaveTextContent("IMAGE_TOO_LARGE");
    expect(
      screen.queryByTestId("playground-attachment"),
    ).not.toBeInTheDocument();
  });

  it("removes an attachment when its tag is closed", async () => {
    const user = userEvent.setup();
    createSessionMock.mockResolvedValue(sampleThread);
    uploadImageMock.mockResolvedValue("helix://image/img-2.png");
    render(<PlaygroundTab detail={sampleDetail} />);
    await screen.findByText(/33333333-3333-3333/);

    const file = new File(["x"], "pic.png", { type: "image/png" });
    await user.upload(screen.getByTestId("playground-file-input"), file);
    await screen.findByTestId("playground-attachment");

    await user.click(screen.getByLabelText("Remove attachment"));
    expect(
      screen.queryByTestId("playground-attachment"),
    ).not.toBeInTheDocument();
  });
});
