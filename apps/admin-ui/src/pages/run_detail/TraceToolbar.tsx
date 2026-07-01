/**
 * Trace toolbar — Stream H.3 PR 6 (Mini-ADR H-8).
 *
 * Surfaces the ``agent_run.trace_id`` captured in PR 7b and (when the
 * deploy is wired against Langfuse) deep-links to the trace timeline.
 *
 * The actual span timeline lives in OTLP / Langfuse — embedding it
 * directly is out of scope for H.3 (Mini-ADR H-8 records the decision
 * to ship an external link first, defer the embed to H.4). This
 * component owns the contract on the UI side:
 *
 *   - When ``trace_id`` is ``null`` (legacy rows, runs that errored
 *     before the trace started), we show the chip with a muted "no
 *     trace recorded" placeholder so the metadata panel still has the
 *     expected shape.
 *   - The "Open in Langfuse" link is conditional on
 *     ``VITE_LANGFUSE_BASE_URL`` being set at build time — deployments
 *     without a Langfuse instance show the trace_id + copy button only.
 */
import { Button, Card, Space, Tooltip, Typography, message } from "antd";
import { Copy, ExternalLink } from "lucide-react";
import { useTranslation } from "react-i18next";

import { buildLangfuseTraceUrl } from "../../config/env";
import { useAuth } from "../../auth/AuthContext";

const { Text } = Typography;

interface TraceToolbarProps {
  traceId: string | null;
}

export function TraceToolbar({ traceId }: TraceToolbarProps) {
  const { t } = useTranslation();
  const { identity } = useAuth();
  // Langfuse has no per-tenant isolation (single ClickHouse, all tenants
  // mixed), so the deep link is platform-ops only — a tenant user must not
  // be handed a cross-tenant trace URL. They still get the trace_id + copy.
  const isSystemAdmin = identity?.isSystemAdmin ?? false;
  const langfuseUrl = isSystemAdmin ? buildLangfuseTraceUrl(traceId) : null;

  const onCopy = async (): Promise<void> => {
    if (traceId === null) return;
    try {
      await navigator.clipboard.writeText(traceId);
      message.success(t("trace_toolbar.copied"));
    } catch {
      // Clipboard access denied — silently fail. The user can still
      // select + copy the visible trace_id from the chip.
    }
  };

  return (
    <Card
      data-testid="trace-toolbar"
      size="small"
      title={t("trace_toolbar.title")}
    >
      {traceId === null ? (
        <Text type="secondary" data-testid="trace-toolbar-empty">
          {t("trace_toolbar.no_trace")}
        </Text>
      ) : (
        <Space size={8} wrap>
          <Text
            code
            className="mono"
            data-testid="trace-toolbar-id"
            style={{ fontSize: 12 }}
          >
            {traceId}
          </Text>
          <Tooltip title={t("trace_toolbar.copy_aria")}>
            <Button
              type="text"
              size="small"
              icon={<Copy size={14} strokeWidth={1.75} />}
              aria-label={t("trace_toolbar.copy_aria")}
              data-testid="trace-toolbar-copy"
              onClick={onCopy}
            />
          </Tooltip>
          {langfuseUrl !== null ? (
            <Button
              type="link"
              size="small"
              icon={<ExternalLink size={14} strokeWidth={1.75} />}
              href={langfuseUrl}
              target="_blank"
              rel="noopener noreferrer"
              data-testid="trace-toolbar-langfuse"
            >
              {t("trace_toolbar.open_in_langfuse")}
            </Button>
          ) : isSystemAdmin ? (
            <Tooltip title={t("trace_toolbar.langfuse_unconfigured_hint")}>
              <Text type="secondary" style={{ fontSize: 12 }}>
                {t("trace_toolbar.open_in_langfuse")}
              </Text>
            </Tooltip>
          ) : null}
        </Space>
      )}
    </Card>
  );
}
