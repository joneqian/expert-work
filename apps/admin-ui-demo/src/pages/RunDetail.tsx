import { useState } from "react";
import { useParams, Link } from "react-router-dom";
import { Alert, Breadcrumb, Button, Card, Col, Empty, Row, Space, Tabs, Tag, App } from "antd";
import { AlertTriangle, ChevronRight, RotateCcw, Copy, XCircle } from "lucide-react";
import { findRun, type MockSpan, type RunStatus } from "../mock/runs";

const STATUS_COLOR: Record<RunStatus, string> = {
  ok: "success",
  failed: "error",
  approval: "warning",
  running: "processing",
  cancelled: "default",
};
const STATUS_LABEL: Record<RunStatus, string> = {
  ok: "ok",
  failed: "failed",
  approval: "approval pending",
  running: "running",
  cancelled: "cancelled",
};

export function RunDetail() {
  const { runId } = useParams<{ runId: string }>();
  const baseRun = runId ? findRun(runId) : findRun("run_4c9b8e21f60d");
  const { message } = App.useApp();

  // 本地状态:approval pending → approved / rejected 转换
  const [resolution, setResolution] = useState<"pending" | "approved" | "rejected">("pending");

  if (!baseRun) {
    return <Empty description={`Run "${runId}" 不存在`} style={{ marginTop: 80 }} />;
  }

  const effectiveStatus: RunStatus =
    baseRun.status === "approval"
      ? resolution === "approved"
        ? "ok"
        : resolution === "rejected"
          ? "cancelled"
          : "approval"
      : baseRun.status;

  return (
    <div>
      <Breadcrumb
        items={[
          { title: "acme-corp" },
          { title: <Link to="/runs">Runs</Link> },
          { title: <span className="mono">{baseRun.id}</span> },
        ]}
        style={{ marginBottom: 8, fontSize: 13 }}
        separator={<ChevronRight size={12} strokeWidth={1.5} style={{ verticalAlign: "middle" }} />}
      />

      <div className="hx-page-header">
        <div>
          <Space size={8} align="center">
            <h1 style={{ fontFamily: "var(--hx-font-mono)", margin: 0 }}>{baseRun.id}</h1>
            <Tag color={STATUS_COLOR[effectiveStatus]} bordered={false} style={{ borderRadius: 2 }}>
              <span style={{ display: "inline-block", width: 6, height: 6, borderRadius: 999, background: "currentColor", marginRight: 4 }} />
              {STATUS_LABEL[effectiveStatus]}
            </Tag>
          </Space>
          <p style={{ margin: "8px 0 0", color: "var(--hx-text-secondary)" }}>
            <Link to={`/agents/${baseRun.agentId}/overview`}>{baseRun.agentId}</Link> · {baseRun.agentVersion} · started {baseRun.triggeredAt} by {baseRun.triggeredBy}
          </p>
        </div>
        <Space>
          <Button icon={<Copy size={14} strokeWidth={1.5} />}>复制 input → Playground</Button>
          <Button icon={<RotateCcw size={14} strokeWidth={1.5} />}>重跑 <span className="hx-kbd" style={{ marginLeft: 4 }}>.</span></Button>
          {effectiveStatus !== "ok" && effectiveStatus !== "cancelled" && (
            <Button danger icon={<XCircle size={14} strokeWidth={1.5} />}>强制结束</Button>
          )}
        </Space>
      </div>

      {/* Approval banner */}
      {effectiveStatus === "approval" && baseRun.approvalReason && (
        <Alert
          showIcon
          icon={<AlertTriangle size={16} strokeWidth={1.5} />}
          type="warning"
          message={<strong>handover.human — 等待审批</strong>}
          description={
            <div style={{ marginTop: 8 }}>
              <p style={{ margin: "0 0 8px", color: "var(--hx-text-secondary)" }}>{baseRun.approvalReason}</p>
              <div style={{ fontSize: 12, color: "var(--hx-text-tertiary)", marginBottom: 4 }}>建议参数:</div>
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
                }}
              >
                {JSON.stringify(baseRun.approvalArgs, null, 2)}
              </pre>
              <Space style={{ marginTop: 12 }}>
                <Button
                  type="primary"
                  onClick={() => {
                    setResolution("approved");
                    message.success("已批准。Agent 继续执行(demo 模拟)");
                  }}
                >
                  批准 <span className="hx-kbd" style={{ marginLeft: 4 }}>A</span>
                </Button>
                <Button onClick={() => message.info("打开参数编辑(demo 未实现)")}>修改参数后批准</Button>
                <Button
                  danger
                  onClick={() => {
                    setResolution("rejected");
                    message.warning("已拒绝。Run 已取消(demo 模拟)");
                  }}
                >
                  拒绝 <span className="hx-kbd" style={{ marginLeft: 4 }}>R</span>
                </Button>
              </Space>
            </div>
          }
          style={{ marginBottom: 16 }}
        />
      )}

      {effectiveStatus === "ok" && resolution === "approved" && (
        <Alert
          type="success"
          showIcon
          message="审批通过 — Run 已恢复执行,处理 ticket TK-2026-0532(demo 模拟)"
          style={{ marginBottom: 16 }}
        />
      )}
      {effectiveStatus === "cancelled" && resolution === "rejected" && (
        <Alert
          type="error"
          showIcon
          message="审批拒绝 — Run 已取消(demo 模拟)"
          style={{ marginBottom: 16 }}
        />
      )}

      <Row gutter={16}>
        <Col span={9}>
          <Card title={<span style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--hx-text-tertiary)" }}>Spans · 1.84s · {baseRun.stepCount} steps</span>} size="small">
            <div style={{ fontFamily: "var(--hx-font-mono)", fontSize: 11 }}>
              {baseRun.spans.map((s, i) => <SpanRow key={i} span={s} />)}
            </div>
          </Card>
        </Col>
        <Col span={15}>
          <Card title="Trace timeline" size="small">
            <div style={{ display: "flex", flexDirection: "column", gap: 4, fontFamily: "var(--hx-font-mono)", fontSize: 11 }}>
              {baseRun.spans.filter((s) => s.depth === 0 || s.name.includes("tool") || s.name.includes("llm")).map((s, i) => (
                <div key={i} style={{ display: "grid", gridTemplateColumns: "1fr 60px", gap: 8, alignItems: "center" }}>
                  <div>
                    <div style={{ marginBottom: 2 }}>{s.name}</div>
                    <div style={{ position: "relative", height: 6, background: "var(--hx-surface-raised)", borderRadius: 2 }}>
                      <div
                        style={{
                          position: "absolute",
                          left: `${s.startOffsetPct}%`,
                          width: `${Math.max(s.widthPct, 2)}%`,
                          height: "100%",
                          background:
                            s.status === "slow"
                              ? "var(--hx-color-warning-500)"
                              : s.status === "err" || s.status === "pending"
                                ? "var(--hx-color-danger-500)"
                                : "var(--hx-color-brand-500)",
                          opacity: s.status === "pending" ? 0.5 : 1,
                          borderRadius: 2,
                        }}
                      />
                    </div>
                  </div>
                  <span style={{ textAlign: "right", color: "var(--hx-text-tertiary)" }}>
                    {s.durationMs ? `${s.durationMs}ms` : "…"}
                  </span>
                </div>
              ))}
            </div>
          </Card>

          <Card size="small" style={{ marginTop: 16 }}>
            <Tabs
              items={[
                {
                  key: "io",
                  label: "Input / Output",
                  children: (
                    <pre
                      style={{
                        margin: 0,
                        padding: 12,
                        background: "var(--hx-color-neutral-950)",
                        border: "1px solid var(--hx-border-subtle)",
                        borderRadius: 6,
                        fontFamily: "var(--hx-font-mono)",
                        fontSize: 11,
                        lineHeight: 1.5,
                        color: "var(--hx-text-secondary)",
                        overflow: "auto",
                      }}
                    >
{`"input": ${JSON.stringify(baseRun.input, null, 2)},
"output": ${JSON.stringify(baseRun.output, null, 2)}`}
                    </pre>
                  ),
                },
                { key: "logs", label: "Logs", children: <div style={{ color: "var(--hx-text-tertiary)", padding: 12 }}>(demo:logs tab 留空)</div> },
                { key: "tokens", label: "Tokens", children: <div style={{ color: "var(--hx-text-tertiary)", padding: 12 }}>(demo:tokens tab 留空)</div> },
              ]}
            />
          </Card>
        </Col>
      </Row>
    </div>
  );
}

function SpanRow({ span }: { span: MockSpan }) {
  const dotColor =
    span.status === "ok"
      ? "var(--hx-color-success-500)"
      : span.status === "slow"
        ? "var(--hx-color-warning-500)"
        : "var(--hx-color-danger-500)";
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        padding: "4px 8px",
        marginLeft: span.depth * 12,
        borderRadius: 2,
      }}
    >
      <span
        style={{
          width: 6,
          height: 6,
          borderRadius: 999,
          background: dotColor,
          flexShrink: 0,
          animation: span.status === "pending" ? "hx-pulse 1.5s infinite" : undefined,
        }}
      />
      <span style={{ flex: 1 }}>{span.name}</span>
      <span style={{ color: "var(--hx-text-tertiary)", fontSize: 10 }}>{span.durationMs ? `${span.durationMs}ms` : "…"}</span>
      <style>{`@keyframes hx-pulse { 50% { opacity: 0.4; } }`}</style>
    </div>
  );
}
