/**
 * ToolTimeline — a readable view of an agent run's tool activity.
 *
 * Parses the raw SSE ``updates`` frames into an ordered list of tool calls
 * (see ``parseToolCalls``) and renders each as a timeline entry: tool name
 * (with an MCP server badge for ``mcp__server__tool`` calls), status, the
 * call arguments, and a preview of the result. Answers "did the agent call
 * tool/MCP X, with what, and did it work?" at a glance — which the raw
 * event dump does not.
 */
import { useMemo, type ReactNode } from "react";
import { Collapse, Empty, Tag, Typography } from "antd";
import { useTranslation } from "react-i18next";

import type { SseEvent } from "../api/sessions";
import { parseToolCalls, type ToolCallEntry, type ToolCallStatus } from "../api/tool_timeline";
import { cleanUntrusted } from "../pages/agent_detail/playground/untrusted_clean";

const { Text } = Typography;

const STATUS_COLOR: Record<ToolCallStatus, string> = {
  pending: "processing",
  success: "success",
  error: "error",
  pending_approval: "warning",
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
  /** The run paused at an approval gate — render blocked tools as 待审批. */
  awaitingApproval?: boolean;
}

export function ToolTimeline({ events, awaitingApproval = false }: ToolTimelineProps) {
  const { t } = useTranslation();
  const entries = useMemo(
    () => parseToolCalls(events, awaitingApproval),
    [events, awaitingApproval],
  );

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

export function ToolCallCard({ entry }: { entry: ToolCallEntry }) {
  const { t } = useTranslation();
  const statusLabel = t(`tool_timeline.status_${entry.status}`);
  const hasArgs = Object.keys(entry.args).length > 0;

  const items: { key: string; label: ReactNode; children: ReactNode }[] = [];
  if (hasArgs) {
    items.push({
      key: "args",
      label: t("tool_timeline.args_label"),
      children: (
        <pre style={{ margin: 0, fontSize: 11, fontFamily: "var(--ew-font-mono)" }}>
          {pretty(entry.args)}
        </pre>
      ),
    });
  }
  if (entry.execResult) {
    const { stdout, stderr, exitCode } = entry.execResult;
    const stdoutClean = cleanUntrusted(stdout);
    const stderrClean = cleanUntrusted(stderr);
    // tool_timeline.ts already strips the «UNTRUSTED nonce=…» fence while
    // parsing the raw SSE frame, so cleanUntrusted's own fence-based
    // hadUntrusted is always false by the time it reaches this component —
    // the surviving ▁ datamark glyph is the only remaining evidence the
    // result was originally spotlighted, so it doubles as the badge signal.
    const hadUntrusted =
      stdoutClean.hadUntrusted ||
      stderrClean.hadUntrusted ||
      stdout.includes("▁") ||
      stderr.includes("▁");
    items.push({
      key: "result",
      label: (
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <span>{t("tool_timeline.result_label")}</span>
          {hadUntrusted && (
            <Tag color="warning" bordered={false} data-testid="tool-untrusted" style={{ margin: 0 }}>
              ⚑ {t("playground.tr_msg_untrusted")}
            </Tag>
          )}
        </span>
      ),
      children: (
        <div data-testid="tool-exec-result" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <div>
            <Tag
              color={exitCode === 0 ? "success" : "error"}
              bordered={false}
              data-testid="tool-exit-code"
            >
              {t("tool_timeline.exit_code")}: {exitCode ?? "?"}
            </Tag>
          </div>
          {stdoutClean.text && (
            <ExecStream label={t("tool_timeline.stdout_label")} text={stdoutClean.text} />
          )}
          {stderrClean.text && (
            <ExecStream label={t("tool_timeline.stderr_label")} text={stderrClean.text} tone="error" />
          )}
        </div>
      ),
    });
  } else if (entry.resultPreview) {
    const previewClean = cleanUntrusted(entry.resultPreview);
    const hadUntrusted = previewClean.hadUntrusted || entry.resultPreview.includes("▁");
    items.push({
      key: "result",
      label: (
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <span>{t("tool_timeline.result_label")}</span>
          {hadUntrusted && (
            <Tag color="warning" bordered={false} data-testid="tool-untrusted" style={{ margin: 0 }}>
              ⚑ {t("playground.tr_msg_untrusted")}
            </Tag>
          )}
        </span>
      ),
      children: (
        <pre
          style={{
            margin: 0,
            fontSize: 11,
            fontFamily: "var(--ew-font-mono)",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            maxHeight: 240,
            overflow: "auto",
          }}
        >
          {previewClean.text}
        </pre>
      ),
    });
  }

  return (
    <div
      data-testid="tool-call-card"
      style={{
        border: "1px solid var(--ew-border-subtle)",
        borderRadius: 6,
        padding: 10,
        background: "var(--ew-surface-raised)",
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

function ExecStream({ label, text, tone }: { label: string; text: string; tone?: "error" }) {
  return (
    <div>
      <Text type="secondary" style={{ fontSize: 11 }}>
        {label}
      </Text>
      <pre
        style={{
          margin: "2px 0 0",
          fontSize: 11,
          fontFamily: "var(--ew-font-mono)",
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
          maxHeight: 200,
          overflow: "auto",
          color: tone === "error" ? "var(--ew-text-danger, #cf1322)" : undefined,
        }}
      >
        {text}
      </pre>
    </div>
  );
}
