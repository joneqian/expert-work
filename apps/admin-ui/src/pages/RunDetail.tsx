/**
 * Run detail page — Stream H.1b PR 3.
 *
 * Real fetch of ``GET /v1/sessions/{thread_id}/runs/{run_id}`` —
 * Mini-ADR J-41's durable run row, augmented with any pending
 * approval. ``POST .../resume`` lets a reviewer approve or reject a
 * pending approval inline.
 *
 * Route shape changed from the demo's ``/runs/:runId`` to the real
 * ``/runs/:threadId/:runId`` because the backend identity is the
 * tuple ``(thread_id, run_id)`` — there is no flat ``GET /v1/runs``
 * endpoint yet (Mini-ADR J-41 keeps the per-thread shape).
 *
 * Trace / span visualisation is intentionally out of scope here —
 * spans live in OTLP traces, not in the control-plane API surface.
 * H.4 wires the Tempo / Grafana embed.
 */
import { useCallback, useEffect, useState } from "react";
import { App, Alert, Breadcrumb, Button, Card, Empty, Skeleton, Space, Tag, Typography } from "antd";
import { Link, useNavigate, useParams } from "react-router-dom";
import { AlertTriangle, ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";

import { ApiError } from "../api/client";
import {
  getRun,
  resumeRun,
  type RunDetail as RunDetailModel,
  type RunStatus,
} from "../api/runs";

const { Text } = Typography;

const STATUS_COLOR: Record<RunStatus, string> = {
  pending: "default",
  queued: "default",
  running: "processing",
  paused: "warning",
  awaiting_approval: "warning",
  success: "success",
  completed: "success",
  error: "error",
  failed: "error",
  timeout: "error",
  interrupted: "default",
  cancelled: "default",
  unknown: "default",
};

export function RunDetail() {
  const { t } = useTranslation();
  const { threadId, runId } = useParams<{ threadId: string; runId: string }>();
  const navigate = useNavigate();
  const { message } = App.useApp();

  const [run, setRun] = useState<RunDetailModel | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [resuming, setResuming] = useState(false);

  const refresh = useCallback(async () => {
    if (!threadId || !runId) return;
    setLoading(true);
    setError(null);
    try {
      setRun(await getRun(threadId, runId));
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? `${err.code}: ${err.message}`
          : err instanceof Error
            ? err.message
            : "unknown error";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [threadId, runId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const handleResume = async (approved: boolean) => {
    if (!threadId || !runId) return;
    setResuming(true);
    try {
      await resumeRun(threadId, runId, { approved });
      message.success(t(approved ? "run_detail.approved" : "run_detail.rejected"));
      await refresh();
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      message.error(msg);
    } finally {
      setResuming(false);
    }
  };

  if (!threadId || !runId) {
    return <Empty description="Missing :threadId or :runId" style={{ marginTop: 80 }} />;
  }

  if (loading) {
    return <Skeleton active paragraph={{ rows: 8 }} />;
  }

  if (error !== null || run === null) {
    return (
      <Alert
        type="error"
        showIcon
        message={t("run_detail.failed_to_load")}
        description={error ?? "run not found"}
        data-testid="run-detail-error"
      />
    );
  }

  const approval = run.pending_approval;

  return (
    <div data-testid="run-detail-root">
      <Breadcrumb
        items={[
          { title: <Link to="/runs">{t("cmdk.label_runs")}</Link> },
          { title: <Text code style={{ fontSize: 12 }}>{run.run_id.slice(0, 8)}…</Text> },
        ]}
        style={{ marginBottom: 8, fontSize: 13 }}
        separator={<ChevronRight size={12} strokeWidth={1.5} style={{ verticalAlign: "middle" }} />}
      />

      <div className="hx-page-header">
        <div>
          <Space size={8} align="center">
            <h1 style={{ fontFamily: "var(--hx-font-mono)", margin: 0 }}>
              {run.run_id.slice(0, 12)}…
            </h1>
            <Tag color={STATUS_COLOR[run.status] ?? "default"} bordered={false}>
              {run.status}
            </Tag>
          </Space>
          <p style={{ margin: "8px 0 0", color: "var(--hx-text-secondary)", fontSize: 13 }}>
            {t("run_detail.thread_label")}:{" "}
            <Text code style={{ fontSize: 12 }}>
              {run.thread_id.slice(0, 12)}…
            </Text>
          </p>
        </div>
        <Button onClick={() => void refresh()} loading={loading}>
          {t("common.refresh")}
        </Button>
      </div>

      {approval !== null && (
        <Alert
          showIcon
          icon={<AlertTriangle size={16} strokeWidth={1.5} />}
          type="warning"
          message={
            <strong>
              {approval.node} — {t("run_detail.awaiting_approval")}
            </strong>
          }
          description={
            <div style={{ marginTop: 8 }}>
              <p style={{ margin: "0 0 8px", color: "var(--hx-text-secondary)" }}>
                {approval.action_summary}
              </p>
              <Space size={16} style={{ marginBottom: 8, fontSize: 12, color: "var(--hx-text-tertiary)" }}>
                <span>
                  {t("run_detail.reason_kind")}:{" "}
                  <Text code style={{ fontSize: 11 }}>
                    {approval.reason_kind}
                  </Text>
                </span>
                <span>
                  {t("run_detail.requested_at")}: {new Date(approval.requested_at).toLocaleString()}
                </span>
                <span>
                  {t("run_detail.timeout_at")}: {new Date(approval.timeout_at).toLocaleString()}
                </span>
              </Space>
              <div style={{ fontSize: 12, color: "var(--hx-text-tertiary)", marginBottom: 4 }}>
                {t("run_detail.proposed_args")}:
              </div>
              <pre
                style={{
                  margin: 0,
                  padding: 12,
                  background: "var(--hx-surface-base)",
                  borderRadius: 6,
                  fontFamily: "var(--hx-font-mono)",
                  fontSize: 11,
                  color: "var(--hx-text-primary)",
                  overflow: "auto",
                  maxHeight: 240,
                }}
              >
                {JSON.stringify(approval.proposed_args, null, 2)}
              </pre>
              <Space style={{ marginTop: 12 }}>
                <Button
                  type="primary"
                  loading={resuming}
                  onClick={() => void handleResume(true)}
                  data-testid="run-approve"
                >
                  {t("run_detail.approve")}
                </Button>
                <Button
                  danger
                  loading={resuming}
                  onClick={() => void handleResume(false)}
                  data-testid="run-reject"
                >
                  {t("run_detail.reject")}
                </Button>
                <Button onClick={() => navigate("/runs")}>{t("common.cancel")}</Button>
              </Space>
            </div>
          }
          style={{ marginBottom: 16 }}
        />
      )}

      <Card title={t("run_detail.run_metadata")} size="small">
        <dl
          style={{
            display: "grid",
            gridTemplateColumns: "160px 1fr",
            rowGap: 8,
            columnGap: 16,
            margin: 0,
            fontSize: 13,
          }}
        >
          <dt style={{ color: "var(--hx-text-tertiary)" }}>{t("run_detail.run_id")}</dt>
          <dd className="mono" style={{ margin: 0 }}>{run.run_id}</dd>
          <dt style={{ color: "var(--hx-text-tertiary)" }}>{t("run_detail.thread_id")}</dt>
          <dd className="mono" style={{ margin: 0 }}>{run.thread_id}</dd>
          <dt style={{ color: "var(--hx-text-tertiary)" }}>{t("run_detail.status")}</dt>
          <dd style={{ margin: 0 }}>{run.status}</dd>
        </dl>
      </Card>

      <Alert
        type="info"
        showIcon
        style={{ marginTop: 16 }}
        message={t("run_detail.trace_in_observability")}
      />
    </div>
  );
}
