import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "../../../i18n";

import { PromptVariablesEditor } from "../PromptVariablesEditor";
import type { AgentManifest, PromptVariableFields } from "../form_model";

const SEED: AgentManifest = {
  apiVersion: "helix/v1",
  kind: "Agent",
  metadata: { name: "bot" },
  spec: { system_prompt: { template: "hi {{ persona }}" } },
};

function jinjaSeed(variables: PromptVariableFields[]): AgentManifest {
  return {
    ...SEED,
    spec: {
      system_prompt: { template: "hi {{ persona }}", jinja: true, variables },
    },
  };
}

describe("PromptVariablesEditor", () => {
  it("renders the jinja toggle; variable rows hidden until enabled", () => {
    render(<PromptVariablesEditor formData={SEED} onChange={vi.fn()} />);
    expect(screen.getByTestId("af-prompt-jinja")).toBeInTheDocument();
    expect(screen.queryByTestId("af-prompt-var-add")).not.toBeInTheDocument();
  });

  it("toggling jinja on emits jinja:true", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<PromptVariablesEditor formData={SEED} onChange={onChange} />);
    await user.click(screen.getByTestId("af-prompt-jinja"));
    const last = onChange.mock.calls.at(-1)?.[0] as AgentManifest;
    expect(last.spec?.system_prompt?.jinja).toBe(true);
  });

  it("shows the variable editor + add button when jinja is on", () => {
    render(
      <PromptVariablesEditor formData={jinjaSeed([])} onChange={vi.fn()} />,
    );
    expect(screen.getByTestId("af-prompt-var-add")).toBeInTheDocument();
  });

  it("adding a variable appends a trusted+required row", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <PromptVariablesEditor formData={jinjaSeed([])} onChange={onChange} />,
    );
    await user.click(screen.getByTestId("af-prompt-var-add"));
    const last = onChange.mock.calls.at(-1)?.[0] as AgentManifest;
    expect(last.spec?.system_prompt?.variables).toEqual([
      { name: "", trusted: true, required: true, description: "" },
    ]);
  });

  it("editing a variable name patches that row", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <PromptVariablesEditor
        formData={jinjaSeed([{ name: "", trusted: true, required: true }])}
        onChange={onChange}
      />,
    );
    await user.type(screen.getByTestId("af-prompt-var-name-0"), "p");
    const last = onChange.mock.calls.at(-1)?.[0] as AgentManifest;
    expect(last.spec?.system_prompt?.variables?.[0].name).toBe("p");
  });

  it("toggling trusted off patches the row", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <PromptVariablesEditor
        formData={jinjaSeed([
          { name: "profile", trusted: true, required: true },
        ])}
        onChange={onChange}
      />,
    );
    await user.click(screen.getByTestId("af-prompt-var-trusted-0"));
    const last = onChange.mock.calls.at(-1)?.[0] as AgentManifest;
    expect(last.spec?.system_prompt?.variables?.[0].trusted).toBe(false);
  });
});
