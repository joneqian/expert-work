import { describe, expect, it, test } from "vitest";
import { dumpYaml, parseYaml as parse } from "../yaml";
import {
  readApprovalTools,
  readDescription,
  readDynamicWorkersOn,
  readKnowledgeRefs,
  readMemoryOn,
  readSkills,
  readSubagents,
  readModel,
  readName,
  readPromptJinja,
  readPromptVariables,
  readReflectionEvaluator,
  readReflectionEvaluatorOn,
  readSystemPrompt,
  readTools,
  readTopK,
  readVisionModel,
  readVisionOn,
  setDescription,
  setPromptJinja,
  setPromptVariables,
  setMcpAllowTools,
  setMcp,
  setMcpServers,
  setMemoryOn,
  setModel,
  setName,
  setApprovalTools,
  setDynamicWorkersOn,
  setKnowledgeRefs,
  setReflectionEvaluator,
  setSkills,
  setSubagents,
  setSystemPrompt,
  setTool,
  setTopK,
  setVisionModel,
  readWriteBack,
  readVerifyReads,
  readWriteMinImportance,
  readReconcileWrites,
  readRecallMode,
  setWriteBack,
  setVerifyReads,
  setWriteMinImportance,
  setReconcileWrites,
  setRecallMode,
  readApprovalTimeout,
  readTrajectoryRecording,
  setApprovalTimeout,
  setTrajectoryRecording,
  readFallback,
  setFallback,
  normalizeForSubmit,
  readPromptInjection,
  readOutputScreen,
  readOutputJudge,
  readOutputJudgeOnError,
  readActionScreen,
  readActionScreenOnError,
  readOutputDlp,
  readExtends,
  setPromptInjection,
  setOutputScreen,
  setOutputJudge,
  setOutputJudgeOnError,
  setActionScreen,
  setActionScreenOnError,
  setOutputDlp,
  readRunBudget,
  patchRunBudget,
  readContextGates,
  patchContextGates,
  readSecurity,
  patchSecurity,
  readSandboxFs,
  patchSandboxFs,
} from "../form_model";
import type { AgentManifest } from "../form_model";

const seed = {
  apiVersion: "expert_work.io/v1",
  kind: "Agent",
  metadata: { name: "my-agent", version: "1.0.0", tenant: "my-tenant" },
  spec: {
    model: { provider: "anthropic", name: "claude-sonnet-4-6" },
    system_prompt: { template: "You are helpful." },
    memory: {
      long_term: {
        retrieve_top_k: 5,
        write_back: true,
        recall_mode: "per_session",
      },
    },
    sandbox: { resources: { cpu: "1.0" } },
  },
};

describe("form_model readers", () => {
  it("reads scalar curated fields", () => {
    expect(readName(seed)).toBe("my-agent");
    expect(readDescription(seed)).toBe("");
    expect(readModel(seed).provider).toBe("anthropic");
    expect(readSystemPrompt(seed)).toBe("You are helpful.");
    expect(readMemoryOn(seed)).toBe(true);
    expect(readTopK(seed)).toBe(5);
  });

  it("reads tool flags from an empty tool list", () => {
    expect(readTools(seed)).toEqual({
      webSearch: false,
      http: false,
      mcp: false,
      mcpAllowTools: [],
      mcpServers: [],
    });
  });
});

describe("model fallback chain (spec.model.fallback)", () => {
  it("reads an empty chain when no fallback is set", () => {
    expect(readFallback(seed)).toEqual([]);
  });

  it("writes a chain, preserves the primary + siblings, reads it back", () => {
    const chain = [
      { provider: "glm", name: "glm-4-flash" },
      { provider: "deepseek", name: "deepseek-chat" },
    ];
    const next = setFallback(seed, chain);
    expect(readFallback(next)).toEqual(chain);
    // Primary model + its name untouched.
    expect(next.spec?.model?.provider).toBe("anthropic");
    expect(next.spec?.model?.name).toBe("claude-sonnet-4-6");
    // Unrelated spec keys preserved.
    expect(next.spec?.system_prompt).toEqual(seed.spec.system_prompt);
  });

  it("setFallback([]) drops the key so a single-provider manifest stays clean", () => {
    const withChain = setFallback(seed, [{ provider: "glm", name: "glm-4-flash" }]);
    const cleared = setFallback(withChain, []);
    expect(readFallback(cleared)).toEqual([]);
    expect("fallback" in (cleared.spec?.model ?? {})).toBe(false);
  });

  it("preserves an entry's own nested fallback (YAML power-user round-trip)", () => {
    const chain = [
      {
        provider: "glm",
        name: "glm-4-flash",
        fallback: [{ provider: "kimi", name: "kimi-k2" }],
      },
    ];
    const next = setFallback(seed, chain);
    expect(readFallback(next)[0].fallback).toEqual([{ provider: "kimi", name: "kimi-k2" }]);
  });

  it("does not mutate the input manifest", () => {
    const before = JSON.stringify(seed);
    setFallback(seed, [{ provider: "glm", name: "glm-4-flash" }]);
    expect(JSON.stringify(seed)).toBe(before);
  });
});

describe("normalizeForSubmit (serialize-boundary fallback pruning)", () => {
  it("drops an added-but-unfilled entry so the backend never sees [{}]", () => {
    const m = setFallback(seed, [{}, { provider: "glm", name: "glm-4-flash" }]);
    expect(readFallback(normalizeForSubmit(m))).toEqual([
      { provider: "glm", name: "glm-4-flash" },
    ]);
  });

  it("drops a provider-only (name missing) entry", () => {
    const m = setFallback(seed, [{ provider: "glm" }]);
    expect(readFallback(normalizeForSubmit(m))).toEqual([]);
    // Fully pruned → key removed, single-provider manifest stays clean.
    expect("fallback" in (normalizeForSubmit(m).spec?.model ?? {})).toBe(false);
  });

  it("drops an entry that duplicates the primary (backend rejects as a cycle)", () => {
    // seed primary = anthropic/claude-sonnet-4-6
    const m = setFallback(seed, [
      { provider: "anthropic", name: "claude-sonnet-4-6" },
      { provider: "glm", name: "glm-4-flash" },
    ]);
    expect(readFallback(normalizeForSubmit(m))).toEqual([
      { provider: "glm", name: "glm-4-flash" },
    ]);
  });

  it("de-dupes repeated entries within the chain, keeping the first", () => {
    const m = setFallback(seed, [
      { provider: "glm", name: "glm-4-flash", temperature: 0.1 },
      { provider: "glm", name: "glm-4-flash", temperature: 0.9 },
    ]);
    expect(readFallback(normalizeForSubmit(m))).toEqual([
      { provider: "glm", name: "glm-4-flash", temperature: 0.1 },
    ]);
  });

  it("keeps a fully-valid chain untouched (incl. an entry's nested fallback)", () => {
    const chain = [
      { provider: "glm", name: "glm-4-flash", fallback: [{ provider: "kimi", name: "kimi-k2" }] },
      { provider: "deepseek", name: "deepseek-chat" },
    ];
    const m = setFallback(seed, chain);
    expect(readFallback(normalizeForSubmit(m))).toEqual(chain);
  });

  it("does not mutate the input manifest", () => {
    const m = setFallback(seed, [{}, { provider: "glm", name: "glm-4-flash" }]);
    const before = JSON.stringify(m);
    normalizeForSubmit(m);
    expect(JSON.stringify(m)).toBe(before);
  });
});

