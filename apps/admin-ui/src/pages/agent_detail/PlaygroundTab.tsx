/**
 * Playground tab — per-agent debug surface backed by real ``/v1/sessions`` +
 * ``/v1/sessions/{thread_id}/runs`` SSE.
 *
 * Playground-Uplift:
 *   - **user_id (D1)**: an admin may run the session as another user_id (real
 *     tenant user picker OR free-form sandbox UUID) → verify that user's
 *     per-user workspace / memory / episodic. Sent as ``run_as_user_id`` to
 *     createSession; the active identity shows in the header.
 *   - **multi-turn (D2)**: the conversation accumulates as a transcript of
 *     turns (the thread is reused, so the backend already continues context);
 *     each turn keeps its own event stream.
 *   - **per-turn observability (D3)**: each turn distills token usage +
 *     reasoning trace from its frames (the fields surfaced in #847).
 *
 * The "edit manifest snippet + re-run" affordance is a follow-up (needs a
 * backend ad-hoc manifest override that doesn't exist yet).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  AutoComplete,
  Button,
  Collapse,
  Empty,
  Input,
  Popconfirm,
  Popover,
  Segmented,
  Space,
  Tag,
  Typography,
} from "antd";
import {
  AlertTriangle,
  Check,
  Download,
  FileText,
  HardDrive,
  History,
  ImagePlus,
  Play,
  RefreshCw,
  RotateCcw,
  Send,
  Square,
  ThumbsDown,
  ThumbsUp,
  Trash2,
  User,
  X,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  decideApprovals,
  listApprovals,
  type ApprovalItem,
} from "../../api/approvals";
import { ApiError } from "../../api/client";
import { listMembers } from "../../api/members";
import { listRateCards, type RateCardRecord } from "../../api/rate_card";
import { streamRunEvents } from "../../api/runs";
import {
  createSession,
  deleteSessionArtifact,
  deleteSessionWorkspaceFile,
  downloadSessionArtifact,
  downloadSessionWorkspaceFile,
  getSessionMessages,
  getSessionWorkspace,
  getSessionWorkspaceFiles,
  streamRun,
  submitSessionFeedback,
  type HistoryMessage,
  type RunRequest,
  type SessionWorkspace,
  type SseEvent,
  type ThreadMeta,
  type WorkspaceFile,
} from "../../api/sessions";
import { artifactsFromTools, toolStatusSummary } from "../../api/tool_timeline";
import { summarizeTurn } from "../../api/turn_summary";
import { uploadDocument, uploadImage } from "../../api/uploads";
import { CopyButton } from "../../components/CopyButton";
import { MarkdownView } from "../../components/MarkdownView";
import { SessionHistoryDrawer } from "../../components/SessionHistoryDrawer";
import { ToolTimeline } from "../../components/ToolTimeline";
import type { AgentDetailResponse } from "../../api/agents";
import { TurnMeta } from "./playground/TurnMeta";
import {
  readModel,
  readPromptJinja,
  readPromptVariables,
} from "../../components/manifest-editor/form_model";

interface Attachment {
  id: string;
  name: string;
  kind: "image" | "document";
  value: string;
}

/** One round of the conversation — the user input plus the agent's streamed
 *  frames for that turn (the thread is reused, so the backend continues the
 *  context across turns). */
interface Turn {
  id: string;
  input: string;
  attachments: Attachment[];
  events: SseEvent[];
  status: "running" | "done" | "error";
  error: string | null;
  /** #5 — set when the run paused at an approval gate; cleared on decision. */
  approval: ApprovalItem | null;
}

const { Text } = Typography;
const { TextArea } = Input;

/** Char cap for the free-text message box — mirrors the server's
 *  ``RunRequest.input`` ``max_length`` (``MAX_RUN_INPUT_CHARS``). A long
 *  pasted email/spec fits; a whole book rides the document-upload path. */
const MAX_INPUT_CHARS = 65536;

const EVENT_VIEW_STORAGE_KEY = "expert_work.playground.eventView";

interface PlaygroundTabProps {
  detail: AgentDetailResponse;
}

const EVENT_COLOR: Record<string, string> = {
  metadata: "blue",
  updates: "geekblue",
  approval: "gold",
  error: "red",
  end: "green",
};

interface UserOption {
  value: string;
  label: string;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  return `${value.toFixed(1)} ${units[i]}`;
}

