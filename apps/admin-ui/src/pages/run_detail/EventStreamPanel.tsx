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
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Alert, Button, Card, Empty, Segmented, Space, Tag, Typography } from "antd";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";

import { streamRunEvents } from "../../api/runs";
import type { SseEvent } from "../../api/sessions";
import { parseCompactionEvents, type CompactionSummary } from "../../api/tool_timeline";
import { CopyButton } from "../../components/CopyButton";
import { ToolTimeline } from "../../components/ToolTimeline";

const { Text } = Typography;

const LOCAL_STORAGE_KEY = "expert_work.runDetail.eventStream.expanded";

const EVENT_COLOR: Record<string, string> = {
  metadata: "blue",
  updates: "geekblue",
  error: "red",
  end: "green",
  compaction: "purple",
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
  const [view, setView] = useState<"timeline" | "raw">("timeline");
  const compactions = useMemo(() => parseCompactionEvents(events), [events]);

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
          <Segmented<"timeline" | "raw">
            size="small"
            value={view}
            onChange={setView}
            options={[
              { value: "timeline", label: t("event_stream.view_timeline") },
              { value: "raw", label: t("event_stream.view_raw") },
            ]}
            style={{ marginBottom: 8 }}
            data-testid="event-stream-view-toggle"
          />
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
            {events.length > 0 && view === "timeline" && (
              <>
                {compactions.length > 0 && <CompactionSummaryList items={compactions} />}
                <ToolTimeline events={events} />
              </>
            )}
            {view === "raw" &&
              events.map((evt, idx) => <EventCard key={`${evt.receivedAt}-${idx}`} evt={evt} />)}
          </div>
        </div>
      )}
    </Card>
  );
}

function CompactionSummaryList({ items }: { items: readonly CompactionSummary[] }) {
  return (
    <div
      data-testid="compaction-summary-list"
      style={{ display: "flex", flexDirection: "column", gap: 8 }}
    >
      {items.map((item, idx) => (
        <CompactionCard key={`${item.receivedAt}-${idx}`} item={item} />
      ))}
    </div>
  );
}

function CompactionCard({ item }: { item: CompactionSummary }) {
  const { t } = useTranslation();
  const reductionPct =
    item.tokensBefore > 0
      ? Math.max(0, Math.round(((item.tokensBefore - item.tokensAfter) / item.tokensBefore) * 100))
      : 0;
  return (
    <div
      data-testid="compaction-card"
      style={{
        border: "1px solid var(--ew-border-subtle)",
        borderRadius: 6,
        padding: 10,
        background: "var(--ew-surface-raised)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <Tag color="purple" bordered={false} style={{ margin: 0 }}>
          {t("event_stream.compaction_label")}
        </Tag>
        <Text type="secondary" style={{ fontSize: 12 }}>
          {t("event_stream.compaction_passes", { n: item.passes })}
        </Text>
        <Text className="mono" style={{ fontSize: 12 }}>
          {t("event_stream.compaction_reduction", {
            before: item.tokensBefore.toLocaleString(),
            after: item.tokensAfter.toLocaleString(),
            pct: reductionPct,
          })}
        </Text>
        <Text type="secondary" style={{ fontSize: 12 }}>
          {t("event_stream.compaction_summary_chars", { n: item.summaryChars.toLocaleString() })}
        </Text>
      </div>
    </div>
  );
}

function EventCard({ evt }: { evt: SseEvent }) {
  const tagColor = EVENT_COLOR[evt.event] ?? "default";
  const display = typeof evt.data === "string" ? evt.data : JSON.stringify(evt.data, null, 2);
  return (
    <div
      style={{
        border: "1px solid var(--ew-border-subtle)",
        borderRadius: 4,
        padding: 8,
        background: "var(--ew-surface-raised)",
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
        <span style={{ marginLeft: "auto" }}>
          <CopyButton text={display} testId="event-stream-copy" />
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
          maxHeight: 240,
          overflow: "auto",
        }}
      >
        {display}
      </pre>
    </div>
  );
}