describe("form_model writers preserve siblings", () => {
  it("setName updates name and preserves apiVersion + sandbox", () => {
    const next = setName(seed, "x");
    expect(next.metadata?.name).toBe("x");
    expect(next.apiVersion).toBe("expert_work.io/v1");
    expect(next.spec?.sandbox).toEqual(seed.spec.sandbox);
  });

  it("setModel merges model fields and preserves system_prompt", () => {
    const next = setModel(seed, { provider: "deepseek" });
    expect(next.spec?.model?.provider).toBe("deepseek");
    expect(next.spec?.model?.name).toBe("claude-sonnet-4-6");
    expect(next.spec?.system_prompt).toEqual(seed.spec.system_prompt);
  });

  it("setSystemPrompt preserves other spec keys", () => {
    const next = setSystemPrompt(seed, "New prompt.");
    expect(next.spec?.system_prompt?.template).toBe("New prompt.");
    expect(next.spec?.model).toEqual(seed.spec.model);
    expect(next.spec?.sandbox).toEqual(seed.spec.sandbox);
  });

  it("setDescription preserves other spec keys", () => {
    const next = setDescription(seed, "A helpful agent.");
    expect(next.spec?.description).toBe("A helpful agent.");
    expect(next.spec?.model).toEqual(seed.spec.model);
    expect(next.spec?.sandbox).toEqual(seed.spec.sandbox);
  });

  it("setMemoryOn(false) nulls long_term, then (true) restores defaults, sandbox preserved", () => {
    const off = setMemoryOn(seed, false);
    expect(off.spec?.memory?.long_term).toBeNull();
    expect(off.spec?.sandbox).toEqual(seed.spec.sandbox);

    const on = setMemoryOn(off, true);
    expect(on.spec?.memory?.long_term?.retrieve_top_k).toBe(5);
    expect(on.spec?.sandbox).toEqual(seed.spec.sandbox);
  });

  it("setTopK updates retrieve_top_k and keeps write_back", () => {
    const next = setTopK(seed, 8);
    expect(next.spec?.memory?.long_term?.retrieve_top_k).toBe(8);
    expect(next.spec?.memory?.long_term?.write_back).toBe(true);
  });

  it("long_term knob readers default to the backend defaults when unset", () => {
    const bare = { spec: { memory: { long_term: { retrieve_top_k: 5 } } } };
    expect(readWriteBack(bare)).toBe(true);
    expect(readVerifyReads(bare)).toBe(true);
    expect(readWriteMinImportance(bare)).toBe(0.3);
    expect(readReconcileWrites(bare)).toBe(true);
    expect(readRecallMode(bare)).toBe("per_session");
  });

  it("long_term knob setters patch one field, preserving the rest", () => {
    const a = setWriteBack(seed, false);
    expect(a.spec?.memory?.long_term?.write_back).toBe(false);
    expect(a.spec?.memory?.long_term?.retrieve_top_k).toBe(5);

    const b = setVerifyReads(setWriteMinImportance(seed, 0.6), false);
    expect(b.spec?.memory?.long_term?.write_min_importance).toBe(0.6);
    expect(b.spec?.memory?.long_term?.verify_reads).toBe(false);
    expect(b.spec?.memory?.long_term?.write_back).toBe(true);

    const c = setRecallMode(setReconcileWrites(seed, false), "per_turn");
    expect(c.spec?.memory?.long_term?.reconcile_writes).toBe(false);
    expect(c.spec?.memory?.long_term?.recall_mode).toBe("per_turn");
  });

  it("policy knob readers default; setters share the policies block with approval", () => {
    expect(readApprovalTimeout(seed)).toBe(86400);
    expect(readTrajectoryRecording(seed)).toBe(true);

    const withGate = setApprovalTools(seed, ["exec_python"]);

    const noTrace = setTrajectoryRecording(seed, false);
    expect(noTrace.spec?.policies?.trajectory_recording).toBe(false);

    const withTimeout = setApprovalTimeout(withGate, 3600);
    expect(withTimeout.spec?.policies?.approval_timeout_s).toBe(3600);
    expect(withTimeout.spec?.policies?.approval_required_tools).toEqual([
      "exec_python",
    ]);
  });

  it("setTool adds/removes builtin and http tools independently", () => {
    const withWeb = setTool(seed, "webSearch", true);
    expect(readTools(withWeb).webSearch).toBe(true);
    expect(withWeb.spec?.tools).toContainEqual({
      type: "builtin",
      name: "web_search",
      config: {},
    });

    const withBoth = setTool(withWeb, "http", true);
    expect(readTools(withBoth).webSearch).toBe(true);
    expect(readTools(withBoth).http).toBe(true);

    const httpOnly = setTool(withBoth, "webSearch", false);
    expect(readTools(httpOnly).webSearch).toBe(false);
    expect(readTools(httpOnly).http).toBe(true);
  });

  it("setMcpAllowTools updates the mcp tool's allow list", () => {
    const withMcp = setTool(seed, "mcp", true);
    const allowed = setMcpAllowTools(withMcp, ["a", "b"]);
    expect(readTools(allowed).mcpAllowTools).toEqual(["a", "b"]);
  });
});

const withMcp = () =>
  setTool({ apiVersion: "v1", kind: "Agent", spec: {} }, "mcp", true);

