import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  Breadcrumb,
  Button,
  Card,
  Col,
  Empty,
  Input,
  Row,
  Space,
  Statistic,
  Tabs,
  Tag,
  Tooltip,
} from "antd";
import {
  Bot,
  Sparkles,
  Edit,
  MoreHorizontal,
  ChevronRight,
  ArrowUp,
  ArrowDown,
  Play,
  StopCircle,
  Wrench,
  Clock,
} from "lucide-react";
import { findAgent } from "../mock/agents";

export function AgentDetail() {
  const { agentId, tab } = useParams<{ agentId: string; tab?: string }>();
  const nav = useNavigate();
  const agent = agentId ? findAgent(agentId) : undefined;

  const activeTab = tab ?? "overview";

  if (!agent) {
    return <Empty description={`Agent "${agentId}" 不存在`} style={{ marginTop: 80 }} />;
  }

  return (
    <div>
      <Breadcrumb
        items={[
          { title: "acme-corp" },
          { title: <Link to="/agents">Agents</Link> },
          { title: agent.name },
        ]}
        style={{ marginBottom: 8, fontSize: 13 }}
        separator={<ChevronRight size={12} strokeWidth={1.5} style={{ verticalAlign: "middle" }} />}
      />

      {/* Hero */}
      <div style={{ display: "flex", alignItems: "flex-start", gap: 16, paddingBottom: 16 }}>
        <div
          style={{
            width: 40,
            height: 40,
            borderRadius: 6,
            background: "var(--hx-surface-selected)",
            color: "var(--hx-color-brand-500)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            flexShrink: 0,
          }}
        >
          <Bot size={20} strokeWidth={1.5} />
        </div>
        <div style={{ flex: 1 }}>
          <h1 style={{ margin: 0, fontSize: 24, fontWeight: 600, lineHeight: 1.2 }}>{agent.name}</h1>
          <Space size={12} style={{ marginTop: 6, fontSize: 13, color: "var(--hx-text-secondary)" }}>
            <Tag color="success" bordered={false} style={{ borderRadius: 2 }}>
              <span style={{ display: "inline-block", width: 6, height: 6, borderRadius: 999, background: "currentColor", marginRight: 4 }} />
              {agent.status}
            </Tag>
            <span className="mono">{agent.version}</span>
            <span style={{ color: "var(--hx-text-tertiary)" }}>·</span>
            <span>{agent.description}</span>
          </Space>
        </div>
        <Space>
          <Button
            icon={<Sparkles size={14} strokeWidth={1.5} />}
            onClick={() => nav(`/agents/${agent.id}/playground`)}
          >
            Playground
            <span className="hx-kbd" style={{ marginLeft: 4 }}>P</span>
          </Button>
          <Button icon={<Edit size={14} strokeWidth={1.5} />}>编辑 Manifest</Button>
          <Tooltip title="更多操作">
            <Button icon={<MoreHorizontal size={14} strokeWidth={1.5} />} />
          </Tooltip>
        </Space>
      </div>

      <Tabs
        activeKey={activeTab}
        onChange={(k) => nav(`/agents/${agent.id}/${k}`)}
        items={[
          { key: "overview", label: "Overview" },
          { key: "manifest", label: "Manifest" },
          { key: "playground", label: <span>Playground</span> },
          { key: "runs", label: "Runs" },
          { key: "skills", label: "Skills" },
          { key: "triggers", label: "Triggers" },
          { key: "memory", label: "Memory" },
        ]}
      />

      {activeTab === "overview" && <OverviewTab agent={agent} />}
      {activeTab === "playground" && <PlaygroundTab agentName={agent.name} version={agent.version} />}
      {activeTab !== "overview" && activeTab !== "playground" && (
        <Empty description={`此 tab(${activeTab})未在 demo 范围内;H.1b 实施`} style={{ marginTop: 64 }} />
      )}
    </div>
  );
}

