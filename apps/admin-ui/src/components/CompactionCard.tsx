/**
 * CompactionCard — shared compaction-summary card used by RunDetail's
 * EventStreamPanel timeline view (and available to the Playground, which
 * shares the underlying event stream).
 */
import { Tag, Typography } from "antd";
import { useTranslation } from "react-i18next";

import type { CompactionSummary } from "../api/tool_timeline";

const { Text } = Typography;

export function CompactionSummaryList({ items }: { items: readonly CompactionSummary[] }) {
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

export function CompactionCard({ item }: { item: CompactionSummary }) {
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
