/**
 * Playground tab — Stream H.2 PR 3.
 *
 * Per-agent debug surface backed by real ``/v1/sessions`` + ``/v1/sessions/
 * {thread_id}/runs`` SSE. On mount the tab creates a fresh thread bound
 * to the agent; the user types a prompt, clicks Run, and the SSE frames
 * stream into the right panel in real time.
 *
 * The "edit manifest snippet + re-run" affordance from the original
 * design doc is a follow-up — it depends on backend support for an
 * ad-hoc manifest override that doesn't exist yet (today the bound
 * spec is the active ``AgentSpecRecord``).
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, Button, Empty, Input, Space, Tag, Typography } from "antd";
import { ImagePlus, Play, RotateCcw, Send, Square, X } from "lucide-react";
import { useTranslation } from "react-i18next";

import { ApiError } from "../../api/client";
import {
  createSession,
  streamRun,
  type SseEvent,
  type ThreadMeta,
} from "../../api/sessions";
import { uploadImage } from "../../api/uploads";
import type { AgentDetailResponse } from "../../api/agents";

/** An image attached to the next turn — uploaded ahead of Run so the
 *  run request carries only the lightweight ``helix://image/...`` ref. */
interface Attachment {
  id: string;
  name: string;
  ref: string;
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

export function PlaygroundTab({ detail }: PlaygroundTabProps) {
  const { t } = useTranslation();
  const r = detail.record;

  const [thread, setThread] = useState<ThreadMeta | null>(null);
  const [threadError, setThreadError] = useState<string | null>(null);
  const [creatingThread, setCreatingThread] = useState(false);
  const [input, setInput] = useState("");
  const [events, setEvents] = useState<SseEvent[]>([]);
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const eventListRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const newThread = useCallback(async () => {
    setCreatingThread(true);
    setThreadError(null);
    setEvents([]);
    setRunError(null);
    setAttachments([]);
    setUploadError(null);
    try {
      const created = await createSession({
        agent_name: r.name,
        agent_version: r.version,
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
  }, [r.name, r.version]);

  useEffect(() => {
    void newThread();
    return () => {
      abortRef.current?.abort();
    };
  }, [newThread]);

  // Auto-scroll the event log as new frames arrive.
  useEffect(() => {
    const node = eventListRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [events]);

  const handleAttach = useCallback(
    async (event: React.ChangeEvent<HTMLInputElement>) => {
      const file = event.target.files?.[0];
      // Reset the input so picking the same file twice still fires onChange.
      event.target.value = "";
      if (!file || !thread) return;
      setUploading(true);
      setUploadError(null);
      try {
        const ref = await uploadImage(thread.thread_id, file);
        setAttachments((prev) => [...prev, { id: ref, name: file.name, ref }]);
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
    setRunError(null);
    setEvents([]);
    const imageRefs = attachments.map((a) => a.ref);
    const ac = new AbortController();
    abortRef.current = ac;
    try {
      for await (const frame of streamRun(
        thread.thread_id,
        imageRefs.length > 0 ? { input, image_refs: imageRefs } : { input },
        { signal: ac.signal },
      )) {
        setEvents((prev) => [...prev, frame]);
        if (frame.event === "end") break;
      }
      // The turn consumed the attached images — clear so the next turn
      // starts fresh. On error we keep them so the user can retry.
      setAttachments([]);
    } catch (err) {
      if (err instanceof Error && err.name === "AbortError") {
        // Cancelled by the user — not an error.
      } else {
        const message = err instanceof Error ? err.message : "stream failed";
        setRunError(message);
      }
    } finally {
      setRunning(false);
      abortRef.current = null;
    }
  }, [thread, input, running, attachments]);

  const handleStop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

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
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
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
            {thread ? `${t("playground.thread_id")}: ${thread.thread_id}` : t("playground.loading_thread")}
          </Text>
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
          onChange={handleAttach}
          data-testid="playground-file-input"
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
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 6 }}>
              {attachments.map((a) => (
                <Tag
                  key={a.id}
                  closable
                  onClose={(e) => {
                    e.preventDefault();
                    handleRemoveAttachment(a.id);
                  }}
                  closeIcon={
                    <X size={11} strokeWidth={1.75} aria-label={t("playground.remove_attachment")} />
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
            icon={running ? <Play size={14} strokeWidth={1.75} /> : <Send size={14} strokeWidth={1.75} />}
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
            {uploading ? t("playground.uploading") : t("playground.attach_image")}
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

      {/* Right — event log */}
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
            {t("playground.event_log")}
          </Text>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {events.length === 0 ? "" : t("playground.event_count", { n: events.length })}
          </Text>
        </div>

        {runError !== null && (
          <Alert
            type="error"
            showIcon
            message={t("playground.stream_failed")}
            description={runError}
            style={{ margin: 12 }}
            data-testid="playground-stream-error"
          />
        )}

        <div
          ref={eventListRef}
          style={{
            flex: 1,
            padding: 12,
            overflow: "auto",
            display: "flex",
            flexDirection: "column",
            gap: 8,
          }}
          data-testid="playground-event-log"
        >
          {events.length === 0 && runError === null && (
            <Empty
              description={t("playground.empty_log")}
              style={{ marginTop: 64 }}
              data-testid="playground-empty-log"
            />
          )}
          {events.map((evt, idx) => (
            <EventCard key={`${evt.receivedAt}-${idx}`} evt={evt} />
          ))}
        </div>
      </div>
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