// ===================== Overview tab =====================
function OverviewTab({ agent }: { agent: ReturnType<typeof findAgent> extends infer T ? Exclude<T, undefined> : never }) {
  return (
    <div>
      <Row gutter={16} style={{ marginBottom: 24 }}>
        <Col span={6}>
          <Card>
            <Statistic
              title={<span style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.05em" }}>本月 Runs</span>}
              value={agent.monthlyRuns}
              valueStyle={{ fontFamily: "var(--hx-font-mono)", fontSize: 24, fontWeight: 600 }}
            />
            <div style={{ fontSize: 11, color: "var(--hx-color-success-500)", marginTop: 4 }}>
              <ArrowUp size={10} strokeWidth={1.5} style={{ verticalAlign: "middle" }} /> 18.4% vs 上月
            </div>
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title={<span style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.05em" }}>失败率</span>}
              value={(agent.failureRate * 100).toFixed(1)}
              suffix="%"
              valueStyle={{ fontFamily: "var(--hx-font-mono)", fontSize: 24, fontWeight: 600 }}
            />
            <div style={{ fontSize: 11, color: "var(--hx-color-warning-500)", marginTop: 4 }}>
              <ArrowUp size={10} strokeWidth={1.5} style={{ verticalAlign: "middle" }} /> 0.3pp vs 上月 (恶化)
            </div>
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title={<span style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.05em" }}>P95 延迟</span>}
              value={(agent.p95LatencyMs / 1000).toFixed(2)}
              suffix="s"
              valueStyle={{ fontFamily: "var(--hx-font-mono)", fontSize: 24, fontWeight: 600 }}
            />
            <div style={{ fontSize: 11, color: "var(--hx-color-success-500)", marginTop: 4 }}>
              <ArrowDown size={10} strokeWidth={1.5} style={{ verticalAlign: "middle" }} /> 0.21s vs 上月 (改善)
            </div>
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title={<span style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.05em" }}>Token 用量</span>}
              value={agent.monthlyTokensM.toFixed(1)}
              suffix="M"
              valueStyle={{ fontFamily: "var(--hx-font-mono)", fontSize: 24, fontWeight: 600 }}
            />
            <div style={{ fontSize: 11, color: "var(--hx-text-tertiary)", marginTop: 4 }}>
              $ {agent.monthlyCostUsd.toFixed(1)} / 月
            </div>
          </Card>
        </Col>
      </Row>

      <Row gutter={16}>
        <Col span={16}>
          <Card
            title="配置摘要"
            extra={<Button type="link" size="small">查看完整 Manifest →</Button>}
          >
            <dl style={{ display: "grid", gridTemplateColumns: "140px 1fr", rowGap: 8, columnGap: 16, margin: 0, fontSize: 13 }}>
              <dt style={{ color: "var(--hx-text-tertiary)" }}>主模型</dt>
              <dd style={{ margin: 0 }}>
                <span className="mono">{agent.model}</span>
                <Tag color="processing" bordered={false} style={{ marginLeft: 8 }}>primary</Tag>
              </dd>
              {agent.modelFallback && (
                <>
                  <dt style={{ color: "var(--hx-text-tertiary)" }}>fallback</dt>
                  <dd style={{ margin: 0 }} className="mono">{agent.modelFallback}</dd>
                </>
              )}
              <dt style={{ color: "var(--hx-text-tertiary)" }}>温度</dt>
              <dd style={{ margin: 0 }} className="mono">{agent.temperature}</dd>
              <dt style={{ color: "var(--hx-text-tertiary)" }}>最大 steps</dt>
              <dd style={{ margin: 0 }} className="mono">{agent.maxSteps}</dd>
              <dt style={{ color: "var(--hx-text-tertiary)" }}>挂载 skills</dt>
              <dd style={{ margin: 0 }}>
                {agent.skills.map((s) => (
                  <Tag key={s} bordered={false} style={{ marginBottom: 4 }}>{s}</Tag>
                ))}
              </dd>
              {(agent.triggersCount.cron > 0 || agent.triggersCount.webhook > 0) && (
                <>
                  <dt style={{ color: "var(--hx-text-tertiary)" }}>触发器</dt>
                  <dd style={{ margin: 0 }}>
                    {agent.triggersCount.cron > 0 && <Tag color="processing" bordered={false}>cron × {agent.triggersCount.cron}</Tag>}
                    {agent.triggersCount.webhook > 0 && <Tag color="processing" bordered={false}>webhook × {agent.triggersCount.webhook}</Tag>}
                  </dd>
                </>
              )}
              <dt style={{ color: "var(--hx-text-tertiary)" }}>Memory backend</dt>
              <dd style={{ margin: 0 }}>{agent.memoryBackend}</dd>
              {agent.hitlApproval && (
                <>
                  <dt style={{ color: "var(--hx-text-tertiary)" }}>HITL 审批</dt>
                  <dd style={{ margin: 0 }}>{agent.hitlApproval}</dd>
                </>
              )}
              <dt style={{ color: "var(--hx-text-tertiary)" }}>创建于</dt>
              <dd style={{ margin: 0 }}>{agent.createdAt} by {agent.createdBy}</dd>
              <dt style={{ color: "var(--hx-text-tertiary)" }}>最后修改</dt>
              <dd style={{ margin: 0 }}>
                {agent.updatedAt} by {agent.updatedBy} <span style={{ color: "var(--hx-text-tertiary)" }}>({agent.version})</span>
              </dd>
            </dl>
          </Card>
        </Col>
        <Col span={8}>
          <Card title="最近 Runs" extra={<Button type="link" size="small">查看全部 →</Button>}>
            {RECENT_RUNS.map((r) => (
              <div
                key={r.id}
                style={{
                  display: "flex",
                  gap: 12,
                  alignItems: "center",
                  padding: "8px 0",
                  borderBottom: "1px solid var(--hx-border-subtle)",
                  fontSize: 13,
                }}
              >
                <Tag color={r.color} bordered={false} style={{ borderRadius: 2, minWidth: 64, textAlign: "center" }}>
                  {r.status}
                </Tag>
                <span className="mono" style={{ color: "var(--hx-text-tertiary)" }}>{r.id}</span>
                <span style={{ color: "var(--hx-text-secondary)" }}>{r.note}</span>
                <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--hx-text-tertiary)" }}>{r.time}</span>
              </div>
            ))}
          </Card>
        </Col>
      </Row>
    </div>
  );
}

const RECENT_RUNS = [
  { id: "run_8a2c…", status: "ok", color: "success", note: "1.62s · 8 steps", time: "2m ago" },
  { id: "run_7f1d…", status: "ok", color: "success", note: "1.34s · 5 steps", time: "8m ago" },
  { id: "run_4c9b…", status: "approval", color: "warning", note: "handover.human 待审批", time: "12m ago" },
  { id: "run_2e0a…", status: "failed", color: "error", note: "rag.knowledge timeout", time: "23m ago" },
  { id: "run_91b3…", status: "ok", color: "success", note: "2.08s · 11 steps", time: "31m ago" },
];

// ===================== Playground tab =====================
const FULL_RESPONSE = `好的,我查了一下订单 #88234 的物流详情和理赔流程,情况是这样:

物流方:已在 5 月 20 日 14:32 由"前台-王经理"代签收,有签收照片。
常见原因:包裹被前台 / 物业 / 同事代收后未及时转交;少数情况下确实是错签。

建议先按这个顺序处理:
1. 让客户联系收件地址的前台或同事确认是否代收
2. 如确认未送达,我可以帮你发起理赔工单 — 需要客户提供:订单号、收件人姓名、未收到的具体物品清单
3. 同时通知物流方核查签收人身份,要求提供签收凭证补充资料

需要我现在帮你起一张理赔工单吗?`;

function PlaygroundTab({ agentName, version }: { agentName: string; version: string }) {
  const [prompt, setPrompt] = useState("订单 #88234 的物流为什么显示已签收但客户说没收到?需要我帮他理赔吗?");
  const [streaming, setStreaming] = useState(false);
  const [streamed, setStreamed] = useState("");
  const [hasResult, setHasResult] = useState(false);
  const cancelRef = useRef<{ cancelled: boolean }>({ cancelled: false });
  const messagesRef = useRef<HTMLDivElement>(null);

  const runPlayground = () => {
    cancelRef.current.cancelled = false;
    setStreaming(true);
    setHasResult(true);
    setStreamed("");

    let i = 0;
    const tick = () => {
      if (cancelRef.current.cancelled) return;
      if (i >= FULL_RESPONSE.length) {
        setStreaming(false);
        return;
      }
      i += Math.max(1, Math.floor(Math.random() * 4));
      setStreamed(FULL_RESPONSE.slice(0, i));
      setTimeout(tick, 30);
    };
    tick();
  };

  const stop = () => {
    cancelRef.current.cancelled = true;
    setStreaming(false);
  };

  useEffect(() => {
    messagesRef.current?.scrollTo({ top: messagesRef.current.scrollHeight, behavior: "smooth" });
  }, [streamed]);

  return (
    <div style={{ display: "grid", gridTemplateColumns: "340px 1fr 320px", gap: 12, height: "calc(100vh - 280px)", minHeight: 480 }}>
      {/* 左:input + manifest */}
      <Card
        size="small"
        title="Input"
        styles={{ body: { padding: 12, display: "flex", flexDirection: "column", gap: 12, height: "100%" } }}
      >
        <Input.TextArea
          rows={5}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="输入 prompt 测试 agent ..."
          style={{ resize: "none" }}
        />
        <Button
          type="primary"
          block
          icon={streaming ? <StopCircle size={14} strokeWidth={1.5} /> : <Play size={14} strokeWidth={1.5} />}
          onClick={streaming ? stop : runPlayground}
          danger={streaming}
        >
          {streaming ? "停止" : "运行"}
          <span className="hx-kbd" style={{ marginLeft: 6 }}>{streaming ? "Esc" : "⌘↵"}</span>
        </Button>
        <div style={{ fontSize: 11, fontWeight: 500, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--hx-text-tertiary)", marginTop: 4 }}>
          Manifest(可改可重跑)
        </div>
        <pre
          style={{
            flex: 1,
            margin: 0,
            padding: 12,
            background: "var(--hx-color-neutral-950)",
            border: "1px solid var(--hx-border-subtle)",
            borderRadius: 6,
            fontFamily: "var(--hx-font-mono)",
            fontSize: 11,
            color: "var(--hx-text-secondary)",
            lineHeight: 1.5,
            overflow: "auto",
          }}
        >
{`name: ${agentName}
version: ${version}
model:
  primary: claude-sonnet-4-6
  fallback: claude-haiku-4-5
  temperature: 0.3
max_steps: 12
skills:
  - rag.knowledge
  - ticket.create
  - handover.human
memory:
  backend: vector+summary
  ttl_days: 90`}
        </pre>
      </Card>

      {/* 中:messages */}
      <Card
        size="small"
        title={
          <Space>
            <span>对话流</span>
            {streaming && <Tag color="processing" bordered={false}><span style={{ display: "inline-block", width: 6, height: 6, borderRadius: 999, background: "currentColor", marginRight: 4 }} />streaming</Tag>}
          </Space>
        }
        styles={{ body: { padding: 0, display: "flex", flexDirection: "column", height: "100%" } }}
      >
        <div
          ref={messagesRef}
          style={{ flex: 1, overflowY: "auto", padding: 16, display: "flex", flexDirection: "column", gap: 16 }}
          aria-live="polite"
        >
          {!hasResult && (
            <div style={{ textAlign: "center", color: "var(--hx-text-tertiary)", padding: "32px 16px" }}>
              <Sparkles size={24} strokeWidth={1.5} style={{ opacity: 0.4 }} />
              <div style={{ marginTop: 8, fontSize: 13 }}>左侧填 prompt → "运行" 起 debug 会话</div>
              <div style={{ fontSize: 11, marginTop: 4 }}>SSE 流式回复 · tool calls · trace timeline 都会实时出现</div>
            </div>
          )}
          {hasResult && (
            <>
              {/* User message */}
              <div style={{ display: "flex", gap: 8, alignSelf: "flex-end", flexDirection: "row-reverse", maxWidth: "85%" }}>
                <div
                  style={{
                    width: 28, height: 28, borderRadius: 999,
                    background: "var(--hx-color-accent-500)", color: "white",
                    display: "flex", alignItems: "center", justifyContent: "center",
                    fontWeight: 600, fontSize: 11, flexShrink: 0,
                  }}
                >L</div>
                <div
                  style={{
                    background: "var(--hx-color-accent-500)", color: "white",
                    padding: "10px 14px", borderRadius: 8, fontSize: 13,
                  }}
                >{prompt}</div>
              </div>

              {/* Tool call cards */}
              <ToolCallCard name="rag.knowledge" duration="312ms" extra="4 docs" args={`query: "订单 已签收 客户未收到 理赔流程"\ntop_k: 5\nfilter: { collection: "logistics-policy" }`} />
              <ToolCallCard name="logistics.lookup" duration="186ms" args={`order_id: "88234"\nstatus: "delivered"\ndelivered_at: "2026-05-20T14:32:00Z"\nsigned_by: "前台-王经理"\nphoto_url: "https://…/proof.jpg"`} />

              {/* Assistant message */}
              <div style={{ display: "flex", gap: 8, maxWidth: "85%" }}>
                <div
                  style={{
                    width: 28, height: 28, borderRadius: 999,
                    background: "var(--hx-surface-selected)", color: "var(--hx-color-brand-400)",
                    display: "flex", alignItems: "center", justifyContent: "center",
                    fontWeight: 600, fontSize: 11, flexShrink: 0,
                  }}
                >A</div>
                <div
                  style={{
                    background: "var(--hx-surface-base)", border: "1px solid var(--hx-border-subtle)",
                    padding: "10px 14px", borderRadius: 8, fontSize: 13, lineHeight: 1.6,
                    whiteSpace: "pre-wrap",
                  }}
                >
                  {streamed}
                  {streaming && (
                    <span
                      style={{
                        display: "inline-block", width: 6, height: 14,
                        background: "var(--hx-color-brand-500)", marginLeft: 2, verticalAlign: "-2px",
                        animation: "hx-blink 1s steps(2) infinite",
                      }}
                    />
                  )}
                </div>
              </div>
              <style>{`@keyframes hx-blink { 50% { opacity: 0; } } @media (prefers-reduced-motion: reduce) { [style*="hx-blink"] { animation: none !important; opacity: 0.7; } }`}</style>
            </>
          )}
        </div>
      </Card>

      {/* 右:trace + step inspector */}
      <Card size="small" title="Trace" styles={{ body: { padding: 12 } }}>
        {!hasResult && (
          <div style={{ color: "var(--hx-text-tertiary)", fontSize: 12, textAlign: "center", padding: "32px 8px" }}>
            <Clock size={20} strokeWidth={1.5} style={{ opacity: 0.4 }} />
            <div style={{ marginTop: 4 }}>spans 将在运行后出现</div>
          </div>
        )}
        {hasResult && (
          <>
            <div style={{ fontSize: 11, fontWeight: 500, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--hx-text-tertiary)", marginBottom: 8 }}>
              step 3 / N · 1.42s
            </div>
            <TraceBars />

            <div style={{ marginTop: 16, fontSize: 11, fontWeight: 500, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--hx-text-tertiary)", marginBottom: 8 }}>
              Step 4 detail
            </div>
            <pre
              style={{
                margin: 0, padding: 12,
                background: "var(--hx-color-neutral-950)",
                border: "1px solid var(--hx-border-subtle)",
                borderRadius: 6,
                fontFamily: "var(--hx-font-mono)",
                fontSize: 11,
                color: "var(--hx-text-secondary)",
                lineHeight: 1.5,
                overflow: "auto",
              }}
            >{`{
  "step": 4,
  "node": "llm.call",
  "model": "claude-sonnet-4-6",
  "prompt_tokens": 1842,
  "completion_tokens": 96,
  "cached_tokens": 1640,
  "stop_reason": "${streaming ? "streaming" : "stop"}"
}`}</pre>
          </>
        )}
      </Card>
    </div>
  );
}

function ToolCallCard({ name, duration, extra, args }: { name: string; duration: string; extra?: string; args: string }) {
  return (
    <div
      style={{
        marginLeft: 36,
        background: "var(--hx-surface-raised)",
        border: "1px solid var(--hx-border-subtle)",
        borderRadius: 6,
        padding: "8px 12px",
        fontFamily: "var(--hx-font-mono)",
        fontSize: 11,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--hx-text-secondary)" }}>
        <Wrench size={11} strokeWidth={1.5} />
        <span style={{ color: "var(--hx-color-accent-400)", fontWeight: 500 }}>{name}</span>
        <span style={{ marginLeft: "auto", color: "var(--hx-text-tertiary)" }}>
          {duration}
          {extra && ` · ${extra}`}
        </span>
      </div>
      <pre
        style={{
          margin: "8px 0 0",
          padding: 8,
          background: "var(--hx-color-neutral-950)",
          borderRadius: 4,
          color: "var(--hx-text-tertiary)",
          fontSize: 11,
          lineHeight: 1.4,
          overflow: "auto",
        }}
      >{args}</pre>
    </div>
  );
}

function TraceBars() {
  const bars = [
    { name: "step.plan", left: 0, width: 8, status: "ok", duration: "142ms" },
    { name: "llm.call (sonnet)", left: 1, width: 21, status: "ok", duration: "386ms" },
    { name: "tool.rag.knowledge", left: 22, width: 17, status: "ok", duration: "312ms" },
    { name: "tool.logistics.lookup", left: 39, width: 10, status: "ok", duration: "186ms" },
    { name: "llm.call (sonnet stream)", left: 49, width: 34, status: "slow", duration: "stream" },
  ] as const;
  const color = (s: string) =>
    s === "slow" ? "var(--hx-color-warning-500)" : s === "err" ? "var(--hx-color-danger-500)" : "var(--hx-color-brand-500)";
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2, fontFamily: "var(--hx-font-mono)", fontSize: 10 }}>
      {bars.map((b) => (
        <div key={b.name} style={{ display: "grid", gridTemplateColumns: "1fr 50px", gap: 8, alignItems: "center", padding: "2px 4px" }}>
          <div>
            <div style={{ marginBottom: 2 }}>{b.name}</div>
            <div style={{ position: "relative", height: 6, background: "var(--hx-surface-raised)", borderRadius: 2 }}>
              <div
                style={{
                  position: "absolute",
                  left: `${b.left}%`,
                  width: `${b.width}%`,
                  height: "100%",
                  background: color(b.status),
                  borderRadius: 2,
                }}
              />
            </div>
          </div>
          <span style={{ textAlign: "right", color: "var(--hx-text-tertiary)" }}>{b.duration}</span>
        </div>
      ))}
    </div>
  );
}