export function PlaygroundTab({ detail }: PlaygroundTabProps) {
  const { t } = useTranslation();
  const r = detail.record;

  // Dynamic-Prompt — the agent's declared run-time variables (jinja agents only).
  const manifestLike = { spec: r.spec };
  const promptJinja = readPromptJinja(manifestLike);
  const promptVariables = promptJinja
    ? readPromptVariables(manifestLike).filter(
        (v): v is { name: string } & typeof v => Boolean(v.name),
      )
    : [];

  const [thread, setThread] = useState<ThreadMeta | null>(null);
  const [threadError, setThreadError] = useState<string | null>(null);
  const [creatingThread, setCreatingThread] = useState(false);
  const [input, setInput] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [eventView, setEventViewState] = useState<"timeline" | "raw">(() => {
    if (typeof window === "undefined") return "timeline";
    return window.localStorage.getItem(EVENT_VIEW_STORAGE_KEY) === "raw"
      ? "raw"
      : "timeline";
  });
  const setEventView = useCallback((next: "timeline" | "raw") => {
    setEventViewState(next);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(EVENT_VIEW_STORAGE_KEY, next);
    }
  }, []);
  const [running, setRunning] = useState(false);
  const [exportingId, setExportingId] = useState<string | null>(null);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [varValues, setVarValues] = useState<Record<string, string>>({});
  // Playground-Uplift D1 — impersonation. Empty = run as self.
  const [runAsUser, setRunAsUser] = useState("");
  const [userOptions, setUserOptions] = useState<UserOption[]>([]);
  // Playground-Uplift D4 — workspace inspector (verify the VM started + persists).
  const [workspace, setWorkspace] = useState<SessionWorkspace | null>(null);
  const [workspaceFiles, setWorkspaceFiles] = useState<WorkspaceFile[]>([]);
  const [workspaceLoading, setWorkspaceLoading] = useState(false);
  const [downloadingPath, setDownloadingPath] = useState<string | null>(null);
  // Workspace mutation in-flight: the file path or `artifact:<name>` being
  // downloaded/deleted — disables the row's buttons + drives the spinner.
  const [busyWorkspaceKey, setBusyWorkspaceKey] = useState<string | null>(null);
  // Playground-Uplift iter2 — #4 cost (agent model's rate), #6 resume history.
  const [rate, setRate] = useState<RateCardRecord | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [resumed, setResumed] = useState(false);
  // #6 — prior conversation loaded when resuming an existing thread.
  const [history, setHistory] = useState<HistoryMessage[]>([]);

  const abortRef = useRef<AbortController | null>(null);
  // Resume sets ``runAsUser`` to the thread's owner; that change would otherwise
  // trip the "user changed → fresh thread" effect and clobber the resume. This
  // flag tells that effect to skip exactly the one rebind resume triggers.
  const skipRebindRef = useRef(false);
  const transcriptRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const docInputRef = useRef<HTMLInputElement>(null);

  // Load active tenant users for the impersonation picker (subject_id == the
  // tenant_user.id that keys the workspace/memory; only active members have one).
  useEffect(() => {
    let cancelled = false;
    void listMembers({ status: "active", limit: 200 })
      .then((page) => {
        if (cancelled) return;
        const opts = page.items
          .filter((m) => m.subject_id)
          .map((m) => ({ value: m.subject_id as string, label: m.email }));
        setUserOptions(opts);
      })
      .catch(() => {
        // Picker is a convenience — free-form entry still works on failure.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // #4 cost — fetch the agent model's rate once (per-(provider,model), no tier).
  useEffect(() => {
    const model = readModel({ spec: r.spec });
    if (!model.provider || !model.name) return;
    let cancelled = false;
    void listRateCards({ provider: model.provider, model: model.name })
      .then((rows) => {
        if (!cancelled) setRate(rows[0] ?? null);
      })
      .catch(() => {
        // No rate / not authorized → cost simply hidden.
      });
    return () => {
      cancelled = true;
    };
  }, [r.spec]);

  // Reset to a fresh draft — no backend session is created here. The thread is
  // created lazily on the first real action (see ``ensureThread``), so opening
  // the Playground / switching agent no longer POSTs an empty throwaway session.
  const resetDraft = useCallback(() => {
    setThreadError(null);
    setResumed(false);
    setHistory([]);
    setTurns([]);
    setAttachments([]);
    setUploadError(null);
    setThread(null);
  }, []);

  // Lazy session creation — avoids the empty-thread spam that eager creation on
  // mount produced (each mount/rebind POSTed a session before the user did
  // anything; StrictMode doubled it in dev). Returns the existing thread, the
  // freshly created one, or ``null`` on failure.
  const ensureThread = useCallback(async (): Promise<ThreadMeta | null> => {
    if (thread) return thread;
    setCreatingThread(true);
    setThreadError(null);
    try {
      const created = await createSession({
        agent_name: r.name,
        agent_version: r.version,
        ...(runAsUser.trim() ? { run_as_user_id: runAsUser.trim() } : {}),
      });
      setThread(created);
      return created;
    } catch (err) {
      const message =
        err instanceof ApiError
          ? `${err.code}: ${err.message}`
          : err instanceof Error
            ? err.message
            : "unknown error";
      setThreadError(message);
      setThread(null);
      return null;
    } finally {
      setCreatingThread(false);
    }
  }, [thread, r.name, r.version, runAsUser]);

  // #6 — resume an existing thread: switch to it + continue chatting (the
  // backend keeps the context). Past turns aren't replayed in the transcript;
  // a banner makes that explicit.
  const handleResume = useCallback(
    (picked: ThreadMeta) => {
      abortRef.current?.abort();
      setTurns([]);
      setAttachments([]);
      setThreadError(null);
      setResumed(true);
      setHistory([]);
      setThread(picked);
      // Continue as the thread's own user so per-user workspace / memory /
      // episodic stay consistent — auto-fill the run-as field. Guard the
      // user-change rebind effect so this doesn't spawn a fresh thread (only
      // when the value actually changes, else no rebind fires anyway).
      const nextRunAs = picked.user_id ?? "";
      if (nextRunAs !== runAsUser) {
        skipRebindRef.current = true;
        setRunAsUser(nextRunAs);
      }
      // Load the thread's prior conversation from the checkpoint.
      void getSessionMessages(picked.thread_id)
        .then(setHistory)
        .catch(() => setHistory([]));
    },
    [runAsUser],
  );

  // Re-bind a fresh thread when the agent or the impersonated user changes —
  // except the run-as change a resume makes (``skipRebindRef``), which must
  // keep the resumed thread.
  useEffect(() => {
    if (skipRebindRef.current) {
      skipRebindRef.current = false;
    } else {
      resetDraft();
    }
    return () => {
      abortRef.current?.abort();
    };
  }, [r.name, r.version, runAsUser, resetDraft]);

  // Auto-scroll the transcript as turns/frames arrive.
  useEffect(() => {
    const node = transcriptRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [turns]);

  const handleAttach = useCallback(
    (kind: "image" | "document") =>
      async (event: React.ChangeEvent<HTMLInputElement>) => {
        const file = event.target.files?.[0];
        event.target.value = "";
        if (!file) return;
        const active = thread ?? (await ensureThread());
        if (!active) return;
        setUploading(true);
        setUploadError(null);
        try {
          const value =
            kind === "image"
              ? await uploadImage(active.thread_id, file)
              : await uploadDocument(active.thread_id, file);
          setAttachments((prev) => [
            ...prev,
            { id: `${kind}:${value}`, name: file.name, kind, value },
          ]);
        } catch (err) {
          const message =
            err instanceof ApiError
              ? `${err.code}: ${err.message}`
              : err instanceof Error
                ? err.message
                : "upload failed";
          setUploadError(message);
        } finally {
          setUploading(false);
        }
      },
    [thread, ensureThread],
  );

  const handleRemoveAttachment = useCallback((id: string) => {
    setAttachments((prev) => prev.filter((a) => a.id !== id));
  }, []);

  const patchTurn = useCallback((id: string, patch: Partial<Turn>) => {
    setTurns((prev) =>
      prev.map((tn) => (tn.id === id ? { ...tn, ...patch } : tn)),
    );
  }, []);

  // #5 — a paused run registers its agent_approval row just after the stream's
  // end frame, so poll briefly (race) for a pending approval on this thread.
  const detectApproval = useCallback(
    async (turnId: string, threadId: string, runId: string | null) => {
      for (let attempt = 0; attempt < 4; attempt++) {
        try {
          const list = await listApprovals({ status: "pending" });
          const match = list.items.find(
            (a) =>
              a.thread_id === threadId &&
              (runId === null || a.run_id === runId),
          );
          if (match) {
            patchTurn(turnId, { approval: match });
            return;
          }
        } catch {
          // best-effort — approval surfacing never fails the turn.
        }
        await new Promise((resolve) => setTimeout(resolve, 500));
      }
    },
    [patchTurn],
  );

  const handleRun = useCallback(async () => {
    if (running) return;
    // Lazy — create the backend thread on this first send if it doesn't exist.
    const active = thread ?? (await ensureThread());
    if (!active) return;
    setRunning(true);
    const turnAttachments = attachments;
    const turnInput = input;
    const imageRefs = turnAttachments
      .filter((a) => a.kind === "image")
      .map((a) => a.value);
    const docPaths = turnAttachments
      .filter((a) => a.kind === "document")
      .map((a) => a.value);
    const docNote =
      docPaths.length > 0
        ? `${t("playground.uploaded_docs_note")}: ${docPaths.join(", ")}\n\n`
        : "";
    const effectiveInput = docNote + turnInput;
    const inputs: Record<string, string> = {};
    for (const v of promptVariables) {
      const val = varValues[v.name];
      if (val !== undefined && val !== "") inputs[v.name] = val;
    }
    const body: RunRequest = { input: effectiveInput };
    if (imageRefs.length > 0) body.image_refs = imageRefs;
    if (Object.keys(inputs).length > 0) body.inputs = inputs;

    const turnId = `${Date.now()}-${turns.length}`;
    const updateTurn = (patch: Partial<Turn>) =>
      setTurns((prev) =>
        prev.map((tn) => (tn.id === turnId ? { ...tn, ...patch } : tn)),
      );
    setTurns((prev) => [
      ...prev,
      {
        id: turnId,
        input: turnInput,
        attachments: turnAttachments,
        events: [],
        status: "running",
        error: null,
        approval: null,
      },
    ]);
    // Consume the input + attachments — the next turn starts fresh.
    setInput("");
    setAttachments([]);

    const ac = new AbortController();
    abortRef.current = ac;
    const frames: SseEvent[] = [];
    const threadId = active.thread_id;
    try {
      for await (const frame of streamRun(threadId, body, {
        signal: ac.signal,
      })) {
        frames.push(frame);
        // #5 — a dedicated ``approval`` event surfaces the gate deterministically
        // (no dependence on the terminal ``end`` frame or a post-stream poll).
        const approvalFromFrame =
          frame.event === "approval" ? approvalItemFromEvent(frame.data) : null;
        setTurns((prev) =>
          prev.map((tn) =>
            tn.id === turnId
              ? {
                  ...tn,
                  events: [...tn.events, frame],
                  approval: approvalFromFrame ?? tn.approval,
                }
              : tn,
          ),
        );
        if (frame.event === "end") break;
      }
      updateTurn({ status: "done" });
    } catch (err) {
      if (err instanceof Error && err.name === "AbortError") {
        updateTurn({ status: "done" });
      } else {
        const message = err instanceof Error ? err.message : "stream failed";
        updateTurn({ status: "error", error: message });
      }
    } finally {
      setRunning(false);
      abortRef.current = null;
    }
    // #5 — a paused run yields no final answer; look for its approval gate.
    // Fire-and-forget so the run UI (Stop button) frees immediately; a found
    // gate patches the turn asynchronously.
    if (
      frames.at(-1)?.event === "end" &&
      summarizeTurn(frames).finalText === null
    ) {
      void detectApproval(turnId, threadId, runIdOf(frames));
    }
  }, [
    thread,
    ensureThread,
    input,
    running,
    attachments,
    promptVariables,
    varValues,
    turns.length,
    t,
    detectApproval,
  ]);

  // #5 — decide a turn's pending approval, then stream the continuation run
  // (the decision spawns it) into the SAME turn, then re-check for a next gate.
  const handleDecide = useCallback(
    async (
      turnId: string,
      approval: ApprovalItem,
      decision: "approve" | "reject",
    ) => {
      if (!thread) return;
      const threadId = thread.thread_id;
      setRunning(true);
      patchTurn(turnId, { approval: null, status: "running" });
      let continuationRunId: string | null = null;
      try {
        const result = await decideApprovals([
          { thread_id: threadId, run_id: approval.run_id, decision },
        ]);
        continuationRunId = result.results[0]?.continuation_run_id ?? null;
      } catch (err) {
        const message = err instanceof Error ? err.message : "decision failed";
        patchTurn(turnId, { status: "error", error: message });
        setRunning(false);
        return;
      }
      if (continuationRunId === null) {
        patchTurn(turnId, { status: "done" });
        setRunning(false);
        return;
      }
      const ac = new AbortController();
      abortRef.current = ac;
      const frames: SseEvent[] = [];
      try {
        for await (const frame of streamRunEvents(threadId, continuationRunId, {
          signal: ac.signal,
        })) {
          frames.push(frame);
          setTurns((prev) =>
            prev.map((tn) =>
              tn.id === turnId ? { ...tn, events: [...tn.events, frame] } : tn,
            ),
          );
          if (frame.event === "end") break;
        }
        patchTurn(turnId, { status: "done" });
      } catch (err) {
        if (!(err instanceof Error && err.name === "AbortError")) {
          const message = err instanceof Error ? err.message : "stream failed";
          patchTurn(turnId, { status: "error", error: message });
        } else {
          patchTurn(turnId, { status: "done" });
        }
      } finally {
        setRunning(false);
        abortRef.current = null;
      }
      // Chained gate — re-check after the continuation, fire-and-forget.
      if (
        frames.at(-1)?.event === "end" &&
        summarizeTurn(frames).finalText === null
      ) {
        void detectApproval(turnId, threadId, continuationRunId);
      }
    },
    [thread, patchTurn, detectApproval],
  );

  // Export a turn's full event stream as JSON for offline analysis. Prefer the
  // authoritative persisted stream (the ``/events`` replay) — the live client
  // may have missed frames (e.g. a paused run that never delivered ``end``);
  // fall back to the frames this client received when there is no run_id or the
  // fetch fails. Either way a file always downloads.
  const handleExport = useCallback(
    async (turn: Turn) => {
      const threadId = thread?.thread_id ?? null;
      const runId = runIdOf(turn.events);
      setExportingId(turn.id);
      let events: SseEvent[] = turn.events;
      let source: "backend" | "client" = "client";
      try {
        if (threadId && runId) {
          const collected: SseEvent[] = [];
          for await (const frame of streamRunEvents(threadId, runId)) {
            collected.push(frame);
            if (frame.event === "end") break;
          }
          if (collected.length > 0) {
            events = collected;
            source = "backend";
          }
        }
      } catch {
        // Best-effort — fall back to the client-side frames already assigned.
      } finally {
        setExportingId(null);
      }
      downloadJson(`expert-work-events-${runId ?? turn.id}.json`, {
        run_id: runId,
        thread_id: threadId,
        input: turn.input,
        source,
        exported_at: new Date().toISOString(),
        events,
      });
    },
    [thread],
  );

  const loadWorkspace = useCallback(async (threadId: string) => {
    setWorkspaceLoading(true);
    try {
      const [ws, fs] = await Promise.all([
        getSessionWorkspace(threadId),
        getSessionWorkspaceFiles(threadId).catch(() => [] as WorkspaceFile[]),
      ]);
      setWorkspace(ws);
      setWorkspaceFiles(fs);
    } catch {
      setWorkspace(null);
      setWorkspaceFiles([]);
    } finally {
      setWorkspaceLoading(false);
    }
  }, []);

  const handleDownloadFile = useCallback(
    async (threadId: string, path: string) => {
      setDownloadingPath(path);
      try {
        await downloadSessionWorkspaceFile(threadId, path);
      } catch {
        // Swallow — the file may have been removed between list + click; the
        // refresh button re-syncs. A toast here would need the App message API.
      } finally {
        setDownloadingPath(null);
      }
    },
    [],
  );

  const handleDownloadArtifact = useCallback(
    async (threadId: string, name: string) => {
      setBusyWorkspaceKey(`artifact:${name}`);
      try {
        await downloadSessionArtifact(threadId, name);
      } catch {
        // Swallow — same rationale as the file download.
      } finally {
        setBusyWorkspaceKey(null);
      }
    },
    [],
  );

  const handleDeleteFile = useCallback(
    async (threadId: string, path: string) => {
      setBusyWorkspaceKey(path);
      try {
        await deleteSessionWorkspaceFile(threadId, path);
        await loadWorkspace(threadId);
      } catch {
        // Swallow — refresh re-syncs the listing on the next manual refresh.
      } finally {
        setBusyWorkspaceKey(null);
      }
    },
    [loadWorkspace],
  );

  const handleDeleteArtifact = useCallback(
    async (threadId: string, name: string) => {
      setBusyWorkspaceKey(`artifact:${name}`);
      try {
        await deleteSessionArtifact(threadId, name);
        await loadWorkspace(threadId);
      } catch {
        // Swallow — refresh re-syncs.
      } finally {
        setBusyWorkspaceKey(null);
      }
    },
    [loadWorkspace],
  );

  // Refresh the workspace view when the thread (re)binds and after each run —
  // a run that wrote files makes the volume appear / its size grow.
  useEffect(() => {
    if (thread && !running) void loadWorkspace(thread.thread_id);
  }, [thread, running, loadWorkspace]);

  const handleStop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const activeUserLabel = runAsUser.trim()
    ? (userOptions.find((o) => o.value === runAsUser.trim())?.label ??
      runAsUser.trim())
    : t("playground.user_self");

  return (
    <div
      data-testid="playground-tab"
      style={{
        display: "grid",
        gridTemplateColumns: "minmax(360px, 2fr) minmax(420px, 3fr)",
        gap: 12,
        alignItems: "stretch",
      }}
    >
      <SessionHistoryDrawer
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
        agentName={r.name}
        currentThreadId={thread?.thread_id ?? null}
        onResume={handleResume}
      />
      {/* Left — session + input */}
      <div
        style={{
          border: "1px solid var(--ew-border-subtle)",
          borderRadius: 6,
          padding: 12,
          display: "flex",
          flexDirection: "column",
          gap: 12,
          minHeight: "calc(100vh - 360px)",
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <Text strong style={{ fontSize: 13 }}>
            {t("playground.session_label")}
          </Text>
          <Space size={6}>
            <Button
              size="small"
              icon={<History size={12} strokeWidth={1.75} />}
              onClick={() => setHistoryOpen(true)}
              disabled={running}
              data-testid="playground-history-open"
            >
              {t("playground.history_button")}
            </Button>
            <Button
              size="small"
              icon={<RotateCcw size={12} strokeWidth={1.75} />}
              onClick={resetDraft}
              loading={creatingThread}
              disabled={running}
              data-testid="playground-new-session"
            >
              {t("playground.new_session")}
            </Button>
          </Space>
        </div>
        {resumed && (
          <Alert
            type="info"
            showIcon
            message={t("playground.resumed_notice")}
            data-testid="playground-resumed-notice"
            style={{ padding: "4px 8px" }}
          />
        )}

        {/* Playground-Uplift D1 — run-as user (real user picker + free-form id). */}
        <div data-testid="playground-user">
          <Text
            type="secondary"
            style={{ fontSize: 12, display: "block", marginBottom: 4 }}
          >
            {t("playground.run_as_label")}
          </Text>
          <AutoComplete
            options={userOptions}
            value={runAsUser}
            onChange={setRunAsUser}
            allowClear
            disabled={running || creatingThread}
            filterOption={(inputValue, option) =>
              (option?.label ?? "")
                .toString()
                .toLowerCase()
                .includes(inputValue.toLowerCase())
            }
            style={{ width: "100%" }}
            placeholder={t("playground.run_as_placeholder")}
            data-testid="playground-user-select"
          >
            <Input
              aria-label={t("playground.run_as_label")}
              prefix={<User size={12} strokeWidth={1.75} />}
            />
          </AutoComplete>
          <Text
            type="secondary"
            style={{ fontSize: 11, display: "block", marginTop: 2 }}
            data-testid="playground-active-user"
          >
            {t("playground.running_as", { user: activeUserLabel })}
          </Text>
        </div>

        {threadError !== null ? (
          <Alert
            type="error"
            showIcon
            message={t("playground.session_failed")}
            description={threadError}
            data-testid="playground-session-error"
          />
        ) : thread ? (
          <Text type="secondary" style={{ fontSize: 12 }} className="mono">
            {`${t("playground.thread_id")}: ${thread.thread_id}`}
          </Text>
        ) : null}

        {promptVariables.length > 0 && (
          <div data-testid="playground-vars" style={{ marginBottom: 8 }}>
            <Text
              type="secondary"
              style={{ fontSize: 12, display: "block", marginBottom: 4 }}
            >
              {t("playground.prompt_vars_label")}
            </Text>
            {promptVariables.map((v) => (
              <div
                key={v.name}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  marginBottom: 4,
                }}
              >
                <Text style={{ width: 140, fontSize: 12 }} className="mono">
                  {v.name}
                  {v.required !== false ? " *" : ""}
                </Text>
                <Input
                  size="small"
                  value={varValues[v.name] ?? ""}
                  placeholder={v.description ?? v.name}
                  aria-label={`${t("playground.prompt_vars_label")}: ${v.name}`}
                  data-testid={`playground-var-${v.name}`}
                  disabled={running}
                  onChange={(e) =>
                    setVarValues((prev) => ({
                      ...prev,
                      [v.name]: e.target.value,
                    }))
                  }
                />
              </div>
            ))}
          </div>
        )}

        <TextArea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={t("playground.input_placeholder")}
          autoSize={{ minRows: 6, maxRows: 14 }}
          disabled={running}
          maxLength={MAX_INPUT_CHARS}
          showCount
          data-testid="playground-input"
        />

        <input
          ref={fileInputRef}
          type="file"
          accept="image/png,image/jpeg,image/webp,image/gif"
          style={{ display: "none" }}
          onChange={handleAttach("image")}
          data-testid="playground-file-input"
        />

        <input
          ref={docInputRef}
          type="file"
          accept=".pdf,.docx,.xlsx,.pptx,.txt,.md,.csv"
          style={{ display: "none" }}
          onChange={handleAttach("document")}
          data-testid="playground-doc-input"
        />

        {uploadError !== null && (
          <Alert
            type="error"
            showIcon
            message={t("playground.upload_failed")}
            description={uploadError}
            data-testid="playground-upload-error"
          />
        )}

        {attachments.length > 0 && (
          <div data-testid="playground-attachments">
            <Text type="secondary" style={{ fontSize: 12 }}>
              {t("playground.attachments_label")}
            </Text>
            <div
              style={{
                display: "flex",
                flexWrap: "wrap",
                gap: 6,
                marginTop: 6,
              }}
            >
              {attachments.map((a) => (
                <Tag
                  key={a.id}
                  closable
                  onClose={(e) => {
                    e.preventDefault();
                    handleRemoveAttachment(a.id);
                  }}
                  closeIcon={
                    <X
                      size={11}
                      strokeWidth={1.75}
                      aria-label={t("playground.remove_attachment")}
                    />
                  }
                  bordered={false}
                  data-testid="playground-attachment"
                >
                  {a.name}
                </Tag>
              ))}
            </div>
          </div>
        )}

        <Space size={8}>
          <Button
            type="primary"
            icon={
              running ? (
                <Play size={14} strokeWidth={1.75} />
              ) : (
                <Send size={14} strokeWidth={1.75} />
              )
            }
            onClick={handleRun}
            loading={running}
            disabled={!running && input.trim().length === 0}
            data-testid="playground-run"
          >
            {running ? t("playground.running") : t("playground.run")}
          </Button>
          <Button
            icon={<ImagePlus size={14} strokeWidth={1.75} />}
            onClick={() => fileInputRef.current?.click()}
            loading={uploading}
            disabled={running}
            data-testid="playground-attach"
          >
            {uploading
              ? t("playground.uploading")
              : t("playground.attach_image")}
          </Button>
          <Button
            icon={<FileText size={14} strokeWidth={1.75} />}
            onClick={() => docInputRef.current?.click()}
            loading={uploading}
            disabled={running}
            data-testid="playground-attach-doc"
          >
            {uploading
              ? t("playground.uploading")
              : t("playground.attach_document")}
          </Button>
          {running && (
            <Button
              danger
              icon={<Square size={14} strokeWidth={1.75} />}
              onClick={handleStop}
              data-testid="playground-stop"
            >
              {t("playground.stop")}
            </Button>
          )}
        </Space>

        {/* Playground-Uplift D4 — workspace inspector. */}
        {thread && (
          <div
            data-testid="playground-workspace"
            style={{
              marginTop: "auto",
              borderTop: "1px solid var(--ew-border-subtle)",
              paddingTop: 8,
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                marginBottom: 6,
              }}
            >
              <HardDrive size={13} strokeWidth={1.75} />
              <Text strong style={{ fontSize: 12 }}>
                {t("playground.workspace_label")}
              </Text>
              <Button
                size="small"
                type="text"
                icon={<RefreshCw size={11} strokeWidth={1.75} />}
                loading={workspaceLoading}
                onClick={() => void loadWorkspace(thread.thread_id)}
                aria-label={t("playground.workspace_refresh")}
                data-testid="playground-workspace-refresh"
                style={{ marginLeft: "auto" }}
              />
            </div>
            {workspace?.workspace ? (
              <div style={{ fontSize: 11 }} className="mono">
                <div data-testid="playground-workspace-volume">
                  {t("playground.workspace_volume")}:{" "}
                  {workspace.workspace.volume_name}
                </div>
                <div>
                  {t("playground.workspace_size")}:{" "}
                  {formatBytes(workspace.workspace.size_bytes)}
                  {workspace.workspace.deleted_at
                    ? ` · ${t("playground.workspace_deleted")}`
                    : ""}
                </div>
              </div>
            ) : (
              <Text
                type="secondary"
                style={{ fontSize: 11 }}
                data-testid="playground-workspace-none"
              >
                {t("playground.workspace_none")}
              </Text>
            )}
            {/* Artifacts — the agent's registered deliverables: download +
                (soft-)delete each. A list, not chips, since they're the things
                you actually take away. */}
            {workspace && workspace.artifacts.length > 0 && (
              <div
                style={{ marginTop: 6 }}
                data-testid="playground-workspace-artifacts"
              >
                <Text type="secondary" style={{ fontSize: 11 }}>
                  {t("playground.workspace_artifacts")}:
                </Text>
                <div
                  style={{
                    marginTop: 4,
                    display: "flex",
                    flexDirection: "column",
                    gap: 2,
                  }}
                >
                  {workspace.artifacts.map((a) => (
                    <div
                      key={a.name}
                      data-testid="playground-workspace-artifact"
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 6,
                        fontSize: 11,
                      }}
                    >
                      <span
                        className="mono"
                        style={{
                          flex: 1,
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                        title={`${a.name} · ${a.kind} v${a.latest_version}`}
                      >
                        {a.name}
                      </span>
                      <Text type="secondary" style={{ fontSize: 10 }}>
                        {a.kind} v{a.latest_version}
                      </Text>
                      <Button
                        size="small"
                        type="text"
                        icon={<Download size={11} strokeWidth={1.75} />}
                        loading={busyWorkspaceKey === `artifact:${a.name}`}
                        disabled={!thread || busyWorkspaceKey !== null}
                        onClick={() =>
                          thread &&
                          void handleDownloadArtifact(thread.thread_id, a.name)
                        }
                        aria-label={t("playground.artifact_download", {
                          name: a.name,
                        })}
                        data-testid="playground-workspace-artifact-download"
                      />
                      <Popconfirm
                        title={t("playground.artifact_delete_confirm")}
                        okText={t("playground.delete_ok")}
                        cancelText={t("playground.delete_cancel")}
                        okButtonProps={{ danger: true }}
                        onConfirm={() =>
                          thread &&
                          void handleDeleteArtifact(thread.thread_id, a.name)
                        }
                      >
                        <Button
                          size="small"
                          type="text"
                          danger
                          icon={<Trash2 size={11} strokeWidth={1.75} />}
                          disabled={!thread || busyWorkspaceKey !== null}
                          aria-label={t("playground.artifact_delete", {
                            name: a.name,
                          })}
                          data-testid="playground-workspace-artifact-delete"
                        />
                      </Popconfirm>
                    </div>
                  ))}
                </div>
              </div>
            )}
            {/* Browse + download + delete the raw files the agent wrote. Hidden
                files (.npm/.cache/.mplconfig …) are filtered — runtime noise. */}
            {workspaceFiles.some((f) => !isHiddenWorkspacePath(f.path)) && (
              <div
                style={{ marginTop: 8 }}
                data-testid="playground-workspace-files"
              >
                <Text type="secondary" style={{ fontSize: 11 }}>
                  {t("playground.workspace_files")}:
                </Text>
                <div
                  style={{
                    marginTop: 4,
                    display: "flex",
                    flexDirection: "column",
                    gap: 2,
                  }}
                >
                  {workspaceFiles
                    .filter((f) => !isHiddenWorkspacePath(f.path))
                    .map((f) => (
                      <div
                        key={f.path}
                        data-testid="playground-workspace-file"
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 6,
                          fontSize: 11,
                        }}
                      >
                        <span
                          className="mono"
                          style={{
                            flex: 1,
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                          }}
                          title={f.path}
                        >
                          {f.path}
                        </span>
                        <Text type="secondary" style={{ fontSize: 10 }}>
                          {formatBytes(f.size)}
                        </Text>
                        <Button
                          size="small"
                          type="text"
                          icon={<Download size={11} strokeWidth={1.75} />}
                          loading={downloadingPath === f.path}
                          disabled={
                            !thread ||
                            downloadingPath !== null ||
                            busyWorkspaceKey !== null
                          }
                          onClick={() =>
                            thread &&
                            void handleDownloadFile(thread.thread_id, f.path)
                          }
                          aria-label={t("playground.workspace_file_download", {
                            name: f.path,
                          })}
                          data-testid="playground-workspace-file-download"
                        />
                        <Popconfirm
                          title={t("playground.file_delete_confirm")}
                          okText={t("playground.delete_ok")}
                          cancelText={t("playground.delete_cancel")}
                          okButtonProps={{ danger: true }}
                          onConfirm={() =>
                            thread &&
                            void handleDeleteFile(thread.thread_id, f.path)
                          }
                        >
                          <Button
                            size="small"
                            type="text"
                            danger
                            icon={<Trash2 size={11} strokeWidth={1.75} />}
                            loading={busyWorkspaceKey === f.path}
                            disabled={!thread || busyWorkspaceKey !== null}
                            aria-label={t("playground.file_delete", {
                              name: f.path,
                            })}
                            data-testid="playground-workspace-file-delete"
                          />
                        </Popconfirm>
                      </div>
                    ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Right — conversation transcript */}
      <div
        style={{
          border: "1px solid var(--ew-border-subtle)",
          borderRadius: 6,
          padding: 0,
          display: "flex",
          flexDirection: "column",
          // Definite height (not just a floor) so the transcript body below can
          // be a bounded flex child that scrolls internally. A minHeight-only
          // box grows with content → the cap below is ignored → no scroll.
          height: "calc(100vh - 360px)",
          overflow: "hidden",
        }}
      >
        <div
          style={{
            padding: "8px 12px",
            borderBottom: "1px solid var(--ew-border-subtle)",
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}
        >
          <Text strong style={{ fontSize: 13 }}>
            {t("playground.transcript_label")}
          </Text>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {turns.length === 0
              ? ""
              : t("playground.turn_count", { n: turns.length })}
          </Text>
        </div>

        <div
          ref={transcriptRef}
          style={{
            flex: 1,
            // ``minHeight: 0`` is the critical bit — a flex child defaults to
            // ``min-height: auto`` (≥ its content), which beats the parent's cap
            // when events pile up, so the list grows past the viewport and never
            // scrolls. Zeroing it lets the bounded parent clip → overflow scrolls.
            minHeight: 0,
            padding: 12,
            overflow: "auto",
            display: "flex",
            flexDirection: "column",
            gap: 12,
          }}
          data-testid="playground-transcript"
        >
          {turns.length === 0 && history.length === 0 && (
            <Empty
              description={t("playground.empty_log")}
              style={{ marginTop: 64 }}
              data-testid="playground-empty-log"
            />
          )}
          {/* #6 — prior conversation (read-only) when resuming a thread. */}
          {history.length > 0 && (
            <div
              data-testid="playground-history"
              style={{
                display: "flex",
                flexDirection: "column",
                gap: 8,
                flexShrink: 0,
              }}
            >
              {history.map((m, idx) => (
                <div
                  key={idx}
                  style={{
                    alignSelf: m.role === "user" ? "flex-end" : "flex-start",
                    maxWidth: "85%",
                    padding: "6px 10px",
                    borderRadius: 8,
                    fontSize: 13,
                    whiteSpace: "pre-wrap",
                    background:
                      m.role === "user"
                        ? "var(--ew-surface-raised)"
                        : "transparent",
                    border:
                      m.role === "user"
                        ? "1px solid var(--ew-border-subtle)"
                        : "none",
                    opacity: 0.75,
                  }}
                >
                  {m.role === "user" ? (
                    m.content
                  ) : (
                    <MarkdownView>{m.content}</MarkdownView>
                  )}
                </div>
              ))}
              <div
                style={{
                  textAlign: "center",
                  fontSize: 11,
                  color: "var(--ew-text-tertiary)",
                  borderTop: "1px dashed var(--ew-border-subtle)",
                  paddingTop: 6,
                  marginTop: 2,
                }}
              >
                {t("playground.history_divider")}
              </div>
            </div>
          )}
          {turns.map((turn, turnIndex) => (
            <TurnCard
              key={turn.id}
              turn={turn}
              turnSeq={turnIndex}
              eventView={eventView}
              onViewChange={setEventView}
              threadId={thread?.thread_id ?? null}
              onDownloadArtifact={handleDownloadArtifact}
              rate={rate}
              onDecide={handleDecide}
              deciding={running}
              onExport={handleExport}
              exporting={exportingId === turn.id}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

/** SE-16 (SE-A46) — the per-turn 👍/👎 bar. 👍 submits immediately; 👎 opens
 *  a comment popover (the user's own words are the highest-value failure
 *  label for the distiller). One submission per turn; errors surface inline
 *  (fire-and-forget signal — never blocks the conversation). */
function FeedbackBar({ threadId, turnSeq }: { threadId: string; turnSeq: number }) {
  const { t } = useTranslation();
  const [submitted, setSubmitted] = useState<"up" | "down" | null>(null);
  const [busy, setBusy] = useState(false);
  const [commentOpen, setCommentOpen] = useState(false);
  const [comment, setComment] = useState("");
  const [error, setError] = useState<string | null>(null);

  const submit = useCallback(
    async (rating: "up" | "down", text?: string) => {
      setBusy(true);
      setError(null);
      try {
        await submitSessionFeedback(threadId, {
          rating,
          comment: text?.trim() || undefined,
          turn_seq: turnSeq,
        });
        setSubmitted(rating);
        setCommentOpen(false);
      } catch (err) {
        setError(
          err instanceof ApiError ? `${err.code}: ${err.message}` : String(err),
        );
      } finally {
        setBusy(false);
      }
    },
    [threadId, turnSeq],
  );

  return (
    <div
      style={{ marginTop: 6, display: "flex", gap: 4, alignItems: "center" }}
      data-testid="playground-turn-feedback"
    >
      <Button
        type="text"
        size="small"
        disabled={busy || submitted !== null}
        onClick={() => void submit("up")}
        aria-label={t("playground.feedback_up")}
        data-testid="playground-feedback-up"
        icon={
          <ThumbsUp
            size={13}
            strokeWidth={1.75}
            color={submitted === "up" ? "var(--ew-status-success, #52c41a)" : undefined}
          />
        }
      />
      <Popover
        open={commentOpen}
        onOpenChange={(open) => {
          if (submitted === null && !busy) setCommentOpen(open);
        }}
        trigger="click"
        content={
          <div style={{ width: 260 }}>
            <Input.TextArea
              rows={3}
              maxLength={4000}
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              placeholder={t("playground.feedback_comment_placeholder")}
              aria-label={t("playground.feedback_comment_placeholder")}
              data-testid="playground-feedback-comment"
            />
            <div style={{ marginTop: 8, textAlign: "right" }}>
              <Button
                type="primary"
                size="small"
                loading={busy}
                onClick={() => void submit("down", comment)}
                data-testid="playground-feedback-down-submit"
              >
                {t("playground.feedback_comment_submit")}
              </Button>
            </div>
          </div>
        }
      >
        <Button
          type="text"
          size="small"
          disabled={busy || submitted !== null}
          aria-label={t("playground.feedback_down")}
          data-testid="playground-feedback-down"
          icon={
            <ThumbsDown
              size={13}
              strokeWidth={1.75}
              color={
                submitted === "down" ? "var(--ew-status-error, #f5222d)" : undefined
              }
            />
          }
        />
      </Popover>
      {submitted !== null && (
        <Text type="secondary" style={{ fontSize: 11 }}>
          {t("playground.feedback_thanks")}
        </Text>
      )}
      {error !== null && (
        <Text type="danger" style={{ fontSize: 11 }} data-testid="playground-feedback-error">
          {t("playground.feedback_failed")}: {error}
        </Text>
      )}
    </div>
  );
}

/** Trigger a client-side download of ``data`` as a pretty-printed JSON file. */
function downloadJson(filename: string, data: unknown): void {
  const blob = new Blob([JSON.stringify(data, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

/** Hide dotfiles/dotdirs (``.npm``, ``.cache``, ``.mplconfig``, …) — runtime
 *  scaffolding the agent didn't author, just noise in the workspace browser. */
function isHiddenWorkspacePath(path: string): boolean {
  return path.split("/").some((seg) => seg.startsWith("."));
}

function runIdOf(events: readonly SseEvent[]): string | null {
  for (const e of events) {
    if (
      e.event === "metadata" &&
      e.data !== null &&
      typeof e.data === "object"
    ) {
      const rid = (e.data as Record<string, unknown>).run_id;
      if (typeof rid === "string" && rid) return rid;
    }
  }
  return null;
}

/** #5 — build an ``ApprovalItem`` from a backend ``approval`` SSE frame so the
 *  gate renders the instant the run pauses, without waiting for the terminal
 *  ``end`` frame + a ``/v1/approvals`` poll (which never fires when the client
 *  misses ``end``). The decide call only needs ``thread_id`` + ``run_id``; the
 *  rest feeds the gate card. Fields absent from the stream default safely. */
function approvalItemFromEvent(data: unknown): ApprovalItem | null {
  if (data === null || typeof data !== "object") return null;
  const d = data as Record<string, unknown>;
  if (typeof d.run_id !== "string" || typeof d.thread_id !== "string")
    return null;
  const str = (v: unknown): string => (typeof v === "string" ? v : "");
  return {
    id: str(d.request_id) || d.run_id,
    tenant_id: str(d.tenant_id),
    user_id: null,
    run_id: d.run_id,
    thread_id: d.thread_id,
    request_id: str(d.request_id),
    node: str(d.node),
    reason_kind: str(d.reason_kind),
    action_summary: str(d.action_summary),
    proposed_args:
      d.proposed_args !== null && typeof d.proposed_args === "object"
        ? (d.proposed_args as Record<string, unknown>)
        : {},
    requested_at: str(d.requested_at),
    timeout_at: str(d.timeout_at),
    status: "pending",
    decided_by: null,
    decided_at: null,
  };
}

function ApprovalGate({
  approval,
  busy,
  onDecide,
}: {
  approval: ApprovalItem;
  busy: boolean;
  onDecide: (decision: "approve" | "reject") => void;
}) {
  const { t } = useTranslation();
  return (
    <div
      data-testid="playground-approval"
      style={{
        border: "1px solid var(--ew-color-warning, #d4a017)",
        borderRadius: 6,
        padding: 10,
        marginTop: 8,
        background: "var(--ew-surface-raised)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          marginBottom: 4,
        }}
      >
        <AlertTriangle size={14} strokeWidth={1.75} />
        <Text strong style={{ fontSize: 12 }}>
          {approval.node} — {t("playground.approval_awaiting")}
        </Text>
      </div>
      <Text style={{ fontSize: 12, display: "block", marginBottom: 6 }}>
        {approval.action_summary}
      </Text>
      <pre
        style={{
          margin: 0,
          fontSize: 11,
          fontFamily: "var(--ew-font-mono)",
          color: "var(--ew-text-secondary)",
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
          maxHeight: 160,
          overflow: "auto",
          marginBottom: 8,
        }}
      >
        {JSON.stringify(approval.proposed_args, null, 2)}
      </pre>
      <Space size={8}>
        <Button
          type="primary"
          size="small"
          icon={<Check size={13} strokeWidth={1.75} />}
          loading={busy}
          onClick={() => onDecide("approve")}
          data-testid="playground-approval-approve"
        >
          {t("playground.approval_approve")}
        </Button>
        <Button
          danger
          size="small"
          icon={<X size={13} strokeWidth={1.75} />}
          loading={busy}
          onClick={() => onDecide("reject")}
          data-testid="playground-approval-reject"
        >
          {t("playground.approval_reject")}
        </Button>
        <Text type="secondary" style={{ fontSize: 11 }}>
          {t("playground.approval_modify_hint")}
        </Text>
      </Space>
    </div>
  );
}

function TurnCard({
  turn,
  turnSeq,
  eventView,
  onViewChange,
  threadId,
  onDownloadArtifact,
  rate,
  onDecide,
  deciding,
  onExport,
  exporting,
}: {
  turn: Turn;
  /** The turn's index in the transcript — sent as feedback ``turn_seq``. */
  turnSeq: number;
  eventView: "timeline" | "raw";
  onViewChange: (view: "timeline" | "raw") => void;
  threadId: string | null;
  onDownloadArtifact: (threadId: string, name: string) => Promise<void>;
  rate: RateCardRecord | null;
  onDecide: (
    turnId: string,
    approval: ApprovalItem,
    decision: "approve" | "reject",
  ) => void;
  deciding: boolean;
  onExport: (turn: Turn) => void;
  exporting: boolean;
}) {
  const { t } = useTranslation();
  const summary = summarizeTurn(turn.events);
  const toolStats = toolStatusSummary(turn.events);
  // A+B — artifacts the agent registered this turn (``save_artifact``). The
  // agent can't emit a download link itself (the endpoint is thread-scoped +
  // auth'd), so surface them as an inline download row — deer-flow's pattern.
  const turnArtifacts = useMemo(
    () => artifactsFromTools(turn.events),
    [turn.events],
  );
  const [downloadingArtifact, setDownloadingArtifact] = useState<string | null>(
    null,
  );
  const downloadArtifact = useCallback(
    async (name: string) => {
      if (threadId === null) return;
      setDownloadingArtifact(name);
      try {
        await onDownloadArtifact(threadId, name);
      } finally {
        setDownloadingArtifact(null);
      }
    },
    [threadId, onDownloadArtifact],
  );
  const answer =
    summary.finalText ??
    (turn.status === "running" ? t("playground.turn_running") : null);
  const runId = runIdOf(turn.events);
  // #4 cost — non-cached input + cache_read + output, each at its per-mtok rate
  // (micro-元 per 1M tokens). null when no usage or no rate for the model.
  const costCny =
    summary.usage && rate
      ? (Math.max(
          0,
          summary.usage.inputTokens - summary.usage.cacheReadTokens,
        ) *
          rate.input_per_mtok_micros +
          summary.usage.cacheReadTokens * rate.cache_read_per_mtok_micros +
          summary.usage.outputTokens * rate.output_per_mtok_micros) /
        1e12
      : null;

  return (
    <div
      data-testid="playground-turn"
      style={{
        border: "1px solid var(--ew-border-subtle)",
        borderRadius: 6,
        overflow: "hidden",
        // The transcript is a flex column — without this the (single) turn
        // shrinks to the container height and its overflow:hidden clips the
        // events instead of letting the transcript scroll. Keep natural height.
        flexShrink: 0,
      }}
    >
      {/* User message */}
      <div
        style={{
          padding: "8px 12px",
          background: "var(--ew-surface-raised)",
          borderBottom: "1px solid var(--ew-border-subtle)",
        }}
      >
        <Text style={{ whiteSpace: "pre-wrap", fontSize: 13 }}>
          {turn.input}
        </Text>
        {turn.attachments.length > 0 && (
          <div style={{ marginTop: 4 }}>
            {turn.attachments.map((a) => (
              <Tag key={a.id} bordered={false} style={{ fontSize: 11 }}>
                {a.name}
              </Tag>
            ))}
          </div>
        )}
      </div>

      {/* Agent answer */}
      <div style={{ padding: "8px 12px" }} data-testid="playground-turn-answer">
        {turn.status === "error" ? (
          <Alert
            type="error"
            showIcon
            message={t("playground.stream_failed")}
            description={turn.error}
            data-testid="playground-turn-error"
          />
        ) : answer !== null ? (
          // While streaming, render raw text (markdown reflow on every token is
          // janky + partial fences render oddly); render markdown once the turn
          // settles.
          turn.status === "running" ? (
            <Text style={{ whiteSpace: "pre-wrap", fontSize: 13 }}>
              {answer}
            </Text>
          ) : (
            <MarkdownView>{answer}</MarkdownView>
          )
        ) : (
          <Text type="secondary" style={{ fontSize: 12 }}>
            {t("playground.turn_no_text")}
          </Text>
        )}

        {/* A+B — inline download row for artifacts this turn registered. */}
        {turnArtifacts.length > 0 && threadId && (
          <div
            style={{
              marginTop: 8,
              display: "flex",
              gap: 6,
              flexWrap: "wrap",
              alignItems: "center",
            }}
            data-testid="playground-turn-artifacts"
          >
            <Text type="secondary" style={{ fontSize: 11 }}>
              {t("playground.workspace_artifacts")}:
            </Text>
            {turnArtifacts.map((a) => (
              <Button
                key={a.name}
                size="small"
                icon={<Download size={11} strokeWidth={1.75} />}
                loading={downloadingArtifact === a.name}
                onClick={() => void downloadArtifact(a.name)}
                aria-label={t("playground.artifact_download", { name: a.name })}
                data-testid="playground-turn-artifact-download"
              >
                {a.name}
              </Button>
            ))}
          </div>
        )}

        {/* #5 — approval gate (run paused on an approval-required tool). */}
        {turn.approval && threadId && (
          <ApprovalGate
            approval={turn.approval}
            busy={deciding}
            onDecide={(decision) => onDecide(turn.id, turn.approval!, decision)}
          />
        )}

        <TurnMeta
          summary={summary}
          costCny={costCny}
          runId={runId}
          threadId={threadId}
        />

        {/* SE-16 (SE-A46) — per-turn 👍/👎 quality signal feeding the
            skill-evolution curation pipeline. Settled turns only. */}
        {turn.status === "done" && threadId && (
          <FeedbackBar threadId={threadId} turnSeq={turnSeq} />
        )}
      </div>

      {/* Reasoning (collapsed) + events (expanded by default). */}
      <Collapse
        ghost
        size="small"
        defaultActiveKey={["events"]}
        items={[
          ...(summary.reasoning.length > 0
            ? [
                {
                  key: "reasoning",
                  label: t("playground.reasoning_label"),
                  children: (
                    <pre
                      data-testid="playground-reasoning"
                      style={{
                        margin: 0,
                        fontSize: 11,
                        fontFamily: "var(--ew-font-mono)",
                        color: "var(--ew-text-secondary)",
                        whiteSpace: "pre-wrap",
                        wordBreak: "break-word",
                        maxHeight: 240,
                        overflow: "auto",
                      }}
                    >
                      {summary.reasoning.join("\n\n———\n\n")}
                    </pre>
                  ),
                },
              ]
            : []),
          {
            key: "events",
            label: (
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: 8,
                }}
              >
                <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                  {t("playground.events_label")}
                  {toolStats.total > 0 && (
                    <Tag bordered={false} style={{ margin: 0 }} data-testid="playground-tool-count">
                      {t("playground.tool_count", { count: toolStats.total })}
                    </Tag>
                  )}
                  {toolStats.failed > 0 && (
                    <Tag color="error" bordered={false} style={{ margin: 0 }} data-testid="playground-tool-failed">
                      {t("playground.tool_failed_count", { count: toolStats.failed })}
                    </Tag>
                  )}
                </span>
                {/* Toggle lives next to the content it switches; stop the click
                    from collapsing the panel. */}
                <span
                  onClick={(e) => e.stopPropagation()}
                  role="presentation"
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 8,
                  }}
                >
                  <Segmented<"timeline" | "raw">
                    size="small"
                    value={eventView}
                    onChange={onViewChange}
                    options={[
                      {
                        value: "timeline",
                        label: t("event_stream.view_timeline"),
                      },
                      { value: "raw", label: t("event_stream.view_raw") },
                    ]}
                    data-testid="playground-event-view-toggle"
                  />
                  <Button
                    size="small"
                    icon={<Download size={13} strokeWidth={1.75} />}
                    loading={exporting}
                    onClick={() => onExport(turn)}
                    title={t("playground.export_json_tip")}
                    aria-label={t("playground.export_json")}
                    data-testid="playground-export-json"
                  >
                    {t("playground.export_json")}
                  </Button>
                </span>
              </div>
            ),
            children:
              turn.events.length === 0 ? (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {t("playground.empty_log")}
                </Text>
              ) : eventView === "timeline" ? (
                <ToolTimeline
                  events={turn.events}
                  awaitingApproval={turn.approval !== null}
                />
              ) : (
                <div
                  style={{ display: "flex", flexDirection: "column", gap: 8 }}
                >
                  {turn.events.map((evt, idx) => (
                    <EventCard key={`${evt.receivedAt}-${idx}`} evt={evt} />
                  ))}
                </div>
              ),
          },
        ]}
      />
    </div>
  );
}

function EventCard({ evt }: { evt: SseEvent }) {
  const tagColor = EVENT_COLOR[evt.event] ?? "default";
  const display =
    typeof evt.data === "string" ? evt.data : JSON.stringify(evt.data, null, 2);
  return (
    <div
      style={{
        border: "1px solid var(--ew-border-subtle)",
        borderRadius: 4,
        padding: 8,
        background: "var(--ew-surface-raised)",
      }}
      data-testid={`playground-event-${evt.event}`}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          marginBottom: 4,
          fontSize: 11,
        }}
      >
        <Tag color={tagColor} bordered={false} style={{ margin: 0 }}>
          {evt.event}
        </Tag>
        <Text type="secondary" style={{ fontSize: 11 }} className="mono">
          {new Date(evt.receivedAt).toLocaleTimeString()}
        </Text>
        <span style={{ marginLeft: "auto" }}>
          <CopyButton text={display} testId="playground-event-copy" />
        </span>
      </div>
      <pre
        style={{
          margin: 0,
          fontSize: 11,
          fontFamily: "var(--ew-font-mono)",
          color: "var(--ew-text-secondary)",
          whiteSpace: "pre-wrap",
          wordBreak: "break-all",
          maxHeight: 280,
          overflow: "auto",
        }}
      >
        {display}
      </pre>
    </div>
  );
}