test("readTools defaults mcpServers to empty", () => {
  expect(readTools(withMcp()).mcpServers).toEqual([]);
});

test("setMcpServers sets the servers list on the mcp tool entry", () => {
  const m = setMcpServers(withMcp(), ["github", "linear"]);
  expect(readTools(m).mcpServers).toEqual(["github", "linear"]);
});

test("setMcpServers preserves allow_tools (merge-preserving)", () => {
  let m = setMcpAllowTools(withMcp(), ["create_issue"]);
  m = setMcpServers(m, ["github"]);
  expect(readTools(m).mcpAllowTools).toEqual(["create_issue"]);
  expect(readTools(m).mcpServers).toEqual(["github"]);
});

test("setMcpServers creates the mcp entry when selecting a server (= enabling MCP)", () => {
  const m = setMcpServers({ apiVersion: "v1", kind: "Agent", spec: {} }, [
    "github",
  ]);
  expect(readTools(m).mcp).toBe(true);
  expect(readTools(m).mcpServers).toEqual(["github"]);
});

test("setMcpServers([]) drops the mcp entry (MCP off, no separate toggle)", () => {
  const m = setMcpServers(withMcp(), []);
  expect(readTools(m).mcp).toBe(false);
  expect((m.spec?.tools ?? []).some((t) => t.type === "mcp")).toBe(false);
});

test("setMcp writes servers + allow_tools in one patch", () => {
  const m = setMcp(
    { apiVersion: "v1", kind: "Agent", spec: {} },
    ["github"],
    ["create_issue"],
  );
  expect(readTools(m).mcpServers).toEqual(["github"]);
  expect(readTools(m).mcpAllowTools).toEqual(["create_issue"]);
});

describe("form_model preserve chain + immutability", () => {
  it("preserves apiVersion/kind/sandbox through a chain of edits", () => {
    let m = setName(seed, "renamed");
    m = setDescription(m, "desc");
    m = setModel(m, { provider: "openai" });
    m = setMemoryOn(m, false);
    m = setTool(m, "webSearch", true);

    expect(m.apiVersion).toBe(seed.apiVersion);
    expect(m.kind).toBe(seed.kind);
    expect(m.spec?.sandbox).toEqual(seed.spec.sandbox);
  });

  it("does not mutate the input manifest", () => {
    setName(seed, "mutated");
    expect(seed.metadata.name).toBe("my-agent");
  });
});

describe("reflection evaluator (routing when=reflection projection)", () => {
  it("reads undefined when no routing rule", () => {
    expect(readReflectionEvaluator(seed)).toBeUndefined();
    expect(readReflectionEvaluatorOn(seed)).toBe(false);
  });

  it("writes a when=reflection route rule and reads it back", () => {
    const m = setReflectionEvaluator(seed, {
      provider: "openai",
      name: "gpt-4o-mini",
    });
    expect(m.spec?.routing?.rules).toEqual([
      {
        when: "reflection",
        model: { provider: "openai", name: "gpt-4o-mini" },
      },
    ]);
    expect(readReflectionEvaluator(m)?.name).toBe("gpt-4o-mini");
    expect(readReflectionEvaluatorOn(m)).toBe(true);
  });

  it("keeps a partial pick (provider only) so the picker doesn't lose state", () => {
    const m = setReflectionEvaluator(seed, { provider: "openai" });
    expect(readReflectionEvaluator(m)).toEqual({ provider: "openai" });
  });

  it("clearing removes the rule and drops empty routing", () => {
    const withRule = setReflectionEvaluator(seed, {
      provider: "openai",
      name: "gpt-4o-mini",
    });
    const cleared = setReflectionEvaluator(withRule, null);
    expect(readReflectionEvaluator(cleared)).toBeUndefined();
    expect(cleared.spec?.routing).toBeUndefined();
  });

  it("preserves a sibling planning rule when setting/clearing reflection", () => {
    const base = {
      ...seed,
      spec: {
        ...seed.spec,
        routing: {
          rules: [
            {
              when: "planning",
              model: { provider: "anthropic", name: "claude-opus-4-8" },
            },
          ],
        },
      },
    };
    const set = setReflectionEvaluator(base, {
      provider: "openai",
      name: "gpt-4o-mini",
    });
    expect(set.spec?.routing?.rules).toHaveLength(2);
    const cleared = setReflectionEvaluator(set, null);
    // planning survives; only reflection removed; routing stays (still has planning).
    expect(cleared.spec?.routing?.rules).toEqual([
      {
        when: "planning",
        model: { provider: "anthropic", name: "claude-opus-4-8" },
      },
    ]);
  });

  it("does not mutate the input manifest", () => {
    setReflectionEvaluator(seed, { provider: "openai", name: "gpt-4o-mini" });
    expect((seed.spec as { routing?: unknown }).routing).toBeUndefined();
  });
});

describe("approval gate (policies.approval_required_tools)", () => {
  it("reads an empty list when no policies block", () => {
    expect(readApprovalTools(seed)).toEqual([]);
  });

  it("writes the approval tool list and reads it back", () => {
    const m = setApprovalTools(seed, ["exec_python", "http"]);
    expect(m.spec?.policies?.approval_required_tools).toEqual([
      "exec_python",
      "http",
    ]);
    expect(readApprovalTools(m)).toEqual(["exec_python", "http"]);
  });

  it("clearing drops the key and empty policies block", () => {
    const withGate = setApprovalTools(seed, ["bash"]);
    const cleared = setApprovalTools(withGate, []);
    expect(readApprovalTools(cleared)).toEqual([]);
    expect(cleared.spec?.policies).toBeUndefined();
  });

  it("preserves sibling policy keys when clearing the gate", () => {
    const base = {
      ...seed,
      spec: {
        ...seed.spec,
        policies: {
          approval_required_tools: ["bash"],
          approval_timeout_s: 3600,
        },
      },
    };
    const cleared = setApprovalTools(base, []);
    expect(cleared.spec?.policies).toEqual({ approval_timeout_s: 3600 });
  });

  it("does not mutate the input manifest", () => {
    setApprovalTools(seed, ["exec_python"]);
    expect((seed.spec as { policies?: unknown }).policies).toBeUndefined();
  });
});

