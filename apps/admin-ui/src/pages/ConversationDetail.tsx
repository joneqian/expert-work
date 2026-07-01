/**
 * Conversation detail — one ``(agent, user, session=thread)`` conversation
 * (``docs/design/conversation-centric-ia.md``).
 *
 * Shows the conversation summary (agent / user / status / token rollup /
 * last active) and its run list; each run drills into the existing
 * per-run detail (``/runs/{thread}/{run}``) with its event stream,
 * approval card, and Langfuse deep link. The unified cross-run message
 * transcript is M1.5 (needs a checkpoint-read API).
 */
import { useCallback, useEffect, useState } from "react";
import { Alert, Card, Empty, Skeleton, Space, Table, Tag, Tooltip, Typography } from "antd";
import type { TableColumnsType } from "antd";
import { useParams } from "react-router-dom";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { ApiError } from "../api/client";
import {
  getConversation,
  type ConversationDetail as ConversationDetailModel,
  type ConversationRun,
} from "../api/conversations";
import { PageHeader } from "../components/PageHeader";
import { formatCompact, formatDuration } from "../utils/runFormat";

const { Text } = Typography;

const STATUS_COLOR: Record<string, string> = {
  active: "processing",
  paused: "warning",
  completed: "success",
  failed: "error",
  cancelled: "default",
  archived: "default",
  pending: "default",
  running: "processing",
  success: "success",
  error: "error",
  timeout: "error",
  interrupted: "default",
};

