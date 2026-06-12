/**
 * Memory tab — Stream H.6 PR 2.
 *
 * Read-only view of the long-term memory the agent reads. Memory is a
 * *per-user* asset (Mini-ADR H-13) — there is no agent_name dimension
 * on memory rows, and inventing one would be a fake filter. Under the
 * per-user persistent-agent product form a user's memory IS their
 * agent instance's memory, so this tab shows the tenant's per-user
 * items with the scope stated up front. Governance actions (edit /
 * delete / review) live on the global /memory admin page.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Alert, Card, Empty, Select, Space, Table, Tag, Tooltip, Typography } from "antd";
import type { TableColumnsType } from "antd";
import { Brain } from "lucide-react";
import { useTranslation } from "react-i18next";

import { ApiError } from "../../api/client";
import { listMemories, type MemoryItem, type MemoryKind } from "../../api/memory";

const { Text } = Typography;

export function MemoryTab() {
  const { t } = useTranslation();

  const [items, setItems] = useState<MemoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [kindFilter, setKindFilter] = useState<MemoryKind | undefined>(undefined);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await listMemories({ kind: kindFilter });
      setItems(result.items);
    } catch (err) {
      setError(err instanceof ApiError ? `${err.code}: ${err.message}` : String(err));
    } finally {
      setLoading(false);
    }
  }, [kindFilter]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const columns: TableColumnsType<MemoryItem> = useMemo(
    () => [
      {
        title: t("memory_tab.col_kind"),
        dataIndex: "kind",
        key: "kind",
        width: 110,
        render: (kind: string) => (
          <Tag color={kind === "fact" ? "blue" : "purple"} bordered={false}>
            {kind}
          </Tag>
        ),
      },
      {
        title: t("memory_tab.col_content"),
        dataIndex: "content",
        key: "content",
        ellipsis: true,
      },
      {
        title: t("memory_tab.col_user"),
        dataIndex: "user_id",
        key: "user_id",
        width: 140,
        render: (id: string) => (
          <Tooltip title={id}>
            <Text code style={{ fontSize: 12 }}>
              {id.slice(0, 8)}…
            </Text>
          </Tooltip>
        ),
      },
      {
        title: t("memory_tab.col_created"),
        dataIndex: "created_at",
        key: "created_at",
        width: 200,
        render: (iso: string) => (
          <Text type="secondary" style={{ fontSize: 12 }}>
            {new Date(iso).toLocaleString()}
          </Text>
        ),
      },
    ],
    [t],
  );

  return (
    <Card
      title={
        <Space size={8}>
          <Brain size={15} strokeWidth={1.5} />
          {t("memory_tab.title")}
        </Space>
      }
      extra={
        <Select<MemoryKind | "all">
          value={kindFilter ?? "all"}
          onChange={(v) => setKindFilter(v === "all" ? undefined : (v as MemoryKind))}
          style={{ width: 130 }}
          size="small"
          aria-label={t("memory_tab.filter_kind")}
          data-testid="memory-tab-kind-filter"
          options={[
            { value: "all", label: t("memory_tab.filter_kind_all") },
            { value: "fact", label: "fact" },
            { value: "episodic", label: "episodic" },
          ]}
        />
      }
      data-testid="memory-tab-root"
    >
      <Alert
        type="info"
        showIcon
        message={t("memory_tab.user_scope_note")}
        style={{ marginBottom: 12 }}
        data-testid="memory-tab-scope-note"
      />
      {error !== null && (
        <Alert
          type="error"
          showIcon
          message={error}
          style={{ marginBottom: 12 }}
          data-testid="memory-tab-error"
        />
      )}
      <Table<MemoryItem>
        size="small"
        columns={columns}
        dataSource={items}
        rowKey="id"
        loading={loading}
        pagination={false}
        locale={{ emptyText: <Empty description={t("memory_tab.empty")} /> }}
        data-testid="memory-tab-table"
      />
    </Card>
  );
}