describe("dynamic workers (spawn_worker opt-out)", () => {
  it("defaults to ON when no dynamic_workers block (the platform default)", () => {
    expect(readDynamicWorkersOn(seed)).toBe(true);
  });

  it("reads OFF when enabled is false", () => {
    const off = setDynamicWorkersOn(seed, false);
    expect(off.spec?.dynamic_workers?.enabled).toBe(false);
    expect(readDynamicWorkersOn(off)).toBe(false);
  });

  it("turning ON drops the block so YAML stays clean (absent = on)", () => {
    const off = setDynamicWorkersOn(seed, false);
    const on = setDynamicWorkersOn(off, true);
    expect(on.spec?.dynamic_workers).toBeUndefined();
    expect(readDynamicWorkersOn(on)).toBe(true);
  });

  it("does not mutate the input manifest", () => {
    setDynamicWorkersOn(seed, false);
    expect(
      (seed.spec as { dynamic_workers?: unknown }).dynamic_workers,
    ).toBeUndefined();
  });
});

describe("knowledge (RAG knowledge_base_refs)", () => {
  it("reads empty when no knowledge block", () => {
    expect(readKnowledgeRefs(seed)).toEqual([]);
  });

  it("writes refs and reads them back", () => {
    const m = setKnowledgeRefs(seed, ["hr", "eng"]);
    expect(m.spec?.knowledge?.knowledge_base_refs).toEqual(["hr", "eng"]);
    expect(readKnowledgeRefs(m)).toEqual(["hr", "eng"]);
  });

  it("clearing drops the knowledge block", () => {
    const withRefs = setKnowledgeRefs(seed, ["hr"]);
    const cleared = setKnowledgeRefs(withRefs, []);
    expect(cleared.spec?.knowledge).toBeUndefined();
  });
});

describe("skills (attached refs)", () => {
  it("reads empty when no skills", () => {
    expect(readSkills(seed)).toEqual([]);
  });

  it("writes + clears skills", () => {
    const m = setSkills(seed, ["pptx", "docx"]);
    expect(readSkills(m)).toEqual(["pptx", "docx"]);
    expect(setSkills(m, []).spec?.skills).toBeUndefined();
  });
});

describe("subagents (static delegation)", () => {
  it("reads empty when no subagents", () => {
    expect(readSubagents(seed)).toEqual([]);
  });

  it("writes rows verbatim and clears on empty", () => {
    const rows = [
      {
        name: "researcher",
        agent_ref: "deep-researcher@1.0.0",
        description: "research",
      },
    ];
    const m = setSubagents(seed, rows);
    expect(m.spec?.subagents).toEqual(rows);
    expect(readSubagents(m)).toEqual(rows);
    expect(setSubagents(m, []).spec?.subagents).toBeUndefined();
  });

  it("does not mutate the input manifest", () => {
    setKnowledgeRefs(seed, ["x"]);
    setSubagents(seed, [{ name: "a", agent_ref: "b@1", description: "c" }]);
    expect((seed.spec as { knowledge?: unknown }).knowledge).toBeUndefined();
    expect((seed.spec as { subagents?: unknown }).subagents).toBeUndefined();
  });
});

describe("vision fallback (Stream J.6 Path B — vision block)", () => {
  it("reads undefined when no vision block", () => {
    expect(readVisionModel(seed)).toBeUndefined();
    expect(readVisionOn(seed)).toBe(false);
  });

  it("writes a vision.model and reads it back", () => {
    const m = setVisionModel(seed, { provider: "qwen", name: "qwen-vl-max" });
    expect(m.spec?.vision?.model).toEqual({
      provider: "qwen",
      name: "qwen-vl-max",
    });
    expect(readVisionModel(m)?.name).toBe("qwen-vl-max");
    expect(readVisionOn(m)).toBe(true);
  });

  it("clearing removes the vision block", () => {
    const withVl = setVisionModel(seed, {
      provider: "qwen",
      name: "qwen-vl-max",
    });
    const cleared = setVisionModel(withVl, null);
    expect(readVisionModel(cleared)).toBeUndefined();
    expect(cleared.spec?.vision).toBeUndefined();
  });

  it("preserves hand-added fallbacks when changing the model", () => {
    const base = {
      ...seed,
      spec: {
        ...seed.spec,
        vision: {
          model: { provider: "qwen", name: "qwen-vl-max" },
          fallbacks: [{ provider: "zhipu", name: "glm-4v" }],
        },
      },
    };
    const swapped = setVisionModel(base, {
      provider: "qwen",
      name: "qwen-vl-plus",
    });
    expect(swapped.spec?.vision?.model?.name).toBe("qwen-vl-plus");
    expect(swapped.spec?.vision?.fallbacks).toEqual([
      { provider: "zhipu", name: "glm-4v" },
    ]);
  });

  it("does not mutate the input manifest", () => {
    setVisionModel(seed, { provider: "qwen", name: "qwen-vl-max" });
    expect((seed.spec as { vision?: unknown }).vision).toBeUndefined();
  });
});

describe("form_model — dynamic prompt (jinja + variables)", () => {
  it("defaults: jinja off, no variables", () => {
    expect(readPromptJinja(seed)).toBe(false);
    expect(readPromptVariables(seed)).toEqual([]);
  });

  it("enabling jinja sets the flag, preserving the template", () => {
    const m = setPromptJinja(seed, true);
    expect(m.spec?.system_prompt?.jinja).toBe(true);
    expect(m.spec?.system_prompt?.template).toBe("You are helpful.");
    expect(readPromptJinja(m)).toBe(true);
  });

  it("disabling jinja drops jinja AND variables (backend requires the pairing)", () => {
    const on = setPromptVariables(setPromptJinja(seed, true), [
      { name: "persona" },
    ]);
    const off = setPromptJinja(on, false);
    expect(off.spec?.system_prompt?.jinja).toBeUndefined();
    expect(off.spec?.system_prompt?.variables).toBeUndefined();
    expect(off.spec?.system_prompt?.template).toBe("You are helpful.");
  });

  it("writes variable rows verbatim and reads them back", () => {
    const m = setPromptVariables(setPromptJinja(seed, true), [
      { name: "persona", trusted: true, required: true },
      {
        name: "profile",
        trusted: false,
        required: false,
        description: "客户画像",
      },
    ]);
    expect(readPromptVariables(m)).toHaveLength(2);
    expect(readPromptVariables(m)[1]).toMatchObject({
      name: "profile",
      trusted: false,
    });
  });

  it("empty variable list drops the key", () => {
    const m = setPromptVariables(setPromptJinja(seed, true), []);
    expect(m.spec?.system_prompt?.variables).toBeUndefined();
  });

  it("does not mutate the input manifest", () => {
    setPromptJinja(seed, true);
    expect(
      (seed.spec.system_prompt as { jinja?: unknown }).jinja,
    ).toBeUndefined();
  });
});