export function ConversationDetail() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { threadId } = useParams<{ threadId: string }>();

  const [convo, setConvo] = useState<ConversationDetailModel | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!threadId) return;
    setLoading(true);
    setError(null);
    try {
      setConvo(await getConversation(threadId));
    } catch (err) {
      setError(
        err instanceof ApiError
          ? `${err.code}: ${err.message}`
          : err instanceof Error
            ? err.message
            : "unknown error",
      );
    } finally {
      setLoading(false);
    }
  }, [threadId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const columns: TableColumnsType<ConversationRun> = [
    {
      title: t("runs_page.column_run_id"),
      dataIndex: "run_id",
      key: "run_id",
      width: 180,
      render: (id: string) => (
        <Tooltip title={id}>
          <Text code style={{ fontSize: 12 }}>
            {id.slice(0, 8)}…
          </Text>
        </Tooltip>
      ),
    },
    {
      title: t("runs_page.column_status"),
      dataIndex: "status",
      key: "status",
      width: 120,
      render: (status: string, record) => {
        const tag = <Tag color={STATUS_COLOR[status] ?? "default"}>{status}</Tag>;
        if (!record.error) return tag;
        return (
          <Tooltip title={record.error}>
            <span data-testid={`conversation-run-error-${record.run_id}`}>{tag}</span>
          </Tooltip>
        );
      },
    },
    {
      title: t("runs_page.column_duration"),
      key: "duration",
      width: 100,
      render: (_: unknown, record) => (
        <Text type="secondary" style={{ fontSize: 12 }}>
          {formatDuration(t, record.created_at, record.finished_at)}
        </Text>
      ),
    },
    {
      title: t("runs_page.column_tokens"),
      key: "tokens",
      width: 90,
      render: (_: unknown, record) => {
        const tk = record.tokens;
        if (!tk || tk.total_tokens === 0) return <Text type="secondary">—</Text>;
        return <Text style={{ fontSize: 12 }}>{formatCompact(tk.total_tokens)}</Text>;
      },
    },
    {
      title: t("conversations_detail.column_started"),
      dataIndex: "created_at",
      key: "created_at",
      width: 190,
      render: (iso: string) => (
        <Text type="secondary" style={{ fontSize: 12 }}>
          {new Date(iso).toLocaleString()}
        </Text>
      ),
    },
  ];

  if (!threadId) {
    return <Empty description="Missing :threadId" style={{ marginTop: 80 }} />;
  }
  if (loading) {
    return <Skeleton active paragraph={{ rows: 6 }} />;
  }
  if (error !== null || convo === null) {
    return (
      <Alert
        type="error"
        showIcon
        message={t("conversations_detail.failed_to_load")}
        description={error ?? "conversation not found"}
        data-testid="conversation-detail-error"
      />
    );
  }

  const tk = convo.tokens;
  // Back to the agent's conversations tab when the thread is agent-bound;
  // the global conversation browser (M1b-2) will deep-link here too.
  const backTo =
    convo.agent_name && convo.agent_version
      ? {
          label: convo.agent_name,
          to: `/agents/${encodeURIComponent(convo.agent_name)}/${encodeURIComponent(
            convo.agent_version,
          )}/conversations`,
        }
      : { label: t("nav.agents"), to: "/agents" };

  return (
    <div data-testid="conversation-detail-root">
      <PageHeader
        title={convo.title ?? t("conversations_page.untitled")}
        backTo={backTo}
        subtitle={
          <Space size={8} align="center" wrap>
            <Tag color={STATUS_COLOR[convo.status] ?? "default"} bordered={false}>
              {convo.status}
            </Tag>
            {convo.agent_name && (
              <Text type="secondary" style={{ fontSize: 12 }}>
                {convo.agent_name}
                {convo.agent_version ? ` v${convo.agent_version}` : ""}
              </Text>
            )}
            <span>
              {t("conversations_detail.thread_label")}:{" "}
              <Text code style={{ fontSize: 12 }}>
                {convo.thread_id.slice(0, 12)}…
              </Text>
            </span>
          </Space>
        }
      />

      <Card size="small" title={t("conversations_detail.summary_title")}>
        <dl
          style={{
            display: "grid",
            gridTemplateColumns: "140px 1fr",
            rowGap: 8,
            columnGap: 16,
            margin: 0,
            fontSize: 13,
          }}
        >
          <dt style={{ color: "var(--hx-text-tertiary)" }}>{t("conversations_detail.user")}</dt>
          <dd className="mono" style={{ margin: 0 }}>
            {convo.user_id ?? "—"}
          </dd>
          <dt style={{ color: "var(--hx-text-tertiary)" }}>{t("conversations_page.column_runs")}</dt>
          <dd style={{ margin: 0 }}>
            {convo.run_count}
            {convo.error_count > 0 &&
              ` · ${t("conversations_page.error_count", { count: convo.error_count })}`}
          </dd>
          <dt style={{ color: "var(--hx-text-tertiary)" }}>
            {t("conversations_detail.tokens")}
          </dt>
          <dd style={{ margin: 0 }}>
            {tk && tk.total_tokens > 0 ? (
              <span data-testid="conversation-tokens">
                {t("conversations_detail.tokens_value", {
                  total: formatCompact(tk.total_tokens),
                  input: tk.input_tokens,
                  output: tk.output_tokens,
                  calls: tk.llm_calls,
                })}
              </span>
            ) : (
              "—"
            )}
          </dd>
          <dt style={{ color: "var(--hx-text-tertiary)" }}>
            {t("conversations_detail.models")}
          </dt>
          <dd style={{ margin: 0 }}>
            {tk && tk.models.length > 0 ? tk.models.join(", ") : "—"}
          </dd>
          <dt style={{ color: "var(--hx-text-tertiary)" }}>
            {t("conversations_page.column_last_active")}
          </dt>
          <dd style={{ margin: 0 }}>
            {convo.last_run_at ? new Date(convo.last_run_at).toLocaleString() : "—"}
          </dd>
        </dl>
      </Card>

      <Card
        size="small"
        title={t("conversations_detail.runs_title")}
        style={{ marginTop: 16 }}
        data-testid="conversation-runs"
      >
        <Table<ConversationRun>
          size="small"
          columns={columns}
          dataSource={convo.runs}
          rowKey={(record) => record.run_id}
          pagination={false}
          onRow={(record) => ({
            onClick: () =>
              navigate(
                `/runs/${encodeURIComponent(record.thread_id)}/${encodeURIComponent(record.run_id)}`,
              ),
            style: { cursor: "pointer" },
          })}
          locale={{ emptyText: <Empty description={t("conversations_detail.runs_empty")} /> }}
          data-testid="conversation-runs-table"
        />
      </Card>
    </div>
  );
}
