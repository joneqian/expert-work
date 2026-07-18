import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "../../../i18n";

vi.mock("@monaco-editor/react", () => {
  const Editor = ({
    value,
    onChange,
  }: {
    value?: string;
    onChange?: (v: string | undefined) => void;
  }) => (
    <textarea
      data-testid="monaco-stub"
      value={value}
      onChange={(e) => onChange?.(e.target.value)}
    />
  );
  return { default: Editor };
});

// The "capabilities" group mounts McpToolPicker, which loads servers on mount.
vi.mock("../../../api/mcp-servers", () => ({
  listAvailableMcpServers: vi.fn().mockResolvedValue([]),
  listMcpServerTools: vi.fn().mockResolvedValue([]),
}));
vi.mock("../../../api/mcp-catalog", () => ({
  listPlatformCatalog: vi.fn().mockResolvedValue([]),
  listCatalogTools: vi.fn().mockResolvedValue({ status: "ok", tools: [] }),
}));

import * as schemaSdk from "../../../api/manifest_schema";
import { __resetSchemaCacheForTest } from "../schema";
import { ManifestEditor } from "../ManifestEditor";
import en from "../../../i18n/locales/en";

const SCHEMA = {
  type: "object",
  required: ["metadata"],
  properties: {
    metadata: {
      type: "object",
      required: ["name"],
      properties: { name: { type: "string", title: "Name" } },
    },
  },
};

const SEED = "metadata:\n  name: bot\n";

beforeEach(() => {
  __resetSchemaCacheForTest();
  vi.spyOn(schemaSdk, "fetchAgentSchema").mockResolvedValue(SCHEMA);
});
afterEach(() => vi.restoreAllMocks());

