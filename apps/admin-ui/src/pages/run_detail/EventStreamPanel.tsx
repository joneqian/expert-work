/**
 * Event stream panel — Stream H.3 PR 4 (Mini-ADR H-7).
 *
 * Reads the new ``GET /v1/sessions/{thread}/runs/{run}/events`` endpoint
 * and renders the frames in the same color-coded style as the
 * Playground tab. Two backend modes, one client UI:
 *
 *   - Active run → ``stream-mode: live`` header; events arrive until
 *     the bridge emits ``end``.
 *   - Terminal run → ``stream-mode: replay`` header; events replay
 *     from the durable ``run_event`` table and the endpoint closes.
 *
 * **Default state is collapsed** (decision E) — admins arriving at
 * RunDetail to approve / audit don't always want a live SSE pipe to
 * open. The toggle persists per-user via ``localStorage`` so a debug-
 * heavy reviewer keeps it open across page loads.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, Button, Card, Empty, Space, Tag, Typography } from "antd";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";

import { streamRunEvents } from "../../api/runs";
import type { SseEvent } from "../../api/sessions";

const { Text } = Typography;

const LOCAL_STORAGE_KEY = "helix.runDetail.eventStream.expanded";

const EVENT_COLOR: Record<string, string> = {
  metadata: "blue",
  updates: "geekblue",
  error: "red",
  end: "green",
};

interface EventStreamPanelProps {
  threadId: string;
  runId: string;
}

export function EventStreamPanel({ threadId, runId }: EventStreamPanelProps) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    return window.localStorage.getItem(LOCAL_STORAGE_KEY) === "1";
  });
  const [events, setEvents] = useState<SseEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [connecting, setConnecting] = useState(false);

  const abortRef = useRef<AbortController | null>(null);
  const eventListRef = useRef<HTMLDivElement>(null);

  const setExpandedPersistent = useCallback((next: boolean) => {
    setExpanded(next);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(LOCAL_STORAGE_KEY, next ? "1" : "0");
    }
  }, []);

  // Auto-scroll the event log as new frames arrive.
  useEffect(() => {
    const node = eventListRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [events]);

  // Open the SSE pipe when the user expands the panel; close it when
  // collapsed (or the component unmounts). The endpoint distinguishes
  // live vs replay server-side; the client UI is identical.
  useEffect(() => {
    if (!expanded) {
      abortRef.current?.abort();
      abortRef.current = null;
      return;
    }
    setEvents([]);
    setError(null);
    setConnecting(true);
    const ac = new AbortController();
    abortRef.current = ac;
    (async () => {
      try {
        for await (const frame of streamRunEvents(threadId, runId, { signal: ac.signal })) {
          setEvents((prev) => [...prev, frame]);
          if (frame.event === "end") break;
        }
      } catch (err) {
        if (err instanceof Error && err.name === "AbortError") {
          // Collapsed mid-stream — not an error.
        } else {
          const message = err instanceof Error ? err.message : "stream failed";
          setError(message);
        }
      } finally {
        setConnecting(false);
      }
    })();
    return () => {
      ac.abort();
    };
  }, [expanded, threadId, runId]);

  return (
    <Card
      data-testid="event-stream-panel"
      size="small"
      style={{ marginTop: 16 }}
      title={
        <Space size={8}>
          <Button
            type="text"
            size="small"
            icon={
              expanded ? (
                <ChevronDown size={14} strokeWidth={1.75} />
              ) : (
                <ChevronRight size={14} strokeWidth={1.75} />
              )
            }
            onClick={() => setExpandedPersistent(!expanded)}
            data-testid="event-stream-toggle"
            aria-expanded={expanded}
            aria-controls="event-stream-body"
          >
            {t("event_stream.title")}
          </Button>
          {expanded && (
            <Text type="secondary" style={{ fontSize: 12 }}>
              {connecting
                ? t("event_stream.connecting")
                : t("event_stream.event_count", { n: events.length })}
            </Text>
          )}
        </Space>
      }
    >
      {expanded && (
        <div id="event-stream-body">
          {error !== null && (
            <Alert
              type="error"
              showIcon
              message={t("event_stream.stream_failed")}
              description={error}
              style={{ marginBottom: 12 }}
              data-testid="event-stream-error"
            />
          )}
          <div
            ref={eventListRef}
            style={{
              maxHeight: 480,
              overflow: "auto",
              display: "flex",
              flexDirection: "column",
              gap: 8,
              padding: 4,
            }}
            data-testid="event-stream-body"
          >
            {events.length === 0 && error === null && !connecting && (
              <Empty description={t("event_stream.empty")} />
            )}
            {events.map((evt, idx) => (
              <EventCard key={`${evt.receivedAt}-${idx}`} evt={evt} />
            ))}
          </div>
        </div>
      )}
    </Card>
  );
}

function EventCard({ evt }: { evt: SseEvent }) {
  const tagColor = EVENT_COLOR[evt.event] ?? "default";
  const display = typeof evt.data === "string" ? evt.data : JSON.stringify(evt.data, null, 2);
  return (
    <div
      style={{
        border: "1px solid var(--hx-border-subtle)",
        borderRadius: 4,
        padding: 8,
        background: "var(--hx-surface-raised)",
      }}
      data-testid={`event-stream-event-${evt.event}`}
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
        {evt.id !== null && (
          <Text type="secondary" style={{ fontSize: 11 }} className="mono">
            {evt.id}
          </Text>
        )}
      </div>
      <pre
        style={{
          margin: 0,
          fontSize: 11,
          fontFamily: "var(--hx-font-mono)",
          color: "var(--hx-text-secondary)",
          whiteSpace: "pre-wrap",
          wordBreak: "break-all",
          maxHeight: 240,
          overflow: "auto",
        }}
      >
        {display}
      </pre>
    </div>
  );
}
