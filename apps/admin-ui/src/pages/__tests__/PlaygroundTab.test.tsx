/**
 * PlaygroundTab tests — Stream H.2 PR 3.
 *
 * Both async paths are mocked: ``createSession`` returns a stubbed
 * thread, ``streamRun`` is an async generator we drive frame-by-frame
 * from the test body. This keeps the network layer out of jsdom.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import "../../i18n";
import i18n from "../../i18n";

import * as approvalsSdk from "../../api/approvals";
import { ApiError } from "../../api/client";
import * as membersSdk from "../../api/members";
import * as rateCardSdk from "../../api/rate_card";
import * as runsSdk from "../../api/runs";
import * as sessionsSdk from "../../api/sessions";
import * as uploadsSdk from "../../api/uploads";
import { PlaygroundTab } from "../agent_detail/PlaygroundTab";
import type { AgentDetailResponse } from "../../api/agents";
import type { ApprovalItem } from "../../api/approvals";
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
const listMembersMock = vi.spyOn(membersSdk, "listMembers");
const getWorkspaceMock = vi.spyOn(sessionsSdk, "getSessionWorkspace");
const listSessionsMock = vi.spyOn(sessionsSdk, "listSessions");
const listRateCardsMock = vi.spyOn(rateCardSdk, "listRateCards");
const listApprovalsMock = vi.spyOn(approvalsSdk, "listApprovals");
const decideApprovalsMock = vi.spyOn(approvalsSdk, "decideApprovals");
const streamRunEventsMock = vi.spyOn(runsSdk, "streamRunEvents");

beforeEach(() => {
  createSessionMock.mockReset();
  streamRunMock.mockReset();
  uploadImageMock.mockReset();
  uploadDocumentMock.mockReset();
  listMembersMock.mockReset();
  listMembersMock.mockResolvedValue({ items: [], total: 0 });
  getWorkspaceMock.mockReset();
  getWorkspaceMock.mockResolvedValue({ workspace: null, artifacts: [] });
  listSessionsMock.mockReset();
  listSessionsMock.mockResolvedValue([]);
  listRateCardsMock.mockReset();
  listRateCardsMock.mockResolvedValue([]);
  listApprovalsMock.mockReset();
  listApprovalsMock.mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 });
  decideApprovalsMock.mockReset();
  decideApprovalsMock.mockResolvedValue({ results: [], succeeded: 0 });
  streamRunEventsMock.mockReset();
  streamRunEventsMock.mockReturnValue(makeStream([]));
});

afterEach(() => {
  vi.clearAllMocks();
});

function makeStream(events: SseEvent[]): AsyncGenerator<SseEvent, void, void> {
  return (async function* () {
    for (const e of events) yield e;
  })();
}

// The per-turn run-detail link uses react-router <Link>, so every render needs
// a Router context.
function renderPg(detail: AgentDetailResponse = sampleDetail) {
  return render(
    <MemoryRouter>
      <PlaygroundTab detail={detail} />
    </MemoryRouter>,
  );
}

describe("PlaygroundTab", () => {
  it("creates a thread on mount and displays its id", async () => {
    createSessionMock.mockResolvedValue(sampleThread);
    renderPg();
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
    renderPg();
    await screen.findByText(/33333333-3333-3333/);
    await user.type(screen.getByTestId("playground-input"), "hello");
    // Raw-event view to assert the individual frames (default view is the
    // tool-call timeline, which only surfaces tool calls).
    await user.click(screen.getByText(i18n.t("event_stream.view_raw")));
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
    renderPg();
    await screen.findByText(/33333333-3333-3333/);
    await user.type(screen.getByTestId("playground-input"), "x");
    await user.click(screen.getByTestId("playground-run"));
    const alert = await screen.findByTestId("playground-turn-error");
    expect(alert).toHaveTextContent("boom");
  });

  it("shows a session-failure alert when createSession rejects", async () => {
    createSessionMock.mockRejectedValue(
      new ApiError("agent not active", "AGENT_NOT_FOUND", 422),
    );
    renderPg();
    const alert = await screen.findByTestId("playground-session-error");
    expect(alert).toHaveTextContent("AGENT_NOT_FOUND");
    expect(screen.getByTestId("playground-run")).toBeDisabled();
  });

  it("disables Run while the input is empty", async () => {
    createSessionMock.mockResolvedValue(sampleThread);
    renderPg();
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
    renderPg();
    await screen.findByText(/33333333-3333-3333/);

    const file = new File(["\x89PNG"], "shot.png", { type: "image/png" });
    await user.upload(screen.getByTestId("playground-file-input"), file);

    expect(
      await screen.findByTestId("playground-attachment"),
    ).toHaveTextContent("shot.png");
    expect(uploadImageMock).toHaveBeenCalledWith(sampleThread.thread_id, file);

    await user.type(screen.getByTestId("playground-input"), "describe this");
    await user.click(screen.getByTestId("playground-run"));
    await waitFor(() => expect(screen.queryByTestId("playground-stop")).not.toBeInTheDocument());

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
    renderPg();
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
    await waitFor(() => expect(screen.queryByTestId("playground-stop")).not.toBeInTheDocument());

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
    renderPg(jinjaDetail);
    await screen.findByText(/33333333-3333-3333/);

    await user.type(screen.getByTestId("playground-var-persona"), "顾问");
    await user.type(screen.getByTestId("playground-input"), "go");
    await user.click(screen.getByTestId("playground-run"));
    await waitFor(() => expect(screen.queryByTestId("playground-stop")).not.toBeInTheDocument());

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
    renderPg();
    await screen.findByText(/33333333-3333-3333/);

    const file = new File(["x"], "huge.png", { type: "image/png" });
    await user.upload(screen.getByTestId("playground-file-input"), file);

    const alert = await screen.findByTestId("playground-upload-error");
    expect(alert).toHaveTextContent("IMAGE_TOO_LARGE");
    expect(
      screen.queryByTestId("playground-attachment"),
    ).not.toBeInTheDocument();
  });

  it("runs as another user when a user_id is entered (impersonation)", async () => {
    const user = userEvent.setup();
    createSessionMock.mockResolvedValue(sampleThread);
    renderPg();
    await screen.findByText(/33333333-3333-3333/);
    createSessionMock.mockClear();

    const target = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
    // The AutoComplete wraps a real input; type the target user_id into it.
    const userField = screen.getByLabelText(
      i18n.t("playground.run_as_label"),
    );
    await user.type(userField, target);
    // Changing the user re-binds a fresh thread with run_as_user_id.
    await waitFor(() =>
      expect(createSessionMock).toHaveBeenCalledWith({
        agent_name: "demo-agent",
        agent_version: "1.0.0",
        run_as_user_id: target,
      }),
    );
  });

  it("accumulates turns across runs and parses per-turn token usage", async () => {
    const user = userEvent.setup();
    createSessionMock.mockResolvedValue(sampleThread);
    const endFrame = (text: string, input: number): SseEvent[] => [
      {
        id: "u",
        event: "updates",
        data: {
          agent: {
            messages: [
              {
                type: "ai",
                content: text,
                usage_metadata: {
                  input_tokens: input,
                  output_tokens: 10,
                  total_tokens: input + 10,
                },
              },
            ],
          },
        },
        rawData: "",
        receivedAt: "2026-05-25T00:00:02Z",
      },
      {
        id: "e",
        event: "end",
        data: "ok",
        rawData: "ok",
        receivedAt: "2026-05-25T00:00:03Z",
      },
    ];
    streamRunMock.mockReturnValueOnce(makeStream(endFrame("first answer", 100)));
    streamRunMock.mockReturnValueOnce(makeStream(endFrame("second answer", 200)));

    renderPg();
    await screen.findByText(/33333333-3333-3333/);

    await user.type(screen.getByTestId("playground-input"), "q1");
    await user.click(screen.getByTestId("playground-run"));
    await screen.findByText("first answer");

    await user.type(screen.getByTestId("playground-input"), "q2");
    await user.click(screen.getByTestId("playground-run"));
    await screen.findByText("second answer");

    // Both turns persist (not wiped) + usage chips render per turn.
    expect(screen.getAllByTestId("playground-turn")).toHaveLength(2);
    expect(screen.getAllByTestId("playground-usage")).toHaveLength(2);
    // The thread is reused across turns (multi-turn continuation).
    expect(streamRunMock.mock.calls.every(([tid]) => tid === sampleThread.thread_id)).toBe(true);
  });

  it("shows per-turn cost + step + a run-detail link", async () => {
    const user = userEvent.setup();
    const costDetail: AgentDetailResponse = {
      record: {
        ...sampleDetail.record,
        spec: { model: { provider: "anthropic", name: "claude-x" } },
      },
    };
    createSessionMock.mockResolvedValue(sampleThread);
    listRateCardsMock.mockResolvedValue([
      {
        id: "rc",
        tenant_id: null,
        provider: "anthropic",
        model: "claude-x",
        input_per_mtok_micros: 3_000_000,
        output_per_mtok_micros: 15_000_000,
        cache_creation_per_mtok_micros: 0,
        cache_read_per_mtok_micros: 0,
      },
    ]);
    streamRunMock.mockReturnValue(
      makeStream([
        {
          id: "m",
          event: "metadata",
          data: { run_id: "run-77" },
          rawData: "",
          receivedAt: "2026-05-25T00:00:01Z",
        },
        {
          id: "u",
          event: "updates",
          data: {
            agent: {
              messages: [
                {
                  type: "ai",
                  content: "hi",
                  usage_metadata: {
                    input_tokens: 1000,
                    output_tokens: 100,
                    total_tokens: 1100,
                  },
                },
              ],
              step_count: 2,
            },
          },
          rawData: "",
          receivedAt: "2026-05-25T00:00:02Z",
        },
        {
          id: "e",
          event: "end",
          data: "ok",
          rawData: "ok",
          receivedAt: "2026-05-25T00:00:03Z",
        },
      ]),
    );
    renderPg(costDetail);
    await screen.findByText(/33333333-3333-3333/);
    await user.type(screen.getByTestId("playground-input"), "q");
    await user.click(screen.getByTestId("playground-run"));
    await screen.findByText("hi");

    expect(screen.getByTestId("playground-turn-cost")).toBeInTheDocument();
    expect(screen.getByTestId("playground-turn-meta")).toHaveTextContent("2");
    expect(screen.getByTestId("playground-turn-run-link")).toHaveAttribute(
      "href",
      `/runs/${sampleThread.thread_id}/run-77`,
    );
  });

  it("lists past sessions for resume and shows a resumed banner", async () => {
    const user = userEvent.setup();
    createSessionMock.mockResolvedValue(sampleThread);
    const past: ThreadMeta = {
      ...sampleThread,
      thread_id: "99999999-9999-9999-9999-999999999999",
      created_at: "2026-05-20T00:00:00Z",
    };
    listSessionsMock.mockResolvedValue([past]);
    renderPg();
    await screen.findByText(/33333333-3333-3333/);

    const select = within(
      await screen.findByTestId("playground-resume-select"),
    ).getByRole("combobox");
    await user.click(select);
    await user.click(await screen.findByText(/99999999/));
    expect(
      await screen.findByTestId("playground-resumed-notice"),
    ).toBeInTheDocument();
  });

  it("shows the workspace inspector with the volume + artifacts", async () => {
    createSessionMock.mockResolvedValue(sampleThread);
    getWorkspaceMock.mockResolvedValue({
      workspace: {
        id: "w1",
        tenant_id: sampleThread.tenant_id,
        user_id: "u-1",
        volume_name: "helix-ws-t-u",
        size_bytes: 2048,
        size_limit_bytes: 1000000,
        created_at: null,
        last_accessed_at: null,
        deleted_at: null,
        archived_object_key: null,
      },
      artifacts: [
        {
          name: "report.md",
          kind: "document",
          latest_version: 2,
          created_at: null,
          updated_at: null,
        },
      ],
    });
    renderPg();
    await screen.findByText(/33333333-3333-3333/);
    const panel = await screen.findByTestId("playground-workspace");
    expect(panel).toHaveTextContent("helix-ws-t-u");
    expect(panel).toHaveTextContent("2.0 KB");
    expect(panel).toHaveTextContent("report.md");
  });

  it("shows 'no workspace' when the user has none (read-only null)", async () => {
    createSessionMock.mockResolvedValue(sampleThread);
    renderPg();
    await screen.findByText(/33333333-3333-3333/);
    expect(
      await screen.findByTestId("playground-workspace-none"),
    ).toBeInTheDocument();
  });

  it("surfaces an approval gate, approves, and streams the continuation", async () => {
    const user = userEvent.setup();
    createSessionMock.mockResolvedValue(sampleThread);
    // Paused run: an AI tool_call with no final text → detectApproval polls.
    streamRunMock.mockReturnValue(
      makeStream([
        {
          id: "m",
          event: "metadata",
          data: { run_id: "r-pause" },
          rawData: "",
          receivedAt: "2026-05-25T00:00:01Z",
        },
        {
          id: "u",
          event: "updates",
          data: {
            agent: {
              messages: [
                {
                  type: "ai",
                  content: "",
                  tool_calls: [
                    { id: "tc1", name: "bash", args: { cmd: "rm -rf /" }, type: "tool_call" },
                  ],
                },
              ],
              step_count: 1,
            },
          },
          rawData: "",
          receivedAt: "2026-05-25T00:00:02Z",
        },
        {
          id: "e",
          event: "end",
          data: "ok",
          rawData: "ok",
          receivedAt: "2026-05-25T00:00:03Z",
        },
      ]),
    );
    const approval: ApprovalItem = {
      id: "ap1",
      tenant_id: sampleThread.tenant_id,
      user_id: null,
      run_id: "r-pause",
      thread_id: sampleThread.thread_id,
      request_id: "req1",
      node: "tools",
      reason_kind: "policy_required",
      action_summary: "run bash: rm -rf /",
      proposed_args: { cmd: "rm -rf /" },
      requested_at: "2026-05-25T00:00:03Z",
      timeout_at: "2026-05-26T00:00:03Z",
      status: "pending",
      decided_by: null,
      decided_at: null,
    };
    listApprovalsMock.mockResolvedValue({ items: [approval], total: 1, limit: 50, offset: 0 });
    decideApprovalsMock.mockResolvedValue({
      results: [{ run_id: "r-pause", ok: true, continuation_run_id: "r-cont" }],
      succeeded: 1,
    });
    streamRunEventsMock.mockReturnValue(
      makeStream([
        {
          id: "u2",
          event: "updates",
          data: { agent: { messages: [{ type: "ai", content: "done after approval" }] } },
          rawData: "",
          receivedAt: "2026-05-25T00:00:05Z",
        },
        {
          id: "e2",
          event: "end",
          data: "ok",
          rawData: "ok",
          receivedAt: "2026-05-25T00:00:06Z",
        },
      ]),
    );

    renderPg();
    await screen.findByText(/33333333-3333-3333/);
    await user.type(screen.getByTestId("playground-input"), "delete everything");
    await user.click(screen.getByTestId("playground-run"));

    const card = await screen.findByTestId("playground-approval");
    expect(card).toHaveTextContent("rm -rf /");

    await user.click(screen.getByTestId("playground-approval-approve"));
    await screen.findByText("done after approval");
    expect(decideApprovalsMock).toHaveBeenCalledWith([
      { thread_id: sampleThread.thread_id, run_id: "r-pause", decision: "approve" },
    ]);
    expect(streamRunEventsMock).toHaveBeenCalledWith(
      sampleThread.thread_id,
      "r-cont",
      expect.objectContaining({ signal: expect.anything() }),
    );
    expect(screen.queryByTestId("playground-approval")).not.toBeInTheDocument();
  });

  it("removes an attachment when its tag is closed", async () => {
    const user = userEvent.setup();
    createSessionMock.mockResolvedValue(sampleThread);
    uploadImageMock.mockResolvedValue("helix://image/img-2.png");
    renderPg();
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