describe("form_model defenses readers (default-aware)", () => {
  it("reads effective DefenseSpec defaults when defenses absent", () => {
    const m: AgentManifest = { spec: {} };
    expect(readPromptInjection(m)).toBe("spotlight");
    expect(readOutputScreen(m)).toBe("block");
    expect(readOutputJudge(m)).toBe("off");
    expect(readOutputJudgeOnError(m)).toBe("open");
    expect(readActionScreen(m)).toBe("off");
    expect(readActionScreenOnError(m)).toBe("open");
    expect(readOutputDlp(m)).toBe("off");
    expect(readExtends(m)).toBeUndefined();
  });

  it("reads explicit values", () => {
    const m: AgentManifest = {
      spec: {
        extends: "secure-template",
        defenses: {
          prompt_injection: "off",
          output_screen: "off",
          output_judge: "block",
          output_judge_on_error: "closed",
          action_screen: "approval",
          action_screen_on_error: "closed",
          output_dlp: "redact",
        },
      },
    };
    expect(readPromptInjection(m)).toBe("off");
    expect(readOutputScreen(m)).toBe("off");
    expect(readOutputJudge(m)).toBe("block");
    expect(readOutputJudgeOnError(m)).toBe("closed");
    expect(readActionScreen(m)).toBe("approval");
    expect(readActionScreenOnError(m)).toBe("closed");
    expect(readOutputDlp(m)).toBe("redact");
    expect(readExtends(m)).toBe("secure-template");
  });
});

describe("form_model defenses setters (default-omission + orphan cleanup)", () => {
  const BASE: AgentManifest = { spec: { model: { provider: "openai" } } };

  it("writing a non-default value adds the defenses key", () => {
    const out = setOutputScreen(BASE, "off");
    expect(out.spec?.defenses?.output_screen).toBe("off");
    // sibling spec fields untouched
    expect(out.spec?.model?.provider).toBe("openai");
  });

  it("writing the default value omits the key and drops an empty defenses block", () => {
    const withOff = setOutputScreen(BASE, "off");
    const backToDefault = setOutputScreen(withOff, "block");
    expect(backToDefault.spec?.defenses).toBeUndefined();
  });

  it("turning the judge off clears the output_judge_on_error orphan", () => {
    const on = setOutputJudge(BASE, "block");
    const withErr = setOutputJudgeOnError(on, "closed");
    expect(withErr.spec?.defenses?.output_judge).toBe("block");
    expect(withErr.spec?.defenses?.output_judge_on_error).toBe("closed");
    const off = setOutputJudge(withErr, "off");
    expect(off.spec?.defenses?.output_judge).toBeUndefined();
    expect(off.spec?.defenses?.output_judge_on_error).toBeUndefined();
  });

  it("turning action_screen off clears the action_screen_on_error orphan", () => {
    const on = setActionScreen(BASE, "block");
    const withErr = setActionScreenOnError(on, "closed");
    const off = setActionScreen(withErr, "off");
    expect(off.spec?.defenses?.action_screen).toBeUndefined();
    expect(off.spec?.defenses?.action_screen_on_error).toBeUndefined();
  });

  it("on_error at its default (open) is omitted", () => {
    const on = setOutputJudge(BASE, "block");
    const openErr = setOutputJudgeOnError(on, "open");
    expect(openErr.spec?.defenses?.output_judge_on_error).toBeUndefined();
    // judge itself stays written
    expect(openErr.spec?.defenses?.output_judge).toBe("block");
  });

  it("setting one switch preserves other defense siblings", () => {
    const a = setOutputScreen(BASE, "off");
    const b = setOutputDlp(a, "redact");
    const c = setPromptInjection(b, "off");
    expect(c.spec?.defenses?.output_screen).toBe("off");
    expect(c.spec?.defenses?.output_dlp).toBe("redact");
    expect(c.spec?.defenses?.prompt_injection).toBe("off");
  });

  it("does not mutate the input manifest", () => {
    const frozen: AgentManifest = {
      spec: { defenses: { output_dlp: "redact" } },
    };
    const snapshot = JSON.stringify(frozen);
    setOutputScreen(frozen, "off");
    expect(JSON.stringify(frozen)).toBe(snapshot);
  });
});

describe("run budget (workflow.max_iterations + policies.max_no_progress/run_deadline_s + stream/idle deadlines)", () => {
  it("workflow.max_iterations projects and round-trips", () => {
    const m = parse(`spec:\n  workflow:\n    max_iterations: 40\n    type: react\n`);
    expect(readRunBudget(m).maxIterations).toBe(40);
    const next = patchRunBudget(m, { maxIterations: 50 });
    expect(next.spec?.workflow?.max_iterations).toBe(50);
    expect(next.spec?.workflow?.type).toBe("react"); // 未投影键保留
  });

  it("max_no_progress round-trips under policies", () => {
    const m = parse(
      `spec:\n  policies:\n    max_no_progress: 3\n    approval_timeout_s: 3600\n`,
    );
    expect(readRunBudget(m).maxNoProgress).toBe(3);
    const next = patchRunBudget(m, { maxNoProgress: 7 });
    expect(next.spec?.policies?.max_no_progress).toBe(7);
    // 未投影键保留
    expect(next.spec?.policies?.approval_timeout_s).toBe(3600);
  });

  it("absent workflow stays absent until set", () => {
    const m = parse(`spec:\n  policies:\n    approval_timeout_s: 3600\n`);
    // patch 未设值 (maxIterations) 不产生空块
    const next = patchRunBudget(m, { maxNoProgress: 5 });
    expect(next.spec?.workflow).toBeUndefined();
    expect(next.spec?.policies?.max_no_progress).toBe(5);
    expect(next.spec?.policies?.approval_timeout_s).toBe(3600);
  });

  it("patching maxNoProgress preserves unrelated policies keys (approval_required_tools)", () => {
    const base: AgentManifest = {
      spec: {
        policies: { approval_required_tools: ["exec_python"] },
      },
    };
    const next = patchRunBudget(base, { maxNoProgress: 2 });
    expect(next.spec?.policies?.max_no_progress).toBe(2);
    expect(next.spec?.policies?.approval_required_tools).toEqual([
      "exec_python",
    ]);
  });

  it("aggregates run_deadline_s / stream_deadline_s / idle_timeout_s from their existing locations", () => {
    const m = parse(
      `spec:\n  policies:\n    run_deadline_s: 1800\n  stream_deadline_s: 90\n  idle_timeout_s: 30\n`,
    );
    expect(readRunBudget(m)).toEqual({
      maxIterations: undefined,
      maxNoProgress: undefined,
      runDeadlineS: 1800,
      streamDeadlineS: 90,
      idleTimeoutS: 30,
    });
  });

  it("patchRunBudget writes run_deadline_s/stream_deadline_s/idle_timeout_s independently", () => {
    const m: AgentManifest = { spec: {} };
    const next = patchRunBudget(m, {
      runDeadlineS: 600,
      streamDeadlineS: 120,
      idleTimeoutS: 60,
    });
    expect(next.spec?.policies?.run_deadline_s).toBe(600);
    expect(next.spec?.stream_deadline_s).toBe(120);
    expect(next.spec?.idle_timeout_s).toBe(60);
  });

  it("setting a field to undefined removes it from its block, dropping an emptied block", () => {
    const m: AgentManifest = {
      spec: { workflow: { max_iterations: 40 } },
    };
    const cleared = patchRunBudget(m, { maxIterations: undefined });
    expect(cleared.spec?.workflow).toBeUndefined();
  });

  it("does not mutate the input manifest", () => {
    const m: AgentManifest = { spec: { workflow: { max_iterations: 40 } } };
    const snapshot = JSON.stringify(m);
    patchRunBudget(m, { maxIterations: 50 });
    expect(JSON.stringify(m)).toBe(snapshot);
  });
});

