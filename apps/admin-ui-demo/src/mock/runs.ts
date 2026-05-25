export type RunStatus = "ok" | "failed" | "approval" | "running" | "cancelled";

export interface MockSpan {
  name: string;
  durationMs: number;
  status: "ok" | "slow" | "err" | "pending";
  startOffsetPct: number; // 0..100 (relative to total run)
  widthPct: number; // 0..100
  depth: number; // tree indent level
}

export interface MockRun {
  id: string;
  agentId: string;
  agentVersion: string;
  status: RunStatus;
  totalDurationMs: number;
  stepCount: number;
  triggeredBy: string;
  triggeredAt: string;
  threadId: string;
  userId: string;
  errorReason?: string;
  approvalReason?: string;
  approvalArgs?: Record<string, unknown>;
  input: Record<string, unknown>;
  output?: Record<string, unknown>;
  spans: MockSpan[];
}

export const mockRuns: MockRun[] = [
  {
    id: "run_4c9b8e21f60d",
    agentId: "customer-support-bot",
    agentVersion: "v3.4.2",
    status: "approval",
    totalDurationMs: 1840,
    stepCount: 8,
    triggeredBy: "webhook · trg_help_desk",
    triggeredAt: "12m ago",
    threadId: "th_5a3c8e21",
    userId: "u_88234",
    approvalReason:
      "Agent 在 step 7 触发了 handover.human skill,需要工单经理决策:是直接转人工还是先尝试自助理赔。",
    approvalArgs: {
      ticket_id: "TK-2026-0532",
      customer_id: "u_88234",
      reason: "logistics_dispute",
      urgency: "medium",
      suggested_handler: "logistics-team",
    },
    input: {
      thread_id: "th_5a3c8e21",
      user_id: "u_88234",
      message:
        "订单 #88234 的物流为什么显示已签收但客户说没收到?需要我帮他理赔吗?",
      channel: "webhook",
      trigger_id: "trg_help_desk",
    },
    output: {
      status: "awaiting_approval",
      pending_action: "handover.human",
      step: 7,
    },
    spans: [
      { name: "step.plan", durationMs: 142, status: "ok", startOffsetPct: 0, widthPct: 8, depth: 0 },
      { name: "llm.call (sonnet)", durationMs: 386, status: "ok", startOffsetPct: 1, widthPct: 21, depth: 1 },
      { name: "step.act", durationMs: 312, status: "ok", startOffsetPct: 22, widthPct: 17, depth: 0 },
      { name: "tool.rag.knowledge", durationMs: 312, status: "ok", startOffsetPct: 22, widthPct: 17, depth: 1 },
      { name: "step.act", durationMs: 186, status: "ok", startOffsetPct: 39, widthPct: 10, depth: 0 },
      { name: "tool.logistics.lookup", durationMs: 186, status: "ok", startOffsetPct: 39, widthPct: 10, depth: 1 },
      { name: "step.reflect", durationMs: 624, status: "slow", startOffsetPct: 49, widthPct: 34, depth: 0 },
      { name: "llm.call (sonnet)", durationMs: 624, status: "slow", startOffsetPct: 49, widthPct: 34, depth: 1 },
      { name: "step.handover (await approval)", durationMs: 0, status: "pending", startOffsetPct: 83, widthPct: 4, depth: 0 },
      { name: "tool.handover.human", durationMs: 0, status: "pending", startOffsetPct: 83, widthPct: 4, depth: 1 },
    ],
  },
];

export function findRun(id: string): MockRun | undefined {
  return mockRuns.find((r) => r.id === id);
}
