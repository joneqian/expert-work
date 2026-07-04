export interface ModelFields {
  provider?: string;
  name?: string;
  supports_vision?: boolean;
  temperature?: number;
  max_tokens?: number;
  rate_limit_rpm?: number;
  // Declared context window in tokens; drives the compaction threshold
  // (context_window * threshold_pct). Undefined = resolve from the model
  // catalog, falling back to 200k (agent_factory._resolved_context_window).
  context_window?: number;
  // Thinking-Toggle — tri-state on/off (undefined = inherit vendor default).
  thinking_enabled?: boolean;
  [k: string]: unknown;
}
export interface LongTermFields {
  retrieve_top_k?: number;
  write_back?: boolean;
  recall_mode?: string;
  // Stream Memory-Enhance (M-3) — read-time verification of recalled memories.
  verify_reads?: boolean;
  // Stream Memory-Enhance (M-2) — importance floor for run-end write-back.
  write_min_importance?: number;
  // Stream CM-7 — Mem0-style reconcile (dedup/supersede) at write-back.
  reconcile_writes?: boolean;
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
  metadata?: {
    name?: string;
    version?: string;
    tenant?: string;
    [k: string]: unknown;
  };
  spec?: {
    description?: string;
    model?: ModelFields;
    system_prompt?: {
      template?: string;
      // Dynamic-Prompt — opt-in run-time Jinja rendering of the template.
      jinja?: boolean;
      variables?: PromptVariableFields[];
      [k: string]: unknown;
    };
    memory?: { long_term?: LongTermFields | null; [k: string]: unknown } | null;
    tools?: ToolEntry[];
    routing?: RoutingFields | null;
    // Stream J.6 Path B — VL fallback for a text-only main model (ask_image).
    vision?: {
      model?: ModelFields;
      fallbacks?: ModelFields[];
      [k: string]: unknown;
    } | null;
    // Declarative human-approval gate — tool names that pause the run for a
    // human verdict before they execute (the governance counterweight to the
    // always-on exec_python base capability).
    policies?: {
      approval_required_tools?: string[];
      // Seconds a pending approval may sit before auto-reject (default 24h).
      approval_timeout_s?: number;
      // Wall-clock cap on the whole run incl. sub-agent recursion (0 = off).
      run_deadline_s?: number;
      // Stream L.L7 — record completed runs to ObjectStore (privacy toggle).
      trajectory_recording?: boolean;
      // Phase 3 — per-agent master switch for the tool-output-budget feature
      // (externalization + persist + prune). Block absent = enabled.
      tool_output_budget?: { enabled?: boolean; [k: string]: unknown };
      [k: string]: unknown;
    } | null;
    // Orchestrator-Worker — whether the agent may spawn ephemeral workers at
    // run time (spawn_worker). Block absent = enabled (the platform default).
    dynamic_workers?: { enabled?: boolean; [k: string]: unknown } | null;
    // RAG — tenant knowledge bases this agent may search (activates the
    // knowledge_search tool). Block absent = no knowledge access.
    knowledge?: { knowledge_base_refs?: string[]; [k: string]: unknown } | null;
    // Stream RT-1 (RT-ADR-4) — structured final reply: a JSON Schema the
    // agent's FINAL assistant message must validate against (intermediate
    // tool-calling turns are unaffected). Authored in the YAML view; the
    // curated form only surfaces whether it is configured.
    output_schema?: {
      name?: string;
      json_schema?: Record<string, unknown>;
      strict?: boolean;
      [k: string]: unknown;
    } | null;
    // Attached skills — skill refs (``name`` or ``name@N``) the agent loads.
    skills?: string[];
    // SE-16 (SE-A42) — opt-in: build auto-attaches this agent's own ACTIVE
    // distilled skills (lazy, summary only). Absent = off.
    auto_attach_evolved_skills?: boolean;
    // Static delegation — named sub-agents (agent_ref to a deployed agent)
    // the parent may delegate to via a per-subagent tool.
    subagents?: SubAgentFields[];
    [k: string]: unknown;
  };
  [k: string]: unknown;
}

export interface SubAgentFields {
  name?: string;
  agent_ref?: string;
  description?: string;
  [k: string]: unknown;
}