describe("context gates (policies.context_compression / working_memory / tool_result_prune / tool_output_budget)", () => {
  it("context_compression fields round-trip through YAML", () => {
    const m: AgentManifest = { spec: {} };
    const next = patchContextGates(m, {
      ccEnabled: false,
      ccThresholdPct: 0.6,
      ccHeadKeep: 2,
      ccTailKeep: 3,
      ccFlushBeforeCompaction: false,
      ccMaxPasses: 5,
      ccMaxTurns: 10,
      ccMaxTokens: 8000,
      ccPressureFeedback: false,
      ccPressureWarnPct: 0.8,
    });
    const yaml = dumpYaml(next);
    expect(yaml).toContain("context_compression:");
    expect(yaml).toContain("threshold_pct: 0.6");
    expect(yaml).toContain("max_tokens: 8000");
    const roundTripped = parse(yaml) as AgentManifest;
    expect(readContextGates(roundTripped)).toMatchObject({
      ccEnabled: false,
      ccThresholdPct: 0.6,
      ccHeadKeep: 2,
      ccTailKeep: 3,
      ccFlushBeforeCompaction: false,
      ccMaxPasses: 5,
      ccMaxTurns: 10,
      ccMaxTokens: 8000,
      ccPressureFeedback: false,
      ccPressureWarnPct: 0.8,
    });
  });

  it("working_memory fields round-trip through YAML", () => {
    const m: AgentManifest = { spec: {} };
    const next = patchContextGates(m, {
      wmEnabled: false,
      wmThresholdPct: 0.5,
      wmMaxRecentTurns: 12,
      wmKeepFirstTurn: false,
    });
    const yaml = dumpYaml(next);
    expect(yaml).toContain("working_memory:");
    expect(yaml).toContain("max_recent_turns: 12");
    const roundTripped = parse(yaml) as AgentManifest;
    expect(readContextGates(roundTripped)).toMatchObject({
      wmEnabled: false,
      wmThresholdPct: 0.5,
      wmMaxRecentTurns: 12,
      wmKeepFirstTurn: false,
    });
  });

  it("tool_result_prune fields round-trip through YAML", () => {
    const m: AgentManifest = { spec: {} };
    const next = patchContextGates(m, {
      prEnabled: false,
      prThresholdPct: 0.9,
      prRecentKept: 2,
    });
    const yaml = dumpYaml(next);
    expect(yaml).toContain("tool_result_prune:");
    expect(yaml).toContain("recent_tool_results_kept: 2");
    const roundTripped = parse(yaml) as AgentManifest;
    expect(readContextGates(roundTripped)).toMatchObject({
      prEnabled: false,
      prThresholdPct: 0.9,
      prRecentKept: 2,
    });
  });

  it("tool_output_budget.enabled round-trips through YAML", () => {
    const m: AgentManifest = { spec: {} };
    const next = patchContextGates(m, { budgetEnabled: false });
    const yaml = dumpYaml(next);
    expect(yaml).toContain("tool_output_budget:");
    expect(yaml).toContain("enabled: false");
    const roundTripped = parse(yaml) as AgentManifest;
    expect(readContextGates(roundTripped).budgetEnabled).toBe(false);
  });

  it("preserves unrelated policies keys (approval_required_tools) when patching", () => {
    const base: AgentManifest = {
      spec: { policies: { approval_required_tools: ["exec_python"] } },
    };
    const next = patchContextGates(base, { ccEnabled: false });
    expect(next.spec?.policies?.approval_required_tools).toEqual([
      "exec_python",
    ]);
    expect(next.spec?.policies?.context_compression).toEqual({
      enabled: false,
    });
  });

  it("explicit undefined deletes a key, dropping the block when emptied", () => {
    const base: AgentManifest = {
      spec: { policies: { context_compression: { enabled: false } } },
    };
    const cleared = patchContextGates(base, { ccEnabled: undefined });
    expect(cleared.spec?.policies?.context_compression).toBeUndefined();
    expect(cleared.spec?.policies).toBeUndefined();
  });

  it("clearing one field preserves siblings in the same sub-block", () => {
    const base: AgentManifest = {
      spec: {
        policies: {
          context_compression: { enabled: false, head_keep: 2 },
        },
      },
    };
    const next = patchContextGates(base, { ccEnabled: undefined });
    expect(next.spec?.policies?.context_compression).toEqual({
      head_keep: 2,
    });
  });

  it("does not materialize an absent parent block for an untouched sub-block", () => {
    const base: AgentManifest = { spec: {} };
    const next = patchContextGates(base, { prEnabled: false });
    expect(next.spec?.policies?.context_compression).toBeUndefined();
    expect(next.spec?.policies?.working_memory).toBeUndefined();
    expect(next.spec?.policies?.tool_output_budget).toBeUndefined();
    expect(next.spec?.policies?.tool_result_prune).toEqual({
      enabled: false,
    });
  });

  it("an empty patch does not materialize policies at all", () => {
    const base: AgentManifest = { spec: {} };
    const next = patchContextGates(base, {});
    expect(next.spec?.policies).toBeUndefined();
  });

  it("does not mutate the input manifest", () => {
    const m: AgentManifest = {
      spec: { policies: { context_compression: { enabled: true } } },
    };
    const snapshot = JSON.stringify(m);
    patchContextGates(m, { ccEnabled: false });
    expect(JSON.stringify(m)).toBe(snapshot);
  });

  it("readContextGates returns undefined for every field on an empty manifest", () => {
    expect(readContextGates({})).toEqual({
      ccEnabled: undefined,
      ccThresholdPct: undefined,
      ccHeadKeep: undefined,
      ccTailKeep: undefined,
      ccFlushBeforeCompaction: undefined,
      ccMaxPasses: undefined,
      ccMaxTurns: undefined,
      ccMaxTokens: undefined,
      ccPressureFeedback: undefined,
      ccPressureWarnPct: undefined,
      wmEnabled: undefined,
      wmThresholdPct: undefined,
      wmMaxRecentTurns: undefined,
      wmKeepFirstTurn: undefined,
      prEnabled: undefined,
      prThresholdPct: undefined,
      prRecentKept: undefined,
      budgetEnabled: undefined,
    });
  });
});

