import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "../../../i18n";

import * as catalog from "../catalog";
import { FormView, type FormSection } from "../FormView";
import type { AgentManifest } from "../form_model";

// The MCP tab mounts McpToolPicker, which loads servers on mount.
vi.mock("../../../api/mcp-servers", () => ({
  listAvailableMcpServers: vi.fn().mockResolvedValue([]),
  listMcpServerTools: vi.fn().mockResolvedValue([]),
}));
vi.mock("../../../api/mcp-catalog", () => ({
  listPlatformCatalog: vi.fn().mockResolvedValue([]),
  listCatalogTools: vi.fn().mockResolvedValue({ status: "ok", tools: [] }),
}));

vi.spyOn(catalog, "loadModelCatalog").mockResolvedValue({
  providers: [
    {
      provider: "openai",
      models: [
        {
          name: "gpt-4o",
          vision: true,
          embeddings: false,
          context_window: 128000,
          deprecated: false,
        },
      ],
    },
  ],
});

const SEED: AgentManifest = {
  apiVersion: "helix/v1",
  kind: "Agent",
  metadata: { name: "bot" },
  spec: {
    model: { provider: "openai", name: "gpt-4o" },
    system_prompt: { template: "hi" },
    memory: {
      long_term: {
        retrieve_top_k: 5,
        write_back: true,
        recall_mode: "per_session",
      },
    },
    sandbox: { kind: "none" },
  },
};

function renderSection(
  section: FormSection,
  formData: AgentManifest = SEED,
  onChange: (d: unknown) => void = vi.fn(),
) {
  return render(
    <FormView formData={formData} onChange={onChange} section={section} />,
  );
}

describe("FormView", () => {
  it("renders each section's testids under its tab", () => {
    renderSection("basic");
    expect(screen.getByTestId("af-basic")).toBeInTheDocument();

    renderSection("model");
    expect(screen.getByTestId("af-model")).toBeInTheDocument();
    expect(screen.getByTestId("af-reflection-evaluator")).toBeInTheDocument();

    renderSection("prompt");
    expect(screen.getByTestId("af-prompt")).toBeInTheDocument();

    renderSection("tools");
    expect(screen.getByTestId("af-tools")).toBeInTheDocument();
    // MCP is its own section now, not under tools — and there's no longer a
    // separate "MCP 工具" enable checkbox (selecting a server enables MCP).
    expect(screen.queryByTestId("af-tool-mcp")).not.toBeInTheDocument();

    renderSection("mcp");
    expect(screen.getByTestId("af-mcp")).toBeInTheDocument();
    expect(screen.queryByTestId("af-tool-mcp")).not.toBeInTheDocument();

    renderSection("knowledge");
    expect(screen.getByTestId("af-knowledge")).toBeInTheDocument();

    renderSection("skills");
    expect(screen.getByTestId("af-skills")).toBeInTheDocument();

    renderSection("subagents");
    expect(screen.getByTestId("af-subagents")).toBeInTheDocument();

    renderSection("memory");
    expect(screen.getByTestId("af-memory")).toBeInTheDocument();

    renderSection("governance");
    expect(screen.getByTestId("af-approval")).toBeInTheDocument();
    expect(screen.getByTestId("af-dynamic-workers")).toBeInTheDocument();
  });

  it("checking an approval tool adds it to policies.approval_required_tools", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderSection("governance", SEED, onChange);
    await user.click(screen.getByTestId("af-approval-exec_python"));
    const last = onChange.mock.calls.at(-1)?.[0] as AgentManifest;
    expect(last.spec?.policies?.approval_required_tools).toEqual([
      "exec_python",
    ]);
  });

  it("turning dynamic workers off writes dynamic_workers.enabled=false", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderSection("governance", SEED, onChange);
    await user.click(screen.getByTestId("af-dynamic-workers-toggle"));
    const last = onChange.mock.calls.at(-1)?.[0] as AgentManifest;
    expect(last.spec?.dynamic_workers?.enabled).toBe(false);
  });

  it("shows the vision (VL fallback) section when the main model is text-only", () => {
    // SEED's model has no supports_vision → Path B section appears.
    renderSection("model");
    expect(screen.getByTestId("af-vision")).toBeInTheDocument();
  });

  it("hides the vision section when the main model is vision-capable", () => {
    const visionModel: AgentManifest = {
      ...SEED,
      spec: {
        ...SEED.spec,
        model: { provider: "openai", name: "gpt-4o", supports_vision: true },
      },
    };
    renderSection("model", visionModel);
    expect(screen.queryByTestId("af-vision")).not.toBeInTheDocument();
  });

  it("hides the evaluator clear button until an independent evaluator is set", () => {
    renderSection("model");
    expect(
      screen.queryByTestId("af-reflection-evaluator-clear"),
    ).not.toBeInTheDocument();
  });

  it("shows the clear button and removes the routing rule when cleared", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const withEvaluator: AgentManifest = {
      ...SEED,
      spec: {
        ...SEED.spec,
        routing: {
          rules: [
            {
              when: "reflection",
              model: { provider: "openai", name: "gpt-4o" },
            },
          ],
        },
      },
    };
    renderSection("model", withEvaluator, onChange);
    const clear = screen.getByTestId("af-reflection-evaluator-clear");
    expect(clear).toBeInTheDocument();
    await user.click(clear);
    const last = onChange.mock.calls.at(-1)?.[0] as AgentManifest;
    expect(last.spec?.routing).toBeUndefined();
  });

  it("typing the name emits a merged manifest preserving non-curated fields", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderSection("basic", SEED, onChange);
    const input = screen
      .getByTestId("af-name")
      .querySelector("input") as HTMLInputElement;
    await user.type(input, "X");
    const last = onChange.mock.calls.at(-1)?.[0] as AgentManifest;
    expect(last.metadata?.name).toBe("botX");
    expect(last.apiVersion).toBe("helix/v1");
    expect(last.spec?.sandbox).toEqual({ kind: "none" });
  });

  it("toggling memory off sets spec.memory.long_term to null", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderSection("memory", SEED, onChange);
    await user.click(screen.getByTestId("af-memory-toggle"));
    const last = onChange.mock.calls.at(-1)?.[0] as AgentManifest;
    expect(last.spec?.memory?.long_term).toBeNull();
  });

  it("checking web search adds a builtin web_search tool entry", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderSection("tools", SEED, onChange);
    await user.click(screen.getByTestId("af-tool-web_search"));
    const last = onChange.mock.calls.at(-1)?.[0] as AgentManifest;
    expect(last.spec?.tools).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ type: "builtin", name: "web_search" }),
      ]),
    );
  });

  it("editing the prompt updates spec.system_prompt.template", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderSection("prompt", SEED, onChange);
    const ta = screen
      .getByTestId("af-prompt-input")
      .querySelector("textarea") as HTMLTextAreaElement;
    await user.type(ta, "!");
    const last = onChange.mock.calls.at(-1)?.[0] as AgentManifest;
    expect(last.spec?.system_prompt?.template).toBe("hi!");
  });

  it("loads the model catalog", async () => {
    renderSection("model");
    await waitFor(() => expect(catalog.loadModelCatalog).toHaveBeenCalled());
  });
});