export interface PromptVariableFields {
  name?: string;
  // ``true`` (default) → value rendered verbatim; ``false`` → spotlight-fenced
  // as DATA before substitution.
  trusted?: boolean;
  // ``true`` (default) → a run missing this input is rejected.
  required?: boolean;
  description?: string;
  [k: string]: unknown;
}

function asObj(v: unknown): AgentManifest {
  return v !== null && typeof v === "object" && !Array.isArray(v)
    ? (v as AgentManifest)
    : {};
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
export const readDescription = (m: unknown): string =>
  specOf(m).description ?? "";
export const readModel = (m: unknown): ModelFields => specOf(m).model ?? {};
export const readSystemPrompt = (m: unknown): string =>
  specOf(m).system_prompt?.template ?? "";
export const readMemoryOn = (m: unknown): boolean =>
  (specOf(m).memory?.long_term ?? null) !== null;
export const readTopK = (m: unknown): number | undefined =>
  specOf(m).memory?.long_term?.retrieve_top_k;
// long_term knob readers — each defaults to the backend default so an
// unset field reads as its effective value (LongTermMemorySpec).
export const readWriteBack = (m: unknown): boolean =>
  specOf(m).memory?.long_term?.write_back ?? true;
export const readVerifyReads = (m: unknown): boolean =>
  specOf(m).memory?.long_term?.verify_reads ?? true;
export const readWriteMinImportance = (m: unknown): number =>
  specOf(m).memory?.long_term?.write_min_importance ?? 0.3;
export const readReconcileWrites = (m: unknown): boolean =>
  specOf(m).memory?.long_term?.reconcile_writes ?? true;
export const readRecallMode = (m: unknown): string =>
  specOf(m).memory?.long_term?.recall_mode ?? "per_session";

// ---- reflection evaluator (Stream J.11 routing — the `when=reflection` rule) ----
// The "reflection evaluator model" friendly control is a curated view over the
// existing ``routing`` block: an independent evaluator is just a route rule that
// sends the reflection step to its own model. Empty = no rule = reflection reuses
// the agent's own model (the safe default).
export const readReflectionEvaluator = (m: unknown): ModelFields | undefined =>
  (specOf(m).routing?.rules ?? []).find((r) => r.when === "reflection")?.model;

export const readReflectionEvaluatorOn = (m: unknown): boolean =>
  readReflectionEvaluator(m) !== undefined;

// ---- vision fallback (Stream J.6 Path B — the ``vision:`` block) ----
// When the main model is NOT vision-capable, a separate VL model handles image
// understanding via the ``ask_image`` tool. Empty = no vision block = the agent
// can't read images (the safe default for a text-only model).
export const readVisionModel = (m: unknown): ModelFields | undefined =>
  specOf(m).vision?.model;
export const readVisionOn = (m: unknown): boolean =>
  readVisionModel(m) !== undefined;
export const readMainSupportsVision = (m: unknown): boolean =>
  readModel(m).supports_vision === true;

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
    webSearch: tools.some(
      (t) => t.type === "builtin" && t.name === "web_search",
    ),
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
export const setDescription = (
  m: unknown,
  description: string,
): AgentManifest => patchSpec(m, { description });
export function setModel(m: unknown, model: ModelFields): AgentManifest {
  return patchSpec(m, { model: { ...readModel(m), ...model } });
}
export function setSystemPrompt(m: unknown, template: string): AgentManifest {
  return patchSpec(m, {
    system_prompt: { ...(specOf(m).system_prompt ?? {}), template },
  });
}

// ---- dynamic prompt (system_prompt.jinja + variables) ----
// Opt-in Jinja mode. ``off`` drops both ``jinja`` and ``variables`` so a plain
// agent's manifest stays clean (and satisfies the backend rule that variables
// require jinja). ``on`` sets ``jinja:true``; variable rows are stored verbatim
// (an in-progress row may be partial — validation happens on save).
export const readPromptJinja = (m: unknown): boolean =>
  specOf(m).system_prompt?.jinja === true;

export const readPromptVariables = (m: unknown): PromptVariableFields[] =>
  specOf(m).system_prompt?.variables ?? [];

export function setPromptJinja(m: unknown, on: boolean): AgentManifest {
  const sp = specOf(m).system_prompt ?? {};
  if (on) return patchSpec(m, { system_prompt: { ...sp, jinja: true } });
  const { jinja: _j, variables: _v, ...rest } = sp;
  return patchSpec(m, { system_prompt: rest });
}

export function setPromptVariables(
  m: unknown,
  rows: PromptVariableFields[],
): AgentManifest {
  const sp = specOf(m).system_prompt ?? {};
  if (rows.length === 0) {
    const { variables: _dropped, ...rest } = sp;
    return patchSpec(m, { system_prompt: rest });
  }
  return patchSpec(m, { system_prompt: { ...sp, variables: rows } });
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
// Merge a partial patch into ``memory.long_term`` preserving siblings. Used by
// every long_term knob setter so toggling one never clobbers the others.
function patchLongTerm(
  m: unknown,
  patch: Partial<LongTermFields>,
): AgentManifest {
  const memory = specOf(m).memory ?? {};
  const lt = specOf(m).memory?.long_term ?? {};
  return patchSpec(m, { memory: { ...memory, long_term: { ...lt, ...patch } } });
}
export const setTopK = (m: unknown, k: number): AgentManifest =>
  patchLongTerm(m, { retrieve_top_k: k });
export const setWriteBack = (m: unknown, on: boolean): AgentManifest =>
  patchLongTerm(m, { write_back: on });
export const setVerifyReads = (m: unknown, on: boolean): AgentManifest =>
  patchLongTerm(m, { verify_reads: on });
export const setWriteMinImportance = (m: unknown, v: number): AgentManifest =>
  patchLongTerm(m, { write_min_importance: v });
export const setReconcileWrites = (m: unknown, on: boolean): AgentManifest =>
  patchLongTerm(m, { reconcile_writes: on });
export const setRecallMode = (m: unknown, mode: string): AgentManifest =>
  patchLongTerm(m, { recall_mode: mode });
export function setReflectionEvaluator(
  m: unknown,
  model: ModelFields | null,
): AgentManifest {
  const routing = specOf(m).routing ?? {};
  // Preserve any other route rules (e.g. a planning rule); only touch reflection.
  const others = (routing.rules ?? []).filter((r) => r.when !== "reflection");
  const keep =
    model !== null &&
    (model.provider !== undefined || model.name !== undefined);
  const rules = keep ? [...others, { when: "reflection", model }] : others;
  if (rules.length === 0) {
    // Drop ``rules`` entirely; if routing then has no other keys, drop routing
    // so the manifest stays clean (js-yaml omits ``undefined``).
    const { rules: _dropped, ...rest } = routing;
    return patchSpec(m, {
      routing: Object.keys(rest).length > 0 ? rest : undefined,
    });
  }
  return patchSpec(m, { routing: { ...routing, rules } });
}

// Stream J.6 Path B — set / clear the VL fallback model. ``null`` (or an empty
// pick) removes the whole ``vision`` block so a text-only agent stays clean.
// ``fallbacks`` (advanced, multi-VL chain) is preserved if hand-added in YAML.
export function setVisionModel(
  m: unknown,
  model: ModelFields | null,
): AgentManifest {
  const keep =
    model !== null &&
    (model.provider !== undefined || model.name !== undefined);
  const existing = specOf(m).vision ?? {};
  if (!keep) {
    const { model: _dropped, ...rest } = existing;
    return patchSpec(m, {
      vision: Object.keys(rest).length > 0 ? rest : undefined,
    });
  }
  return patchSpec(m, { vision: { ...existing, model } });
}

export function setTool(
  m: unknown,
  kind: "webSearch" | "http" | "mcp",
  on: boolean,
): AgentManifest {
  const tools = specOf(m).tools ?? [];
  const without = (pred: (t: ToolEntry) => boolean): ToolEntry[] =>
    tools.filter((t) => !pred(t));
  if (kind === "webSearch") {
    const isWeb = (t: ToolEntry): boolean =>
      t.type === "builtin" && t.name === "web_search";
    return patchSpec(m, {
      tools: on
        ? [
            ...without(isWeb),
            { type: "builtin", name: "web_search", config: {} },
          ]
        : without(isWeb),
    });
  }
  if (kind === "http") {
    const isHttp = (t: ToolEntry): boolean => t.type === "http";
    return patchSpec(m, {
      tools: on ? [...without(isHttp), { type: "http" }] : without(isHttp),
    });
  }
  const isMcp = (t: ToolEntry): boolean => t.type === "mcp";
  return patchSpec(m, {
    tools: on
      ? [...without(isMcp), { type: "mcp", allow_tools: [] }]
      : without(isMcp),
  });
}
export function setMcpAllowTools(m: unknown, allow: string[]): AgentManifest {
  const tools = (specOf(m).tools ?? []).map((t) =>
    t.type === "mcp" ? { ...t, allow_tools: allow } : t,
  );
  return patchSpec(m, { tools });
}

// Selecting servers IS enabling MCP — there is no separate enable toggle. An
// empty selection drops the whole ``mcp`` tool entry (MCP off); a non-empty
// selection creates the entry on first pick. ``allow_tools`` is pruned to the
// selected servers' scope by the caller (the picker knows each server's tools).
export function setMcpServers(m: unknown, servers: string[]): AgentManifest {
  return setMcp(m, servers, readMcpAllowTools(m));
}

const readMcpAllowTools = (m: unknown): string[] =>
  (specOf(m).tools ?? []).find((t) => t.type === "mcp")?.allow_tools ?? [];

// Single writer for the whole ``mcp`` tool entry — both ``servers`` and
// ``allow_tools`` in one patch, so the picker can update them together without
// a stale-read double-patch. Empty ``servers`` ⇒ MCP off (entry dropped).
export function setMcp(
  m: unknown,
  servers: string[],
  allowTools: string[],
): AgentManifest {
  const withoutMcp = (specOf(m).tools ?? []).filter((t) => t.type !== "mcp");
  if (servers.length === 0) {
    return patchSpec(m, { tools: withoutMcp });
  }
  return patchSpec(m, {
    tools: [...withoutMcp, { type: "mcp", servers, allow_tools: allowTools }],
  });
}

// ---- approval gate (policies.approval_required_tools) ----
// Tool names that, when the agent dispatches them, pause the run for a human
// verdict (LangGraph interrupt). The governance counterweight to the always-on
// exec_python / bash base capability: the capability can't be removed, but it
// can be gated behind approval. Empty = no gate (drop the key + empty policies).
export const readApprovalTools = (m: unknown): string[] =>
  specOf(m).policies?.approval_required_tools ?? [];

export function setApprovalTools(m: unknown, tools: string[]): AgentManifest {
  const policies = specOf(m).policies ?? {};
  if (tools.length === 0) {
    const { approval_required_tools: _dropped, ...rest } = policies;
    return patchSpec(m, {
      policies: Object.keys(rest).length > 0 ? rest : undefined,
    });
  }
  return patchSpec(m, {
    policies: { ...policies, approval_required_tools: tools },
  });
}

// ---- other policy knobs (same ``policies`` block as the approval gate) ----
function patchPolicies(
  m: unknown,
  patch: Record<string, unknown>,
): AgentManifest {
  const policies = specOf(m).policies ?? {};
  return patchSpec(m, { policies: { ...policies, ...patch } });
}
export const readApprovalTimeout = (m: unknown): number =>
  specOf(m).policies?.approval_timeout_s ?? 86400;
export const setApprovalTimeout = (m: unknown, s: number): AgentManifest =>
  patchPolicies(m, { approval_timeout_s: s });
export const readRunDeadline = (m: unknown): number =>
  specOf(m).policies?.run_deadline_s ?? 0;
export const setRunDeadline = (m: unknown, s: number): AgentManifest =>
  patchPolicies(m, { run_deadline_s: s });
export const readTrajectoryRecording = (m: unknown): boolean =>
  specOf(m).policies?.trajectory_recording ?? true;
export const setTrajectoryRecording = (m: unknown, on: boolean): AgentManifest =>
  patchPolicies(m, { trajectory_recording: on });

// ---- tool-output budget (policies.tool_output_budget.enabled) ----
// Per-agent master switch for the whole tool-output-budget feature
// (externalization + persist + prune). Block absent = enabled (the platform
// switch governs the ceiling: effective = platform AND agent). ``on`` drops the
// block (back to default-on, clean YAML); ``off`` writes ``{enabled:false}``.
export const readToolBudgetOn = (m: unknown): boolean =>
  (specOf(m).policies?.tool_output_budget?.enabled ?? true) !== false;

export function setToolBudgetOn(m: unknown, on: boolean): AgentManifest {
  const policies = specOf(m).policies ?? {};
  if (on) {
    const { tool_output_budget: _dropped, ...rest } = policies;
    return patchSpec(m, {
      policies: Object.keys(rest).length > 0 ? rest : undefined,
    });
  }
  return patchSpec(m, {
    policies: { ...policies, tool_output_budget: { enabled: false } },
  });
}

// ---- dynamic workers (spawn_worker) ----
// Whether the agent's LLM may spawn ephemeral workers at run time. The block is
// absent by default and that means ENABLED (the platform switch governs the
// ceiling). The form surfaces this so the autonomous-worker behaviour is
// visible + can be opted out per agent: ``off`` writes ``{enabled:false}``;
// ``on`` drops the block (back to the default-on state, keeping YAML clean).
// Stream RT-1 (RT-ADR-4) — the structured-output block is YAML-authored; the
// curated form only shows whether it is configured (and under which name).
// Returns the effective wire name when configured, ``null`` when absent.
export const readOutputSchemaName = (m: unknown): string | null => {
  const block = specOf(m).output_schema;
  if (!block || typeof block !== "object") return null;
  return typeof block.name === "string" && block.name
    ? block.name
    : "final_response";
};

export const readDynamicWorkersOn = (m: unknown): boolean =>
  (specOf(m).dynamic_workers?.enabled ?? true) !== false;

export function setDynamicWorkersOn(m: unknown, on: boolean): AgentManifest {
  if (on) {
    const dw = specOf(m).dynamic_workers ?? {};
    const { enabled: _dropped, ...rest } = dw;
    return patchSpec(m, {
      dynamic_workers: Object.keys(rest).length > 0 ? rest : undefined,
    });
  }
  return patchSpec(m, {
    dynamic_workers: { ...(specOf(m).dynamic_workers ?? {}), enabled: false },
  });
}

// ---- evolved-skill auto-attach (SE-16 SE-A42) ----
// Whether build auto-attaches this agent's own ACTIVE distilled skills
// (evolution flywheel output). Plain opt-in bool: absent = off, so ``off``
// drops the key to keep the YAML clean.
export const readAutoAttachEvolvedSkills = (m: unknown): boolean =>
  specOf(m).auto_attach_evolved_skills === true;

export function setAutoAttachEvolvedSkills(m: unknown, on: boolean): AgentManifest {
  return patchSpec(m, { auto_attach_evolved_skills: on ? true : undefined });
}

// ---- knowledge (RAG knowledge_base_refs) ----
// Tenant knowledge bases the agent may search. Empty = drop the block (no
// knowledge access) so a non-RAG agent's manifest stays clean.
export const readKnowledgeRefs = (m: unknown): string[] =>
  specOf(m).knowledge?.knowledge_base_refs ?? [];

export function setKnowledgeRefs(m: unknown, refs: string[]): AgentManifest {
  const knowledge = specOf(m).knowledge ?? {};
  if (refs.length === 0) {
    const { knowledge_base_refs: _dropped, ...rest } = knowledge;
    return patchSpec(m, {
      knowledge: Object.keys(rest).length > 0 ? rest : undefined,
    });
  }
  return patchSpec(m, {
    knowledge: { ...knowledge, knowledge_base_refs: refs },
  });
}

// ---- skills (attached skill refs) ----
// Skill names the agent loads. Empty = drop the key.
export const readSkills = (m: unknown): string[] => specOf(m).skills ?? [];

export function setSkills(m: unknown, skills: string[]): AgentManifest {
  return patchSpec(m, { skills: skills.length > 0 ? skills : undefined });
}

// ---- subagents (static delegation) ----
// Named delegation targets referencing deployed agents. Rows are stored
// verbatim (an in-progress row may be partial — validation happens on save);
// empty = drop the block.
export const readSubagents = (m: unknown): SubAgentFields[] =>
  specOf(m).subagents ?? [];

export function setSubagents(
  m: unknown,
  rows: SubAgentFields[],
): AgentManifest {
  return patchSpec(m, { subagents: rows.length > 0 ? rows : undefined });
}