describe("security group (spec.sandbox.network egress + policies.tool_use_enforcement)", () => {
  it("egress round-trips through YAML", () => {
    const m: AgentManifest = { spec: {} };
    const next = patchSecurity(m, { egress: "none" });
    const yaml = dumpYaml(next);
    expect(yaml).toContain("network:");
    expect(yaml).toContain("egress: none");
    const roundTripped = parse(yaml) as AgentManifest;
    expect(readSecurity(roundTripped).egress).toBe("none");
  });

  it("allowlist array round-trips through YAML without aliasing the input array", () => {
    const m: AgentManifest = { spec: {} };
    const input = ["example.com", "api.example.com"];
    const next = patchSecurity(m, { allowlist: input });
    // Mutating the caller's array afterwards must not affect the stored manifest.
    input.push("evil.example.com");
    expect(next.spec?.sandbox?.network?.allowlist).toEqual([
      "example.com",
      "api.example.com",
    ]);
    const yaml = dumpYaml(next);
    const roundTripped = parse(yaml) as AgentManifest;
    expect(readSecurity(roundTripped).allowlist).toEqual([
      "example.com",
      "api.example.com",
    ]);
  });

  it("preserves sandbox's unrelated unknown keys (runtime, resources) when patching egress", () => {
    const base: AgentManifest = {
      spec: {
        sandbox: {
          runtime: "gvisor",
          resources: { cpu: "1.0", memory: "512Mi" },
        },
      },
    };
    const next = patchSecurity(base, { egress: "direct" });
    expect(next.spec?.sandbox?.runtime).toBe("gvisor");
    expect(next.spec?.sandbox?.resources).toEqual({
      cpu: "1.0",
      memory: "512Mi",
    });
    expect(next.spec?.sandbox?.network).toEqual({ egress: "direct" });
  });

  it("explicit undefined deletes egress but keeps the emptied network block (backend requires it)", () => {
    // SandboxSpec.network is a REQUIRED pydantic field with no default —
    // dropping the block from an existing sandbox makes the manifest 422 on
    // deploy. An emptied block must survive as ``network: {}`` (valid: all
    // NetworkSpec fields are defaulted).
    const base: AgentManifest = {
      spec: { sandbox: { network: { egress: "direct" } } },
    };
    const cleared = patchSecurity(base, { egress: undefined });
    expect(cleared.spec?.sandbox?.network).toEqual({});
    expect(cleared.spec?.sandbox).toEqual({ network: {} });
  });

  it("keeps network as {} when it empties while unknown sandbox keys remain", () => {
    const base: AgentManifest = {
      spec: {
        sandbox: {
          runtime: "gvisor",
          network: { egress: "direct" },
        },
      },
    };
    const cleared = patchSecurity(base, { egress: undefined });
    expect(cleared.spec?.sandbox?.network).toEqual({});
    expect(cleared.spec?.sandbox?.runtime).toBe("gvisor");
  });

  it("does not materialize an absent sandbox when the network patch nets out empty", () => {
    const base: AgentManifest = { spec: {} };
    const cleared = patchSecurity(base, { egress: undefined });
    expect(cleared.spec?.sandbox).toBeUndefined();
  });

  it("clearing one network field preserves siblings (allowlist/denylist)", () => {
    const base: AgentManifest = {
      spec: {
        sandbox: {
          network: {
            egress: "proxy",
            allowlist: ["example.com"],
            denylist: ["bad.example.com"],
          },
        },
      },
    };
    const next = patchSecurity(base, { egress: undefined });
    expect(next.spec?.sandbox?.network).toEqual({
      allowlist: ["example.com"],
      denylist: ["bad.example.com"],
    });
  });

  it("does not materialize an absent sandbox block when patch only touches tool_use_enforcement", () => {
    const base: AgentManifest = { spec: {} };
    const next = patchSecurity(base, { toolUseEnforcement: "on" });
    expect(next.spec?.sandbox).toBeUndefined();
    expect(next.spec?.policies?.tool_use_enforcement).toBe("on");
  });

  it("tool_use_enforcement coexists with PR2's four policies sub-blocks without disturbing them", () => {
    const base: AgentManifest = {
      spec: {
        policies: {
          approval_required_tools: ["exec_python"],
          max_no_progress: 3,
          context_compression: { enabled: false },
          working_memory: { enabled: true },
          tool_result_prune: { enabled: true },
          tool_output_budget: { enabled: false },
        },
      },
    };
    const next = patchSecurity(base, { toolUseEnforcement: "off" });
    expect(next.spec?.policies?.tool_use_enforcement).toBe("off");
    expect(next.spec?.policies?.approval_required_tools).toEqual([
      "exec_python",
    ]);
    expect(next.spec?.policies?.max_no_progress).toBe(3);
    expect(next.spec?.policies?.context_compression).toEqual({
      enabled: false,
    });
    expect(next.spec?.policies?.working_memory).toEqual({ enabled: true });
    expect(next.spec?.policies?.tool_result_prune).toEqual({ enabled: true });
    expect(next.spec?.policies?.tool_output_budget).toEqual({
      enabled: false,
    });
    // readRunBudget / readContextGates over the same manifest stay unaffected.
    expect(readRunBudget(next).maxNoProgress).toBe(3);
    expect(readContextGates(next).ccEnabled).toBe(false);
    expect(readContextGates(next).wmEnabled).toBe(true);
  });

  it("explicit undefined deletes tool_use_enforcement, dropping policies if emptied", () => {
    const base: AgentManifest = {
      spec: { policies: { tool_use_enforcement: "on" } },
    };
    const cleared = patchSecurity(base, { toolUseEnforcement: undefined });
    expect(cleared.spec?.policies).toBeUndefined();
  });

  it("an empty patch does not materialize sandbox or policies at all", () => {
    const base: AgentManifest = { spec: {} };
    const next = patchSecurity(base, {});
    expect(next.spec?.sandbox).toBeUndefined();
    expect(next.spec?.policies).toBeUndefined();
  });

  it("does not mutate the input manifest", () => {
    const m: AgentManifest = {
      spec: { sandbox: { runtime: "gvisor", network: { egress: "proxy" } } },
    };
    const snapshot = JSON.stringify(m);
    patchSecurity(m, { egress: "none", toolUseEnforcement: "on" });
    expect(JSON.stringify(m)).toBe(snapshot);
  });

  it("readSecurity returns undefined for every field on an empty manifest", () => {
    expect(readSecurity({})).toEqual({
      egress: undefined,
      allowlist: undefined,
      denylist: undefined,
      toolUseEnforcement: undefined,
    });
  });
});

