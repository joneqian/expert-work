/**
 * TurnMeta — the per-turn metric row of a playground turn: token usage chips,
 * step / latency / cost, the model + finish_reason debug chips, and the
 * "view run" deep link. Extracted from TurnCard so the metric logic is unit
 * testable and TurnCard stays focused (§8 refactor).
 */
import { Tag } from "antd";
import { ExternalLink } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import type { TurnSummary } from "../../../api/turn_summary";

export interface TurnMetaProps {
  summary: TurnSummary;
  /** ≈CNY for the turn (null when no usage or no rate). */
  costCny: number | null;
  runId: string | null;
  threadId: string | null;
}

export function TurnMeta({
  summary,
  costCny,
  runId,
  threadId,
}: TurnMetaProps) {
  const { t } = useTranslation();
  const { usage, stepCount, latencyMs, finishReason, modelName } = summary;
  // "stop" is the normal terminal reason — only surface the interesting ones
  // (length / content_filter / a turn that ended on tool_calls).
  const showFinish = finishReason !== null && finishReason !== "stop";

  const hasUsageRow = usage !== null;
  const hasMetaRow =
    stepCount !== null ||
    latencyMs !== null ||
    costCny !== null ||
    modelName !== null ||
    showFinish ||
    Boolean(runId && threadId);

  if (!hasUsageRow && !hasMetaRow) return null;

  return (
    <>
      {usage && (
        <div
          style={{ marginTop: 8, display: "flex", gap: 6, flexWrap: "wrap" }}
          data-testid="playground-usage"
        >
          <Tag bordered={false} color="geekblue">
            {t("playground.usage_in")}: {usage.inputTokens}
          </Tag>
          <Tag bordered={false} color="geekblue">
            {t("playground.usage_out")}: {usage.outputTokens}
          </Tag>
          <Tag bordered={false}>
            {t("playground.usage_total")}: {usage.totalTokens}
          </Tag>
          {usage.cacheReadTokens > 0 && (
            <Tag bordered={false} color="green">
              {t("playground.usage_cache")}: {usage.cacheReadTokens}
            </Tag>
          )}
          {usage.cacheCreationTokens > 0 && (
            <Tag bordered={false} color="cyan" data-testid="playground-turn-cache-write">
              {t("playground.usage_cache_write")}: {usage.cacheCreationTokens}
            </Tag>
          )}
          {usage.reasoningTokens > 0 && (
            <Tag bordered={false} color="purple">
              {t("playground.usage_reasoning")}: {usage.reasoningTokens}
            </Tag>
          )}
        </div>
      )}

      {hasMetaRow && (
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
          {stepCount !== null && (
            <Tag bordered={false}>
              {t("playground.meta_steps")}: {stepCount}
            </Tag>
          )}
          {latencyMs !== null && (
            <Tag bordered={false}>
              {t("playground.meta_latency")}: {(latencyMs / 1000).toFixed(1)}s
            </Tag>
          )}
          {modelName !== null && (
            <Tag bordered={false} color="blue" data-testid="playground-turn-model">
              {t("playground.meta_model")}: {modelName}
            </Tag>
          )}
          {showFinish && (
            <Tag bordered={false} color="orange" data-testid="playground-turn-finish">
              {t("playground.meta_finish")}: {finishReason}
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
    </>
  );
}
