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

import * as schemaSdk from "../../../api/manifest_schema";
import { __resetSchemaCacheForTest } from "../schema";
import { ManifestEditor } from "../ManifestEditor";

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
  it("loads the schema and shows the Form tab by default", async () => {
    render(
      <ManifestEditor mode="create" initialYaml={SEED} onChange={vi.fn()} />,
    );
    await waitFor(() =>
      expect(screen.getByTestId("manifest-form-view")).toBeInTheDocument(),
    );
  });

  it("switching to YAML shows the dumped manifest", async () => {
    const user = userEvent.setup();
    render(
      <ManifestEditor mode="create" initialYaml={SEED} onChange={vi.fn()} />,
    );
    await screen.findByTestId("manifest-form-view");
    await user.click(screen.getByTestId("manifest-tab-yaml"));
    const ta = screen.getByTestId("monaco-stub") as HTMLTextAreaElement;
    expect(ta.value).toContain("name: bot");
  });

  it("blocks the YAML→Form switch when YAML is invalid against the schema", async () => {
    const user = userEvent.setup();
    render(
      <ManifestEditor mode="create" initialYaml={SEED} onChange={vi.fn()} />,
    );
    await screen.findByTestId("manifest-form-view");
    await user.click(screen.getByTestId("manifest-tab-yaml"));

    const ta = screen.getByTestId("monaco-stub") as HTMLTextAreaElement;
    await user.clear(ta);
    await user.type(ta, "metadata:\n  notname: x");

    await user.click(screen.getByTestId("manifest-tab-basic"));
    expect(screen.getByTestId("manifest-switch-error")).toBeInTheDocument();
    expect(screen.queryByTestId("manifest-form-view")).not.toBeInTheDocument();
  });

  it("emits the latest YAML through onChange on raw edits", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <ManifestEditor mode="create" initialYaml={SEED} onChange={onChange} />,
    );
    await screen.findByTestId("manifest-form-view");
    await user.click(screen.getByTestId("manifest-tab-yaml"));
    const ta = screen.getByTestId("monaco-stub") as HTMLTextAreaElement;
    await user.clear(ta);
    await user.type(ta, "metadata:\n  name: edited");
    expect(onChange).toHaveBeenLastCalledWith(
      expect.stringContaining("edited"),
    );
  });

  it("blocks the YAML→Form switch when YAML is syntactically broken", async () => {
    const user = userEvent.setup();
    render(
      <ManifestEditor mode="create" initialYaml={SEED} onChange={vi.fn()} />,
    );
    await screen.findByTestId("manifest-form-view");
    await user.click(screen.getByTestId("manifest-tab-yaml"));

    const ta = screen.getByTestId("monaco-stub") as HTMLTextAreaElement;
    await user.clear(ta);
    // "[[" is userEvent's escape for a literal "[", yielding "metadata: [unterminated"
    // (an unterminated flow sequence) which js-yaml v4 rejects, exercising the parse-throw branch.
    await user.type(ta, "metadata: [[unterminated");

    await user.click(screen.getByTestId("manifest-tab-basic"));
    expect(screen.getByTestId("manifest-switch-error")).toBeInTheDocument();
    expect(screen.queryByTestId("manifest-form-view")).not.toBeInTheDocument();
  });

  it("emits the dumped YAML through onChange when switching Form→YAML", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <ManifestEditor mode="create" initialYaml={SEED} onChange={onChange} />,
    );
    await screen.findByTestId("manifest-form-view");
    await user.click(screen.getByTestId("manifest-tab-yaml"));
    expect(onChange).toHaveBeenLastCalledWith(
      expect.stringContaining("name: bot"),
    );
  });

  it("a leading tab with mergeSection folds in that manifest section + drops its tab", async () => {
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
    expect(screen.getByTestId("af-basic")).toBeInTheDocument();
    // ``bare`` drops the manifest description so it doesn't duplicate the
    // leading tab's own description field.
    expect(screen.queryByTestId("af-description")).not.toBeInTheDocument();
    // "basic" is no longer a standalone tab; the leading "meta" tab replaces it.
    expect(screen.getByTestId("manifest-tab-meta")).toBeInTheDocument();
    expect(screen.queryByTestId("manifest-tab-basic")).not.toBeInTheDocument();
  });
});