describe("ManifestEditor", () => {
  it("renders the group-nav tree + detail pane and shows the 'basic' group by default", async () => {
    render(
      <ManifestEditor mode="create" initialYaml={SEED} onChange={vi.fn()} />,
    );
    await waitFor(() =>
      expect(screen.getByTestId("af-basic")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("cfg-nav")).toBeInTheDocument();
    expect(screen.getByTestId("cfg-pane")).toBeInTheDocument();
  });

  it("selecting the capabilities group stacks tools/mcp/knowledge/skills/subagents in one pane", async () => {
    const user = userEvent.setup();
    render(
      <ManifestEditor mode="create" initialYaml={SEED} onChange={vi.fn()} />,
    );
    await screen.findByTestId("af-basic");
    await user.click(screen.getByTestId("cfg-nav-capabilities"));
    expect(screen.getByTestId("af-tools")).toBeInTheDocument();
    expect(screen.getByTestId("af-mcp")).toBeInTheDocument();
    expect(screen.getByTestId("af-knowledge")).toBeInTheDocument();
    expect(screen.getByTestId("af-skills")).toBeInTheDocument();
    expect(screen.getByTestId("af-subagents")).toBeInTheDocument();
    // "basic" is no longer shown once another group is active.
    expect(screen.queryByTestId("af-basic")).not.toBeInTheDocument();
  });

  it("a group with no curated sections yet (Phase 2) shows the pending hint", async () => {
    const user = userEvent.setup();
    render(
      <ManifestEditor mode="create" initialYaml={SEED} onChange={vi.fn()} />,
    );
    await screen.findByTestId("af-basic");
    await user.click(screen.getByTestId("cfg-nav-budget"));
    expect(screen.getByTestId("cfg-pane-pending")).toHaveTextContent(
      en.manifest_editor.group_pending_hint,
    );
  });

  it("YAML toggle round-trips: Form→YAML dumps the manifest, YAML→Form parses it back", async () => {
    const user = userEvent.setup();
    render(
      <ManifestEditor mode="create" initialYaml={SEED} onChange={vi.fn()} />,
    );
    await screen.findByTestId("af-basic");

    await user.click(screen.getByTestId("cfg-yaml-toggle"));
    const ta = screen.getByTestId("monaco-stub") as HTMLTextAreaElement;
    expect(ta.value).toContain("name: bot");
    expect(screen.queryByTestId("manifest-form-view")).not.toBeInTheDocument();

    await user.click(screen.getByTestId("cfg-yaml-toggle"));
    expect(screen.getByTestId("af-basic")).toBeInTheDocument();
    expect(screen.queryByTestId("monaco-stub")).not.toBeInTheDocument();
  });

  it("refuses the YAML→Form switch when YAML is invalid against the schema, staying on YAML", async () => {
    const user = userEvent.setup();
    render(
      <ManifestEditor mode="create" initialYaml={SEED} onChange={vi.fn()} />,
    );
    await screen.findByTestId("af-basic");
    await user.click(screen.getByTestId("cfg-yaml-toggle"));

    const ta = screen.getByTestId("monaco-stub") as HTMLTextAreaElement;
    await user.clear(ta);
    await user.type(ta, "metadata:\n  notname: x");

    await user.click(screen.getByTestId("cfg-yaml-toggle"));
    expect(screen.getByTestId("manifest-switch-error")).toBeInTheDocument();
    expect(screen.getByTestId("monaco-stub")).toBeInTheDocument();
    expect(screen.queryByTestId("manifest-form-view")).not.toBeInTheDocument();
  });

  it("refuses the YAML→Form switch when YAML is syntactically broken", async () => {
    const user = userEvent.setup();
    render(
      <ManifestEditor mode="create" initialYaml={SEED} onChange={vi.fn()} />,
    );
    await screen.findByTestId("af-basic");
    await user.click(screen.getByTestId("cfg-yaml-toggle"));

    const ta = screen.getByTestId("monaco-stub") as HTMLTextAreaElement;
    await user.clear(ta);
    // "[[" is userEvent's escape for a literal "[", yielding "metadata: [unterminated"
    // (an unterminated flow sequence) which js-yaml v4 rejects, exercising the parse-throw branch.
    await user.type(ta, "metadata: [[unterminated");

    await user.click(screen.getByTestId("cfg-yaml-toggle"));
    expect(screen.getByTestId("manifest-switch-error")).toBeInTheDocument();
    expect(screen.queryByTestId("manifest-form-view")).not.toBeInTheDocument();
  });

  it("strips an incomplete fallback entry when serializing to YAML", async () => {
    const user = userEvent.setup();
    const withEmptyFallback =
      "metadata:\n  name: bot\n" +
      "spec:\n  model:\n    provider: openai\n    name: gpt-4o\n" +
      "    fallback:\n      - {}\n";
    render(
      <ManifestEditor
        mode="create"
        initialYaml={withEmptyFallback}
        onChange={vi.fn()}
      />,
    );
    await screen.findByTestId("af-basic");
    await user.click(screen.getByTestId("cfg-yaml-toggle"));
    const ta = screen.getByTestId("monaco-stub") as HTMLTextAreaElement;
    // The empty entry is pruned; the now-empty fallback key drops out entirely.
    expect(ta.value).not.toContain("fallback");
    expect(ta.value).toContain("name: gpt-4o");
  });

  it("emits the latest YAML through onChange on raw edits", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <ManifestEditor mode="create" initialYaml={SEED} onChange={onChange} />,
    );
    await screen.findByTestId("af-basic");
    await user.click(screen.getByTestId("cfg-yaml-toggle"));
    const ta = screen.getByTestId("monaco-stub") as HTMLTextAreaElement;
    await user.clear(ta);
    await user.type(ta, "metadata:\n  name: edited");
    expect(onChange).toHaveBeenLastCalledWith(
      expect.stringContaining("edited"),
    );
  });

  it("emits the dumped YAML through onChange when toggling Form→YAML", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <ManifestEditor mode="create" initialYaml={SEED} onChange={onChange} />,
    );
    await screen.findByTestId("af-basic");
    await user.click(screen.getByTestId("cfg-yaml-toggle"));
    expect(onChange).toHaveBeenLastCalledWith(
      expect.stringContaining("name: bot"),
    );
  });

  it("shows a leading tab as the top tree node, active by default, mounted (hidden) across group switches", async () => {
    const user = userEvent.setup();
    render(
      <ManifestEditor
        mode="create"
        initialYaml={SEED}
        onChange={vi.fn()}
        leadingTabs={[
          {
            value: "meta",
            label: "Basic info",
            content: <div data-testid="meta-form">meta</div>,
          },
        ]}
      />,
    );
    // Leading tab is active by default.
    await screen.findByTestId("meta-form");
    expect(screen.getByTestId("cfg-nav-meta")).toBeInTheDocument();

    // Switching to a manifest group hides — but does not unmount — the
    // leading pane, so any embedded antd Form keeps its state.
    await user.click(screen.getByTestId("cfg-nav-memory"));
    await screen.findByTestId("af-memory");
    const leadingPane = screen.getByTestId("manifest-leading-meta");
    expect(leadingPane).toHaveStyle({ display: "none" });
    expect(screen.getByTestId("meta-form")).toBeInTheDocument();

    // Switching back re-shows it without remounting.
    await user.click(screen.getByTestId("cfg-nav-meta"));
    expect(screen.getByTestId("manifest-leading-meta")).toHaveStyle({
      display: "block",
    });
    expect(screen.queryByTestId("af-memory")).not.toBeInTheDocument();
  });

  it("a leading tab with mergeSection folds that manifest section in and de-dupes it from its mapped group", async () => {
    const user = userEvent.setup();
    render(
      <ManifestEditor
        mode="create"
        initialYaml={SEED}
        onChange={vi.fn()}
        leadingTabs={[
          {
            value: "meta",
            label: "Basic info",
            content: <div data-testid="meta-form">meta</div>,
            mergeSection: "basic",
          },
        ]}
      />,
    );
    // The merged leading tab is active by default and shows BOTH the caller's
    // content and the manifest's basic section.
    await screen.findByTestId("meta-form");
    expect(screen.getAllByTestId("af-basic")).toHaveLength(1);
    // ``bare`` drops the manifest description so it doesn't duplicate the
    // leading tab's own description field.
    expect(screen.queryByTestId("af-description")).not.toBeInTheDocument();

    // Switching to the "basic" group must NOT render the section a second
    // time — its only instance lives in the (now-hidden) leading pane.
    await user.click(screen.getByTestId("cfg-nav-basic"));
    expect(screen.getAllByTestId("af-basic")).toHaveLength(1);
  });
});
