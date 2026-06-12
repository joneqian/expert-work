/**
 * Runs tab — Stream H.6 PR 2.
 *
 * Per-agent slice of the cross-thread runs index: ``GET /v1/runs``
 * narrowed by ``agent_name`` + ``agent_version`` (the two-step thread
 * resolve lands server-side, Mini-ADR H-10). Mirrors RunsList's table
 * shape minus the agent column (redundant here) and surfaces the
 * ``thread_window_capped`` signal as a warning Alert.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Alert, Card, Empty, Select, Space, Table, Tag, Tooltip, Typography } from "antd";
import type { TableColumnsType } from "antd";
import { Activity } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";

import type { AgentDetailResponse } from "../../api/agents";
import { ApiError } from "../../api/client";
import { listRuns, type RunList, type RunListItem, type RunStatus } from "../../api/runs";

const { Text } = Typography;

const STATUS_COLOR: Record<string, string> = {
  pending: "default",
  running: "processing",
  paused: "warning",
  success: "success",
  error: "error",
  timeout: "error",
  interrupted: "default",
};

const STATUS_OPTIONS: RunStatus[] = [
  "running",
  "paused",
  "success",
  "error",
  "timeout",
  "interrupted",
  "pending",
];

interface RunsTabProps {
  detail: AgentDetailResponse;
}

export function RunsTab({ detail }: RunsTabProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { name, version } = detail.record;

  const [data, setData] = useState<RunList | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<RunStatus | undefined>(undefined);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await listRuns({
        agentName: name,
        agentVersion: version,
        status: statusFilter,
      });
      setData(result);
    } catch (err) {
      setError(err instanceof ApiError ? `${err.code}: ${err.message}` : String(err));
    } finally {
      setLoading(false);
    }
  }, [name, version, statusFilter]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const columns: TableColumnsType<RunListItem> = useMemo(
    () => [
      {
        title: t("runs_page.column_run_id"),
        dataIndex: "run_id",
        key: "run_id",
        width: 200,
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
        width: 130,
        render: (status: string) => <Tag color={STATUS_COLOR[status] ?? "default"}>{status}</Tag>,
      },
      {
        title: t("runs_page.column_thread"),
        dataIndex: "thread_id",
        key: "thread",
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
        title: t("runs_page.column_created"),
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
          <Activity size={15} strokeWidth={1.5} />
          {t("runs_tab.title")}
        </Space>
      }
      extra={
        <Select<RunStatus | "all">
          value={statusFilter ?? "all"}
          onChange={(v) => setStatusFilter(v === "all" ? undefined : (v as RunStatus))}
          style={{ width: 150 }}
          size="small"
          aria-label={t("runs_page.filter_status")}
          data-testid="runs-tab-status-filter"
          options={[
            { value: "all", label: t("runs_page.filter_status_all") },
            ...STATUS_OPTIONS.map((s) => ({ value: s, label: s })),
          ]}
        />
      }
      data-testid="runs-tab-root"
    >
      {error !== null && (
        <Alert
          type="error"
          showIcon
          message={error}
          style={{ marginBottom: 12 }}
          data-testid="runs-tab-error"
        />
      )}
      {data?.thread_window_capped === true && (
        <Alert
          type="warning"
          showIcon
          message={t("runs_tab.window_capped")}
          style={{ marginBottom: 12 }}
          data-testid="runs-tab-window-capped"
        />
      )}
      <Table<RunListItem>
        size="small"
        columns={columns}
        dataSource={data?.items ?? []}
        rowKey={(record) => record.run_id}
        loading={loading}
        pagination={{ total: data?.total ?? 0, showSizeChanger: false, pageSize: 50 }}
        onRow={(record) => ({
          onClick: () =>
            navigate(
              `/runs/${encodeURIComponent(record.thread_id)}/${encodeURIComponent(record.run_id)}`,
            ),
          style: { cursor: "pointer" },
        })}
        locale={{ emptyText: <Empty description={t("runs_tab.empty")} /> }}
        data-testid="runs-tab-table"
      />
    </Card>
  );
}
