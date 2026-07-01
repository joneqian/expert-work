/**
 * Run summary panel — the "what happened this run" glance.
 *
 * Renders duration + token usage (from helix's own ``token_usage``, joined by
 * trace_id — NOT a Langfuse round-trip) so the run detail answers "how long,
 * how many tokens, which models" without leaving the tenant-isolated
 * control-plane UI. Deep per-span traces stay delegated to Langfuse via the
 * ``TraceToolbar`` external link.
 */
import { Card, Space, Tag, Typography } from "antd";
import { useTranslation } from "react-i18next";

import type { RunDetail as RunDetailModel } from "../../api/runs";

const { Text } = Typography;

type TFn = (key: string, opts?: Record<string, unknown>) => string;

function compact(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}

function formatDuration(t: TFn, createdIso?: string | null, finishedIso?: string | null): string | null {
  if (!createdIso) return null;
  if (!finishedIso) return t("runs_page.duration_running");
  const seconds = Math.max(
    0,
    Math.round((new Date(finishedIso).getTime() - new Date(createdIso).getTime()) / 1000),
  );
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${seconds % 60}s`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

const LABEL_STYLE = { color: "var(--hx-text-tertiary)", fontSize: 12, marginBottom: 2 };

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div>
      <div style={LABEL_STYLE}>{label}</div>
      <Text strong style={{ fontSize: 15 }}>
        {value}
      </Text>
      {hint && (
        <div style={{ color: "var(--hx-text-tertiary)", fontSize: 11 }}>{hint}</div>
      )}
    </div>
  );
}

interface RunSummaryPanelProps {
  run: RunDetailModel;
}

export function RunSummaryPanel({ run }: RunSummaryPanelProps) {
  const { t } = useTranslation();
  const tk = run.tokens ?? null;
  const dur = formatDuration(t, run.created_at, run.finished_at);
  const hasTokens = tk !== null && tk.total_tokens > 0;

  return (
    <Card
      size="small"
      title={t("runs_page.summary_title")}
      data-testid="run-summary"
      style={{ marginTop: 16 }}
    >
      <Space size={28} wrap align="start">
        {dur !== null && <Stat label={t("runs_page.summary_duration")} value={dur} />}
        {hasTokens && tk ? (
          <>
            <Stat
              label={t("runs_page.summary_tokens")}
              value={compact(tk.total_tokens)}
              hint={
                `${compact(tk.input_tokens)} ${t("runs_page.summary_input")} / ` +
                `${compact(tk.output_tokens)} ${t("runs_page.summary_output")}` +
                (tk.cache_read_tokens > 0
                  ? ` / ${compact(tk.cache_read_tokens)} ${t("runs_page.summary_cache")}`
                  : "")
              }
            />
            <Stat label={t("runs_page.summary_llm_calls")} value={String(tk.llm_calls)} />
            {tk.models.length > 0 && (
              <div>
                <div style={LABEL_STYLE}>{t("runs_page.summary_models")}</div>
                <Space size={4} wrap>
                  {tk.models.map((m) => (
                    <Tag key={m} style={{ marginInlineEnd: 0 }}>
                      {m}
                    </Tag>
                  ))}
                </Space>
              </div>
            )}
          </>
        ) : (
          <Text type="secondary" data-testid="run-summary-no-tokens">
            {t("runs_page.summary_no_tokens")}
          </Text>
        )}
      </Space>
    </Card>
  );
}
