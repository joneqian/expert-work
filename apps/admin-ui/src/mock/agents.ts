export type AgentStatus = "active" | "draft" | "archived";

export interface MockAgent {
  id: string;
  name: string;
  description: string;
  status: AgentStatus;
  model: string;
  modelFallback?: string;
  temperature: number;
  maxSteps: number;
  version: string;
  monthlyRuns: number;
  failureRate: number; // 0..1
  p95LatencyMs: number;
  monthlyTokensM: number;
  monthlyCostUsd: number;
  updatedAt: string;
  updatedBy: string;
  createdAt: string;
  createdBy: string;
  skills: string[];
  triggersCount: { cron: number; webhook: number };
  memoryBackend: string;
  hitlApproval?: string;
}

export const mockAgents: MockAgent[] = [
  {
    id: "customer-support-bot",
    name: "customer-support-bot",
    description: "客服意图分流 + RAG 检索 + 工单转人工",
    status: "active",
    model: "claude-sonnet-4-6",
    modelFallback: "claude-haiku-4-5",
    temperature: 0.3,
    maxSteps: 12,
    version: "v3.4.2",
    monthlyRuns: 12847,
    failureRate: 0.008,
    p95LatencyMs: 1840,
    monthlyTokensM: 42.1,
    monthlyCostUsd: 168.4,
    updatedAt: "3h ago",
    updatedBy: "bob@acme-corp",
    createdAt: "2026-02-14",
    createdBy: "alice@acme-corp",
    skills: ["rag.knowledge", "ticket.create", "handover.human"],
    triggersCount: { cron: 0, webhook: 1 },
    memoryBackend: "vector + summary;TTL 90d",
    hitlApproval: "handover.human 触发时 → 工单经理审批",
  },
  {
    id: "sales-research-analyst",
    name: "sales-research-analyst",
    description: "销售线索调研 + 公司画像生成",
    status: "active",
    model: "claude-opus-4-7",
    temperature: 0.4,
    maxSteps: 20,
    version: "v1.8.0",
    monthlyRuns: 3402,
    failureRate: 0.024,
    p95LatencyMs: 4210,
    monthlyTokensM: 18.6,
    monthlyCostUsd: 372.8,
    updatedAt: "1d ago",
    updatedBy: "carol@acme-corp",
    createdAt: "2026-03-08",
    createdBy: "carol@acme-corp",
    skills: ["web.search", "company.profile", "linkedin.lookup"],
    triggersCount: { cron: 1, webhook: 0 },
    memoryBackend: "vector;TTL 180d",
  },
  {
    id: "data-pipeline-monitor",
    name: "data-pipeline-monitor",
    description: "每天 0 点扫数据管道异常并发钉钉告警",
    status: "active",
    model: "claude-haiku-4-5",
    temperature: 0.1,
    maxSteps: 5,
    version: "v2.1.0",
    monthlyRuns: 896,
    failureRate: 0,
    p95LatencyMs: 720,
    monthlyTokensM: 2.4,
    monthlyCostUsd: 9.8,
    updatedAt: "2d ago",
    updatedBy: "alice@acme-corp",
    createdAt: "2026-01-05",
    createdBy: "alice@acme-corp",
    skills: ["sql.query", "dingtalk.alert"],
    triggersCount: { cron: 1, webhook: 0 },
    memoryBackend: "none",
  },
  {
    id: "contract-redliner",
    name: "contract-redliner",
    description: "合同条款审阅 + 风险标注(legal RAG)",
    status: "draft",
    model: "claude-opus-4-7",
    temperature: 0.2,
    maxSteps: 12,
    version: "v0.3.0",
    monthlyRuns: 14,
    failureRate: 0.143,
    p95LatencyMs: 8600,
    monthlyTokensM: 0.8,
    monthlyCostUsd: 16.2,
    updatedAt: "5h ago",
    updatedBy: "dave@acme-corp",
    createdAt: "2026-05-10",
    createdBy: "dave@acme-corp",
    skills: ["rag.legal", "contract.annotate"],
    triggersCount: { cron: 0, webhook: 1 },
    memoryBackend: "vector;TTL 30d",
  },
  {
    id: "marketing-content-writer",
    name: "marketing-content-writer",
    description: "营销文案生成(品牌调性约束 + SEO 关键词)",
    status: "archived",
    model: "claude-sonnet-4-6",
    temperature: 0.7,
    maxSteps: 8,
    version: "v2.0.1",
    monthlyRuns: 0,
    failureRate: 0,
    p95LatencyMs: 0,
    monthlyTokensM: 0,
    monthlyCostUsd: 0,
    updatedAt: "14d ago",
    updatedBy: "eve@acme-corp",
    createdAt: "2026-02-20",
    createdBy: "eve@acme-corp",
    skills: ["seo.keywords", "brand.voice"],
    triggersCount: { cron: 0, webhook: 0 },
    memoryBackend: "none",
  },
];

export function findAgent(id: string): MockAgent | undefined {
  return mockAgents.find((a) => a.id === id);
}
