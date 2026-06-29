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
import {
  Alert,
  AutoComplete,
  Button,
  Collapse,
  Empty,
  Input,
  Segmented,
  Space,
  Tag,
  Typography,
} from "antd";
import {
  FileText,
  ImagePlus,
  Play,
  RotateCcw,
  Send,
  Square,
  User,
  X,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { ApiError } from "../../api/client";
import { listMembers } from "../../api/members";
import {
  createSession,
  streamRun,
  type RunRequest,
  type SseEvent,
  type ThreadMeta,
} from "../../api/sessions";
import { summarizeTurn } from "../../api/turn_summary";
import { uploadDocument, uploadImage } from "../../api/uploads";
import { CopyButton } from "../../components/CopyButton";
import { ToolTimeline } from "../../components/ToolTimeline";
import type { AgentDetailResponse } from "../../api/agents";
import {
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

  const newThread = useCallback(async () => {
    setCreatingThread(true);
    setThreadError(null);
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
      },
    ]);
    // Consume the input + attachments — the next turn starts fresh.
    setInput("");
    setAttachments([]);

    const ac = new AbortController();
    abortRef.current = ac;
    try {
      for await (const frame of streamRun(thread.thread_id, body, {
        signal: ac.signal,
      })) {
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
  }, [thread, input, running, attachments, promptVariables, varValues, turns.length, t]);

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
        </div>

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
      </div>

      {/* Right — conversation transcript */}
      <div
        style={{
          border: "1px solid var(--hx-border-subtle)",
          borderRadius: 6,
          padding: 0,
          display: "flex",
          flexDirection: "column",
          minHeight: "calc(100vh - 360px)",
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
          <Segmented<"timeline" | "raw">
            size="small"
            value={eventView}
            onChange={setEventView}
            options={[
              { value: "timeline", label: t("event_stream.view_timeline") },
              { value: "raw", label: t("event_stream.view_raw") },
            ]}
            style={{ marginLeft: "auto" }}
            data-testid="playground-event-view-toggle"
          />
        </div>

        <div
          ref={transcriptRef}
          style={{
            flex: 1,
            padding: 12,
            overflow: "auto",
            display: "flex",
            flexDirection: "column",
            gap: 12,
          }}
          data-testid="playground-transcript"
        >
          {turns.length === 0 && (
            <Empty
              description={t("playground.empty_log")}
              style={{ marginTop: 64 }}
              data-testid="playground-empty-log"
            />
          )}
          {turns.map((turn) => (
            <TurnCard key={turn.id} turn={turn} eventView={eventView} />
          ))}
        </div>
      </div>
    </div>
  );
}

function TurnCard({
  turn,
  eventView,
}: {
  turn: Turn;
  eventView: "timeline" | "raw";
}) {
  const { t } = useTranslation();
  const summary = summarizeTurn(turn.events);
  const answer =
    summary.finalText ??
    (turn.status === "running" ? t("playground.turn_running") : null);

  return (
    <div
      data-testid="playground-turn"
      style={{
        border: "1px solid var(--hx-border-subtle)",
        borderRadius: 6,
        overflow: "hidden",
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
            label: t("playground.events_label"),
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
