export type ApiKeyStatus = "active" | "grace" | "revoked";

export interface MockApiKey {
  id: string;
  name: string;
  prefix: string;
  scopes: string[];
  serviceAccount: string;
  status: ApiKeyStatus;
  graceUntil?: string;
  lastUsed: string;
  expiresAt: string;
}

export const ALL_SCOPES = [
  { value: "agents:read", label: "agents:read", danger: false },
  { value: "agents:write", label: "agents:write", danger: false },
  { value: "runs:read", label: "runs:read", danger: false },
  { value: "runs:write", label: "runs:write", danger: false },
  { value: "memory:read", label: "memory:read", danger: false },
  { value: "memory:write", label: "memory:write", danger: false },
  { value: "triggers:read", label: "triggers:read", danger: false },
  { value: "triggers:write", label: "triggers:write", danger: false },
  { value: "admin:*", label: "admin:*", danger: true },
] as const;

export const mockApiKeys: MockApiKey[] = [
  {
    id: "key_1",
    name: "prod-ingest",
    prefix: "helix_pat_4f1a8e21",
    scopes: ["agents:read", "runs:write"],
    serviceAccount: "prod-ingest-svc",
    status: "active",
    lastUsed: "2m ago",
    expiresAt: "2027-02-14",
  },
  {
    id: "key_2",
    name: "prod-ingest (rotation old)",
    prefix: "helix_pat_2c0f9a18",
    scopes: ["agents:read", "runs:write"],
    serviceAccount: "prod-ingest-svc",
    status: "grace",
    graceUntil: "41m",
    lastUsed: "8m ago",
    expiresAt: "2026-05-25 16:42",
  },
  {
    id: "key_3",
    name: "analyst-readonly",
    prefix: "helix_pat_8e3a1b29",
    scopes: ["agents:read", "runs:read"],
    serviceAccount: "analyst-svc",
    status: "active",
    lastUsed: "1h ago",
    expiresAt: "2026-12-01",
  },
  {
    id: "key_4",
    name: "ci-webhook",
    prefix: "helix_pat_77b3e210",
    scopes: ["triggers:write"],
    serviceAccount: "ci-bot-svc",
    status: "revoked",
    lastUsed: "14d ago",
    expiresAt: "—",
  },
];

export function generateMockKey(prefix = "helix_pat"): { full: string; prefix: string } {
  // demo only — 安全场景下应由后端生成
  const random = Array.from({ length: 24 }, () => Math.floor(Math.random() * 36).toString(36)).join("");
  const head = `${random.slice(0, 8)}`;
  const tail = random.slice(8);
  return { full: `${prefix}_${head}${tail}`, prefix: `${prefix}_${head}` };
}
