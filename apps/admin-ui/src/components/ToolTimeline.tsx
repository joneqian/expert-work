/**
 * ToolTimeline — a readable view of an agent run's tool activity.
 *
 * Parses the raw SSE ``updates`` frames into an ordered list of tool calls
 * (see ``parseToolCalls``) and renders each as a timeline entry: tool name
 * (with an MCP server badge for ``mcp:server.tool`` calls), status, the
 * call arguments, and a preview of the result. Answers "did the agent call
 * tool/MCP X, with what, and did it work?" at a glance — which the raw
 * event dump does not.
 */
import { useMemo, type ReactNode } from "react";
import { Collapse, Empty, Tag, Typography } from "antd";
import { useTranslation } from "react-i18next";

import type { SseEvent } from "../api/sessions";
import { parseToolCalls, type ToolCallEntry, type ToolCallStatus } from "../api/tool_timeline";

const { Text } = Typography;

const STATUS_COLOR: Record<ToolCallStatus, string> = {
  pending: "processing",
  success: "success",
  error: "error",
};

function pretty(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

interface ToolTimelineProps {
  events: readonly SseEvent[];
}

export function ToolTimeline({ events }: ToolTimelineProps) {
  const { t } = useTranslation();
  const entries = useMemo(() => parseToolCalls(events), [events]);

  if (entries.length === 0) {
    return <Empty description={t("tool_timeline.empty")} data-testid="tool-timeline-empty" />;
  }

  return (
    <div data-testid="tool-timeline" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {entries.map((entry, idx) => (
        <ToolCallCard key={`${entry.id}-${idx}`} entry={entry} />
      ))}
    </div>
  );
}

function ToolCallCard({ entry }: { entry: ToolCallEntry }) {
  const { t } = useTranslation();
  const statusLabel = t(`tool_timeline.status_${entry.status}`);
  const hasArgs = Object.keys(entry.args).length > 0;

  const items: { key: string; label: string; children: ReactNode }[] = [];
  if (hasArgs) {
    items.push({
      key: "args",
      label: t("tool_timeline.args_label"),
      children: (
        <pre style={{ margin: 0, fontSize: 11, fontFamily: "var(--hx-font-mono)" }}>
          {pretty(entry.args)}
        </pre>
      ),
    });
  }
  if (entry.resultPreview) {
    items.push({
      key: "result",
      label: t("tool_timeline.result_label"),
      children: (
        <pre
          style={{
            margin: 0,
            fontSize: 11,
            fontFamily: "var(--hx-font-mono)",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            maxHeight: 240,
            overflow: "auto",
          }}
        >
          {entry.resultPreview}
        </pre>
      ),
    });
  }

  return (
    <div
      data-testid="tool-call-card"
      style={{
        border: "1px solid var(--hx-border-subtle)",
        borderRadius: 6,
        padding: 10,
        background: "var(--hx-surface-raised)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        {entry.isMcp ? (
          <Tag color="blue" bordered={false} style={{ margin: 0 }}>
            {t("tool_timeline.mcp_badge")}
            {entry.server ? ` · ${entry.server}` : ""}
          </Tag>
        ) : (
          <Tag bordered={false} style={{ margin: 0 }}>
            {t("tool_timeline.builtin_badge")}
          </Tag>
        )}
        <Text strong className="mono" style={{ fontSize: 13 }}>
          {entry.toolName}
        </Text>
        <Tag color={STATUS_COLOR[entry.status]} bordered={false} style={{ margin: 0 }}>
          {statusLabel}
        </Tag>
      </div>
      {items.length > 0 && (
        <Collapse
          ghost
          size="small"
          items={items}
          style={{ marginTop: 4 }}
          data-testid="tool-call-detail"
        />
      )}
    </div>
  );
}
