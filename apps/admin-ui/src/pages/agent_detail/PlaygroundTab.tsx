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
import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  Alert,
  AutoComplete,
  Button,
  Collapse,
  Empty,
  Input,
  Segmented,
  Select,
  Space,
  Tag,
  Typography,
} from "antd";
import {
  AlertTriangle,
  Check,
  ExternalLink,
  FileText,
  HardDrive,
  History,
  ImagePlus,
  Play,
  RefreshCw,
  RotateCcw,
  Send,
  Square,
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
  getSessionMessages,
  getSessionWorkspace,
  listSessions,
  streamRun,
  type HistoryMessage,
  type RunRequest,
  type SessionWorkspace,
  type SseEvent,
  type ThreadMeta,
} from "../../api/sessions";
import { summarizeTurn } from "../../api/turn_summary";
import { uploadDocument, uploadImage } from "../../api/uploads";
import { CopyButton } from "../../components/CopyButton";
import { ToolTimeline } from "../../components/ToolTimeline";
import type { AgentDetailResponse } from "../../api/agents";
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

interface PlaygroundTabProps {
  detail: AgentDetailResponse;
}

const EVENT_COLOR: Record<string, string> = {
  metadata: "blue",
  updates: "geekblue",
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
  const [eventView, setEventView] = useState<"timeline" | "raw">("timeline");
  const [running, setRunning] = useState(false);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [varValues, setVarValues] = useState<Record<string, string>>({});
  // Playground-Uplift D1 — impersonation. Empty = run as self.
  const [runAsUser, setRunAsUser] = useState("");
  const [userOptions, setUserOptions] = useState<UserOption[]>([]);
  // Playground-Uplift D4 — workspace inspector (verify the VM started + persists).
  const [workspace, setWorkspace] = useState<SessionWorkspace | null>(null);
  const [workspaceLoading, setWorkspaceLoading] = useState(false);
  // Playground-Uplift iter2 — #4 cost (agent model's rate), #6 resume history.
  const [rate, setRate] = useState<RateCardRecord | null>(null);
  const [pastSessions, setPastSessions] = useState<ThreadMeta[]>([]);
  const [resumed, setResumed] = useState(false);
  // #6 — prior conversation loaded when resuming an existing thread.
  const [history, setHistory] = useState<HistoryMessage[]>([]);

  const abortRef = useRef<AbortController | null>(null);
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

  // #6 resume — the caller's recent threads for THIS agent (newest first).
  const refreshPastSessions = useCallback(async () => {
    try {
      const all = await listSessions({ limit: 100 });
      setPastSessions(all.filter((s) => s.agent_name === r.name));
    } catch {
      // Picker is a convenience.
    }
  }, [r.name]);

  useEffect(() => {
    void refreshPastSessions();
  }, [refreshPastSessions]);

  const newThread = useCallback(async () => {
    setCreatingThread(true);
    setThreadError(null);
    setResumed(false);
    setHistory([]);
    setTurns([]);
    setAttachments([]);
    setUploadError(null);
    try {
      const created = await createSession({
        agent_name: r.name,
        agent_version: r.version,
        ...(runAsUser.trim() ? { run_as_user_id: runAsUser.trim() } : {}),
      });
      setThread(created);
    } catch (err) {
      const message =
        err instanceof ApiError
          ? `${err.code}: ${err.message}`
          : err instanceof Error
            ? err.message
            : "unknown error";
      setThreadError(message);
      setThread(null);
    } finally {
      setCreatingThread(false);
    }
  }, [r.name, r.version, runAsUser]);

  // #6 — resume an existing thread: switch to it + continue chatting (the
  // backend keeps the context). Past turns aren't replayed in the transcript;
  // a banner makes that explicit.
  const handleResume = useCallback(
    (threadId: string) => {
      const picked = pastSessions.find((s) => s.thread_id === threadId);
      if (!picked) return;
      abortRef.current?.abort();
      setTurns([]);
      setAttachments([]);
      setThreadError(null);
      setResumed(true);
      setHistory([]);
      setThread(picked);
      // Load the thread's prior conversation from the checkpoint.
      void getSessionMessages(threadId)
        .then(setHistory)
        .catch(() => setHistory([]));
    },
    [pastSessions],
  );

  // Re-bind a fresh thread when the agent or the impersonated user changes.
  useEffect(() => {
    void newThread();
    return () => {
      abortRef.current?.abort();
    };
  }, [newThread]);

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
        if (!file || !thread) return;
        setUploading(true);
        setUploadError(null);
        try {
          const value =
            kind === "image"
              ? await uploadImage(thread.thread_id, file)
              : await uploadDocument(thread.thread_id, file);
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
    [thread],
  );

  const handleRemoveAttachment = useCallback((id: string) => {
    setAttachments((prev) => prev.filter((a) => a.id !== id));
  }, []);

  const patchTurn = useCallback((id: string, patch: Partial<Turn>) => {
    setTurns((prev) => prev.map((tn) => (tn.id === id ? { ...tn, ...patch } : tn)));
  }, []);

  // #5 — a paused run registers its agent_approval row just after the stream's
  // end frame, so poll briefly (race) for a pending approval on this thread.
  const detectApproval = useCallback(
    async (turnId: string, threadId: string, runId: string | null) => {
      for (let attempt = 0; attempt < 4; attempt++) {
        try {
          const list = await listApprovals({ status: "pending" });
          const match = list.items.find(
            (a) => a.thread_id === threadId && (runId === null || a.run_id === runId),
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
    if (!thread || running) return;
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
    const threadId = thread.thread_id;
    try {
      for await (const frame of streamRun(threadId, body, { signal: ac.signal })) {
        frames.push(frame);
        setTurns((prev) =>
          prev.map((tn) =>
            tn.id === turnId ? { ...tn, events: [...tn.events, frame] } : tn,
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
    if (frames.at(-1)?.event === "end" && summarizeTurn(frames).finalText === null) {
      void detectApproval(turnId, threadId, runIdOf(frames));
    }
  }, [
    thread,
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
    async (turnId: string, approval: ApprovalItem, decision: "approve" | "reject") => {
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
      if (frames.at(-1)?.event === "end" && summarizeTurn(frames).finalText === null) {
        void detectApproval(turnId, threadId, continuationRunId);
      }
    },
    [thread, patchTurn, detectApproval],
  );

  const loadWorkspace = useCallback(async (threadId: string) => {
    setWorkspaceLoading(true);
    try {
      setWorkspace(await getSessionWorkspace(threadId));
    } catch {
      setWorkspace(null);
    } finally {
      setWorkspaceLoading(false);
    }
  }, []);

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
      {/* Left — session + input */}
      <div
        style={{
          border: "1px solid var(--hx-border-subtle)",
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
            <Select
              size="small"
              value={null}
              placeholder={t("playground.resume_label")}
              suffixIcon={<History size={12} strokeWidth={1.75} />}
              disabled={running || pastSessions.length === 0}
              popupMatchSelectWidth={false}
              onChange={handleResume}
              aria-label={t("playground.resume_label")}
              data-testid="playground-resume-select"
              options={pastSessions.map((s) => ({
                value: s.thread_id,
                label: `${s.thread_id.slice(0, 8)} · ${new Date(s.created_at).toLocaleString()}`,
              }))}
              style={{ width: 160 }}
            />
            <Button
              size="small"
              icon={<RotateCcw size={12} strokeWidth={1.75} />}
              onClick={newThread}
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
        ) : (
          <Text type="secondary" style={{ fontSize: 12 }} className="mono">
            {thread
              ? `${t("playground.thread_id")}: ${thread.thread_id}`
              : t("playground.loading_thread")}
          </Text>
        )}

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
                  disabled={running || !thread}
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
          disabled={running || !thread}
          maxLength={8192}
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
            disabled={!thread || (!running && input.trim().length === 0)}
            data-testid="playground-run"
          >
            {running ? t("playground.running") : t("playground.run")}
          </Button>
          <Button
            icon={<ImagePlus size={14} strokeWidth={1.75} />}
            onClick={() => fileInputRef.current?.click()}
            loading={uploading}
            disabled={!thread || running}
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
            disabled={!thread || running}
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
              borderTop: "1px solid var(--hx-border-subtle)",
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
            {workspace && workspace.artifacts.length > 0 && (
              <div style={{ marginTop: 6 }}>
                <Text type="secondary" style={{ fontSize: 11 }}>
                  {t("playground.workspace_artifacts")}:
                </Text>
                <div style={{ marginTop: 4 }}>
                  {workspace.artifacts.map((a) => (
                    <Tag
                      key={a.name}
                      bordered={false}
                      style={{ fontSize: 10, marginBottom: 2 }}
                    >
                      {a.name} · {a.kind} v{a.latest_version}
                    </Tag>
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
          border: "1px solid var(--hx-border-subtle)",
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
            borderBottom: "1px solid var(--hx-border-subtle)",
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
              style={{ display: "flex", flexDirection: "column", gap: 8, flexShrink: 0 }}
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
                        ? "var(--hx-surface-raised)"
                        : "transparent",
                    border:
                      m.role === "user"
                        ? "1px solid var(--hx-border-subtle)"
                        : "none",
                    opacity: 0.75,
                  }}
                >
                  {m.content}
                </div>
              ))}
              <div
                style={{
                  textAlign: "center",
                  fontSize: 11,
                  color: "var(--hx-text-tertiary)",
                  borderTop: "1px dashed var(--hx-border-subtle)",
                  paddingTop: 6,
                  marginTop: 2,
                }}
              >
                {t("playground.history_divider")}
              </div>
            </div>
          )}
          {turns.map((turn) => (
            <TurnCard
              key={turn.id}
              turn={turn}
              eventView={eventView}
              onViewChange={setEventView}
              threadId={thread?.thread_id ?? null}
              rate={rate}
              onDecide={handleDecide}
              deciding={running}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function runIdOf(events: readonly SseEvent[]): string | null {
  for (const e of events) {
    if (e.event === "metadata" && e.data !== null && typeof e.data === "object") {
      const rid = (e.data as Record<string, unknown>).run_id;
      if (typeof rid === "string" && rid) return rid;
    }
  }
  return null;
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
        border: "1px solid var(--hx-color-warning, #d4a017)",
        borderRadius: 6,
        padding: 10,
        marginTop: 8,
        background: "var(--hx-surface-raised)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
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
          fontFamily: "var(--hx-font-mono)",
          color: "var(--hx-text-secondary)",
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
  eventView,
  onViewChange,
  threadId,
  rate,
  onDecide,
  deciding,
}: {
  turn: Turn;
  eventView: "timeline" | "raw";
  onViewChange: (view: "timeline" | "raw") => void;
  threadId: string | null;
  rate: RateCardRecord | null;
  onDecide: (turnId: string, approval: ApprovalItem, decision: "approve" | "reject") => void;
  deciding: boolean;
}) {
  const { t } = useTranslation();
  const summary = summarizeTurn(turn.events);
  const answer =
    summary.finalText ??
    (turn.status === "running" ? t("playground.turn_running") : null);
  const runId = runIdOf(turn.events);
  // #4 cost — non-cached input + cache_read + output, each at its per-mtok rate
  // (micro-元 per 1M tokens). null when no usage or no rate for the model.
  const costCny =
    summary.usage && rate
      ? (Math.max(0, summary.usage.inputTokens - summary.usage.cacheReadTokens) *
          rate.input_per_mtok_micros +
          summary.usage.cacheReadTokens * rate.cache_read_per_mtok_micros +
          summary.usage.outputTokens * rate.output_per_mtok_micros) /
        1e12
      : null;

  return (
    <div
      data-testid="playground-turn"
      style={{
        border: "1px solid var(--hx-border-subtle)",
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
          background: "var(--hx-surface-raised)",
          borderBottom: "1px solid var(--hx-border-subtle)",
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
          <Text style={{ whiteSpace: "pre-wrap", fontSize: 13 }}>{answer}</Text>
        ) : (
          <Text type="secondary" style={{ fontSize: 12 }}>
            {t("playground.turn_no_text")}
          </Text>
        )}

        {/* #5 — approval gate (run paused on an approval-required tool). */}
        {turn.approval && threadId && (
          <ApprovalGate
            approval={turn.approval}
            busy={deciding}
            onDecide={(decision) => onDecide(turn.id, turn.approval!, decision)}
          />
        )}

        {/* Per-turn usage chips */}
        {summary.usage && (
          <div
            style={{ marginTop: 8, display: "flex", gap: 6, flexWrap: "wrap" }}
            data-testid="playground-usage"
          >
            <Tag bordered={false} color="geekblue">
              {t("playground.usage_in")}: {summary.usage.inputTokens}
            </Tag>
            <Tag bordered={false} color="geekblue">
              {t("playground.usage_out")}: {summary.usage.outputTokens}
            </Tag>
            <Tag bordered={false}>
              {t("playground.usage_total")}: {summary.usage.totalTokens}
            </Tag>
            {summary.usage.cacheReadTokens > 0 && (
              <Tag bordered={false} color="green">
                {t("playground.usage_cache")}: {summary.usage.cacheReadTokens}
              </Tag>
            )}
            {summary.usage.reasoningTokens > 0 && (
              <Tag bordered={false} color="purple">
                {t("playground.usage_reasoning")}:{" "}
                {summary.usage.reasoningTokens}
              </Tag>
            )}
          </div>
        )}

        {/* #4 step / latency / cost + #8 run-detail link. */}
        {(summary.stepCount !== null ||
          summary.latencyMs !== null ||
          costCny !== null ||
          (runId && threadId)) && (
          <div
            style={{
              marginTop: 6,
              display: "flex",
              gap: 6,
              flexWrap: "wrap",
              alignItems: "center",
            }}
            data-testid="playground-turn-meta"
          >
            {summary.stepCount !== null && (
              <Tag bordered={false}>
                {t("playground.meta_steps")}: {summary.stepCount}
              </Tag>
            )}
            {summary.latencyMs !== null && (
              <Tag bordered={false}>
                {t("playground.meta_latency")}:{" "}
                {(summary.latencyMs / 1000).toFixed(1)}s
              </Tag>
            )}
            {costCny !== null && (
              <Tag bordered={false} color="gold" data-testid="playground-turn-cost">
                ≈ ¥{costCny.toFixed(4)}
              </Tag>
            )}
            {runId && threadId && (
              <Link
                to={`/runs/${threadId}/${runId}`}
                style={{
                  fontSize: 12,
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 3,
                }}
                data-testid="playground-turn-run-link"
              >
                {t("playground.view_run")}
                <ExternalLink size={11} strokeWidth={1.75} />
              </Link>
            )}
          </div>
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
                        fontFamily: "var(--hx-font-mono)",
                        color: "var(--hx-text-secondary)",
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
                <span>{t("playground.events_label")}</span>
                {/* Toggle lives next to the content it switches; stop the click
                    from collapsing the panel. */}
                <span
                  onClick={(e) => e.stopPropagation()}
                  role="presentation"
                >
                  <Segmented<"timeline" | "raw">
                    size="small"
                    value={eventView}
                    onChange={onViewChange}
                    options={[
                      { value: "timeline", label: t("event_stream.view_timeline") },
                      { value: "raw", label: t("event_stream.view_raw") },
                    ]}
                    data-testid="playground-event-view-toggle"
                  />
                </span>
              </div>
            ),
            children:
              turn.events.length === 0 ? (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {t("playground.empty_log")}
                </Text>
              ) : eventView === "timeline" ? (
                <ToolTimeline events={turn.events} />
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
        border: "1px solid var(--hx-border-subtle)",
        borderRadius: 4,
        padding: 8,
        background: "var(--hx-surface-raised)",
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
          fontFamily: "var(--hx-font-mono)",
          color: "var(--hx-text-secondary)",
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
