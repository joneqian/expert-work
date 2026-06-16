export interface ModelFields {
  provider?: string;
  name?: string;
  supports_vision?: boolean;
  temperature?: number;
  max_tokens?: number;
  rate_limit_rpm?: number;
  [k: string]: unknown;
}
export interface LongTermFields {
  retrieve_top_k?: number;
  write_back?: boolean;
  recall_mode?: string;
}
export interface RouteRuleFields {
  when?: string;
  model?: ModelFields;
  [k: string]: unknown;
}
export interface RoutingFields {
  rules?: RouteRuleFields[];
  [k: string]: unknown;
}
export type ToolEntry = {
  type: string;
  name?: string;
  allow_tools?: string[];
  servers?: string[];
  config?: Record<string, unknown>;
  [k: string]: unknown;
};
export interface AgentManifest {
  apiVersion?: string;
  kind?: string;
  metadata?: { name?: string; version?: string; tenant?: string; [k: string]: unknown };
  spec?: {
    description?: string;
    model?: ModelFields;
    system_prompt?: { template?: string; [k: string]: unknown };
    memory?: { long_term?: LongTermFields | null; [k: string]: unknown } | null;
    tools?: ToolEntry[];
    routing?: RoutingFields | null;
    [k: string]: unknown;
  };
  [k: string]: unknown;
}

function asObj(v: unknown): AgentManifest {
  return v !== null && typeof v === "object" && !Array.isArray(v) ? (v as AgentManifest) : {};
}
function specOf(m: unknown): NonNullable<AgentManifest["spec"]> {
  return asObj(m).spec ?? {};
}
function patchSpec(m: unknown, spec: Record<string, unknown>): AgentManifest {
  const base = asObj(m);
  return { ...base, spec: { ...specOf(base), ...spec } };
}

// ---- readers ----
export const readName = (m: unknown): string => asObj(m).metadata?.name ?? "";
export const readDescription = (m: unknown): string => specOf(m).description ?? "";
export const readModel = (m: unknown): ModelFields => specOf(m).model ?? {};
export const readSystemPrompt = (m: unknown): string => specOf(m).system_prompt?.template ?? "";
export const readMemoryOn = (m: unknown): boolean => (specOf(m).memory?.long_term ?? null) !== null;
export const readTopK = (m: unknown): number | undefined =>
  specOf(m).memory?.long_term?.retrieve_top_k;

// ---- reflection evaluator (Stream J.11 routing — the `when=reflection` rule) ----
// The "reflection evaluator model" friendly control is a curated view over the
// existing ``routing`` block: an independent evaluator is just a route rule that
// sends the reflection step to its own model. Empty = no rule = reflection reuses
// the agent's own model (the safe default).
export const readReflectionEvaluator = (m: unknown): ModelFields | undefined =>
  (specOf(m).routing?.rules ?? []).find((r) => r.when === "reflection")?.model;

export const readReflectionEvaluatorOn = (m: unknown): boolean =>
  readReflectionEvaluator(m) !== undefined;

export interface ToolFlags {
  webSearch: boolean;
  http: boolean;
  mcp: boolean;
  mcpAllowTools: string[];
  mcpServers: string[];
}
export function readTools(m: unknown): ToolFlags {
  const tools = specOf(m).tools ?? [];
  const mcp = tools.find((t) => t.type === "mcp");
  return {
    webSearch: tools.some((t) => t.type === "builtin" && t.name === "web_search"),
    http: tools.some((t) => t.type === "http"),
    mcp: mcp !== undefined,
    mcpAllowTools: mcp?.allow_tools ?? [],
    mcpServers: mcp?.servers ?? [],
  };
}

// ---- writers (immutable; preserve siblings) ----
export function setName(m: unknown, name: string): AgentManifest {
  const base = asObj(m);
  return { ...base, metadata: { ...(base.metadata ?? {}), name } };
}
export const setDescription = (m: unknown, description: string): AgentManifest =>
  patchSpec(m, { description });
export function setModel(m: unknown, model: ModelFields): AgentManifest {
  return patchSpec(m, { model: { ...readModel(m), ...model } });
}
export function setSystemPrompt(m: unknown, template: string): AgentManifest {
  return patchSpec(m, { system_prompt: { ...(specOf(m).system_prompt ?? {}), template } });
}
export function setMemoryOn(m: unknown, on: boolean): AgentManifest {
  const memory = specOf(m).memory ?? {};
  if (!on) return patchSpec(m, { memory: { ...memory, long_term: null } });
  const existing = specOf(m).memory?.long_term ?? null;
  const lt: LongTermFields = existing ?? {
    retrieve_top_k: 5,
    write_back: true,
    recall_mode: "per_session",
  };
  return patchSpec(m, { memory: { ...memory, long_term: lt } });
}
export function setTopK(m: unknown, k: number): AgentManifest {
  const memory = specOf(m).memory ?? {};
  const lt = specOf(m).memory?.long_term ?? { write_back: true, recall_mode: "per_session" };
  return patchSpec(m, { memory: { ...memory, long_term: { ...lt, retrieve_top_k: k } } });
}
export function setReflectionEvaluator(m: unknown, model: ModelFields | null): AgentManifest {
  const routing = specOf(m).routing ?? {};
  // Preserve any other route rules (e.g. a planning rule); only touch reflection.
  const others = (routing.rules ?? []).filter((r) => r.when !== "reflection");
  const keep = model !== null && (model.provider !== undefined || model.name !== undefined);
  const rules = keep ? [...others, { when: "reflection", model }] : others;
  if (rules.length === 0) {
    // Drop ``rules`` entirely; if routing then has no other keys, drop routing
    // so the manifest stays clean (js-yaml omits ``undefined``).
    const { rules: _dropped, ...rest } = routing;
    return patchSpec(m, { routing: Object.keys(rest).length > 0 ? rest : undefined });
  }
  return patchSpec(m, { routing: { ...routing, rules } });
}

export function setTool(m: unknown, kind: "webSearch" | "http" | "mcp", on: boolean): AgentManifest {
  const tools = specOf(m).tools ?? [];
  const without = (pred: (t: ToolEntry) => boolean): ToolEntry[] => tools.filter((t) => !pred(t));
  if (kind === "webSearch") {
    const isWeb = (t: ToolEntry): boolean => t.type === "builtin" && t.name === "web_search";
    return patchSpec(m, {
      tools: on
        ? [...without(isWeb), { type: "builtin", name: "web_search", config: {} }]
        : without(isWeb),
    });
  }
  if (kind === "http") {
    const isHttp = (t: ToolEntry): boolean => t.type === "http";
    return patchSpec(m, { tools: on ? [...without(isHttp), { type: "http" }] : without(isHttp) });
  }
  const isMcp = (t: ToolEntry): boolean => t.type === "mcp";
  return patchSpec(m, {
    tools: on ? [...without(isMcp), { type: "mcp", allow_tools: [] }] : without(isMcp),
  });
}
export function setMcpAllowTools(m: unknown, allow: string[]): AgentManifest {
  const tools = (specOf(m).tools ?? []).map((t) =>
    t.type === "mcp" ? { ...t, allow_tools: allow } : t,
  );
  return patchSpec(m, { tools });
}
export function setMcpServers(m: unknown, servers: string[]): AgentManifest {
  const tools = (specOf(m).tools ?? []).map((t) =>
    t.type === "mcp" ? { ...t, servers } : t,
  );
  return patchSpec(m, { tools });
}
