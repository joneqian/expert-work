import { describe, expect, it, test } from "vitest";
import {
  readApprovalTools,
  readDescription,
  readDynamicWorkersOn,
  readMemoryOn,
  readModel,
  readName,
  readReflectionEvaluator,
  readReflectionEvaluatorOn,
  readSystemPrompt,
  readTools,
  readTopK,
  readVisionModel,
  readVisionOn,
  setDescription,
  setMcpAllowTools,
  setMcpServers,
  setMemoryOn,
  setModel,
  setName,
  setApprovalTools,
  setDynamicWorkersOn,
  setReflectionEvaluator,
  setSystemPrompt,
  setTool,
  setTopK,
  setVisionModel,
} from "../form_model";

const seed = {
  apiVersion: "helix.io/v1",
  kind: "Agent",
  metadata: { name: "my-agent", version: "1.0.0", tenant: "my-tenant" },
  spec: {
    model: { provider: "anthropic", name: "claude-sonnet-4-6" },
    system_prompt: { template: "You are helpful." },
    memory: { long_term: { retrieve_top_k: 5, write_back: true, recall_mode: "per_session" } },
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

describe("form_model writers preserve siblings", () => {
  it("setName updates name and preserves apiVersion + sandbox", () => {
    const next = setName(seed, "x");
    expect(next.metadata?.name).toBe("x");
    expect(next.apiVersion).toBe("helix.io/v1");
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

const withMcp = () => setTool({ apiVersion: "v1", kind: "Agent", spec: {} }, "mcp", true);

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

test("setMcpServers no-ops when there is no mcp tool", () => {
  const m = setMcpServers({ apiVersion: "v1", kind: "Agent", spec: {} }, ["github"]);
  expect(readTools(m).mcp).toBe(false);
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
    const m = setReflectionEvaluator(seed, { provider: "openai", name: "gpt-4o-mini" });
    expect(m.spec?.routing?.rules).toEqual([
      { when: "reflection", model: { provider: "openai", name: "gpt-4o-mini" } },
    ]);
    expect(readReflectionEvaluator(m)?.name).toBe("gpt-4o-mini");
    expect(readReflectionEvaluatorOn(m)).toBe(true);
  });

  it("keeps a partial pick (provider only) so the picker doesn't lose state", () => {
    const m = setReflectionEvaluator(seed, { provider: "openai" });
    expect(readReflectionEvaluator(m)).toEqual({ provider: "openai" });
  });

  it("clearing removes the rule and drops empty routing", () => {
    const withRule = setReflectionEvaluator(seed, { provider: "openai", name: "gpt-4o-mini" });
    const cleared = setReflectionEvaluator(withRule, null);
    expect(readReflectionEvaluator(cleared)).toBeUndefined();
    expect(cleared.spec?.routing).toBeUndefined();
  });

  it("preserves a sibling planning rule when setting/clearing reflection", () => {
    const base = {
      ...seed,
      spec: {
        ...seed.spec,
        routing: { rules: [{ when: "planning", model: { provider: "anthropic", name: "claude-opus-4-8" } }] },
      },
    };
    const set = setReflectionEvaluator(base, { provider: "openai", name: "gpt-4o-mini" });
    expect(set.spec?.routing?.rules).toHaveLength(2);
    const cleared = setReflectionEvaluator(set, null);
    // planning survives; only reflection removed; routing stays (still has planning).
    expect(cleared.spec?.routing?.rules).toEqual([
      { when: "planning", model: { provider: "anthropic", name: "claude-opus-4-8" } },
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
    expect(m.spec?.policies?.approval_required_tools).toEqual(["exec_python", "http"]);
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
      spec: { ...seed.spec, policies: { approval_required_tools: ["bash"], approval_timeout_s: 3600 } },
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
    expect((seed.spec as { dynamic_workers?: unknown }).dynamic_workers).toBeUndefined();
  });
});

describe("vision fallback (Stream J.6 Path B — vision block)", () => {
  it("reads undefined when no vision block", () => {
    expect(readVisionModel(seed)).toBeUndefined();
    expect(readVisionOn(seed)).toBe(false);
  });

  it("writes a vision.model and reads it back", () => {
    const m = setVisionModel(seed, { provider: "qwen", name: "qwen-vl-max" });
    expect(m.spec?.vision?.model).toEqual({ provider: "qwen", name: "qwen-vl-max" });
    expect(readVisionModel(m)?.name).toBe("qwen-vl-max");
    expect(readVisionOn(m)).toBe(true);
  });

  it("clearing removes the vision block", () => {
    const withVl = setVisionModel(seed, { provider: "qwen", name: "qwen-vl-max" });
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
    const swapped = setVisionModel(base, { provider: "qwen", name: "qwen-vl-plus" });
    expect(swapped.spec?.vision?.model?.name).toBe("qwen-vl-plus");
    expect(swapped.spec?.vision?.fallbacks).toEqual([{ provider: "zhipu", name: "glm-4v" }]);
  });

  it("does not mutate the input manifest", () => {
    setVisionModel(seed, { provider: "qwen", name: "qwen-vl-max" });
    expect((seed.spec as { vision?: unknown }).vision).toBeUndefined();
  });
});