describe("sandbox filesystem group (spec.sandbox.filesystem.persistent_workspace)", () => {
  it("persistentWorkspace true round-trips through YAML", () => {
    const m: AgentManifest = { spec: {} };
    const next = patchSandboxFs(m, { persistentWorkspace: true });
    const yaml = dumpYaml(next);
    expect(yaml).toContain("filesystem:");
    expect(yaml).toContain("persistent_workspace: true");
    const roundTripped = parse(yaml) as AgentManifest;
    expect(readSandboxFs(roundTripped).persistentWorkspace).toBe(true);
  });

  it("explicit undefined deletes persistent_workspace but keeps the emptied filesystem block (backend requires it)", () => {
    // SandboxSpec.filesystem is a REQUIRED pydantic field with no default —
    // dropping the block from an existing sandbox makes the manifest 422 on
    // deploy (mirrors patchSecurity's network handling, hotfix #1017). An
    // emptied block must survive as ``filesystem: {}``.
    const base: AgentManifest = {
      spec: { sandbox: { filesystem: { persistent_workspace: true } } },
    };
    const cleared = patchSandboxFs(base, { persistentWorkspace: undefined });
    expect(cleared.spec?.sandbox?.filesystem).toEqual({});
    expect(cleared.spec?.sandbox).toEqual({ filesystem: {} });
  });

  it("clearing persistent_workspace preserves sandbox's unrelated unknown keys (runtime, resources)", () => {
    const base: AgentManifest = {
      spec: {
        sandbox: {
          runtime: "gvisor",
          resources: { cpu: "1.0" },
          filesystem: { persistent_workspace: true },
        },
      },
    };
    const cleared = patchSandboxFs(base, { persistentWorkspace: undefined });
    expect(cleared.spec?.sandbox?.filesystem).toEqual({});
    expect(cleared.spec?.sandbox?.runtime).toBe("gvisor");
    expect(cleared.spec?.sandbox?.resources).toEqual({ cpu: "1.0" });
  });

  it("preserves filesystem's own unrelated unknown keys (readonly_root, writable) when patching persistent_workspace", () => {
    const base: AgentManifest = {
      spec: {
        sandbox: {
          filesystem: { readonly_root: true, writable: ["/tmp", "/workspace"] },
        },
      },
    };
    const next = patchSandboxFs(base, { persistentWorkspace: true });
    expect(next.spec?.sandbox?.filesystem).toEqual({
      readonly_root: true,
      writable: ["/tmp", "/workspace"],
      persistent_workspace: true,
    });
  });

  it("does not materialize an absent sandbox when the filesystem patch nets out empty", () => {
    const base: AgentManifest = { spec: {} };
    const cleared = patchSandboxFs(base, { persistentWorkspace: undefined });
    expect(cleared.spec?.sandbox).toBeUndefined();
  });

  it("an empty patch does not materialize sandbox at all", () => {
    const base: AgentManifest = { spec: {} };
    const next = patchSandboxFs(base, {});
    expect(next.spec?.sandbox).toBeUndefined();
  });

  it("coexists with patchSecurity's network projection without disturbing each other", () => {
    const base: AgentManifest = { spec: {} };
    const withNetwork = patchSecurity(base, { egress: "none" });
    const withBoth = patchSandboxFs(withNetwork, { persistentWorkspace: true });
    expect(withBoth.spec?.sandbox?.network).toEqual({ egress: "none" });
    expect(withBoth.spec?.sandbox?.filesystem).toEqual({
      persistent_workspace: true,
    });

    // Same coexistence when applied in the opposite order.
    const withFilesystem = patchSandboxFs(base, { persistentWorkspace: true });
    const withBothReversed = patchSecurity(withFilesystem, { egress: "none" });
    expect(withBothReversed.spec?.sandbox?.network).toEqual({ egress: "none" });
    expect(withBothReversed.spec?.sandbox?.filesystem).toEqual({
      persistent_workspace: true,
    });
  });

  it("does not mutate the input manifest", () => {
    const m: AgentManifest = {
      spec: {
        sandbox: {
          runtime: "gvisor",
          filesystem: { persistent_workspace: false },
        },
      },
    };
    const snapshot = JSON.stringify(m);
    patchSandboxFs(m, { persistentWorkspace: true });
    expect(JSON.stringify(m)).toBe(snapshot);
  });

  it("readSandboxFs returns undefined for persistentWorkspace on an empty manifest", () => {
    expect(readSandboxFs({})).toEqual({ persistentWorkspace: undefined });
  });
});
