import { describe, expect, it, vi } from "vitest";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "../../../../i18n";

import { SecuritySection } from "../SecuritySection";
import type { AgentManifest } from "../../form_model";

// The four new curated fields (sandbox egress + tool-use enforcement) —
// mirrors ``SecurityFields`` in form_model.ts.
const FIELD_IDS = [
  "sandbox.network.egress",
  "sandbox.network.allowlist",
  "sandbox.network.denylist",
  "policies.tool_use_enforcement",
];

function renderSection(
  formData: AgentManifest = {},
  onChange: (d: unknown) => void = vi.fn(),
) {
  return render(<SecuritySection formData={formData} onChange={onChange} />);
}

function rowFor(fieldId: string): HTMLElement {
  return document.querySelector(`[data-field-id="${fieldId}"]`) as HTMLElement;
}

// Fields in a collapsed panel are mounted (``forceRender``) but not in the
// accessibility tree, so ``getByRole`` can't reach them until the panel is
// opened — matches how a real user would interact with the section (mirrors
// ``ContextGatesSection.test.tsx``'s helper of the same shape).
async function openPanel(
  user: ReturnType<typeof userEvent.setup>,
  label: string,
): Promise<void> {
  const header = within(document.body)
    .getByText(label, { selector: ".ant-collapse-header-text" })
    .closest(".ant-collapse-header") as HTMLElement;
  await user.click(header);
}

/**
 * In jsdom, Antd's Select renders each option twice: a visible, clickable
 * ``.ant-select-item-option`` div and a hidden ARIA ``role="option"`` mirror
 * with the same text. This opens the given combobox and clicks the real
 * ``.ant-select-item-option-content`` carrying the requested label (mirrors
 * ``field_defs.test.tsx``'s helper of the same shape).
 */
async function pickOption(
  user: ReturnType<typeof userEvent.setup>,
  combobox: HTMLElement,
  label: string,
): Promise<void> {
  await user.click(combobox);
  const item = await screen.findByText(optionContent(label));
  await user.click(item);
}

const optionContent =
  (label: string) => (_content: string, el: Element | null) =>
    el?.classList.contains("ant-select-item-option-content") === true &&
    el.textContent === label;

/** rc-select's Enter/Backspace handling checks ``keyCode``, which
 * ``userEvent``'s synthetic ``{Enter}``/``{Backspace}`` don't populate in
 * jsdom — fire the DOM event directly instead (mirrors ``field_defs.test.tsx``). */
function pressKey(el: HTMLElement, key: "Enter" | "Backspace"): void {
  const keyCode = key === "Enter" ? 13 : 8;
  fireEvent.keyDown(el, { key, code: key, keyCode, which: keyCode });
}

describe("SecuritySection", () => {
  it("embeds the existing defenses/governance FormView sections", () => {
    renderSection();
    expect(screen.getByTestId("af-defenses-output-screen")).toBeInTheDocument();
    expect(screen.getByTestId("af-approval")).toBeInTheDocument();
  });

  it("renders all four new FieldRows (network egress + tool-use enforcement)", () => {
    renderSection();
    for (const id of FIELD_IDS) {
      expect(rowFor(id)).toBeInTheDocument();
    }
  });

  it("both new panels start collapsed", () => {
    const { container } = renderSection();
    const outer = container.querySelector(
      '[data-testid="security-section"] > .ant-collapse',
    ) as HTMLElement;
    const panelItems = Array.from(outer.children).filter((el) =>
      el.classList.contains("ant-collapse-item"),
    );
    expect(panelItems).toHaveLength(2);
    const headers = panelItems.map((item) => item.children[0]);
    expect(headers[0]).toHaveAttribute("aria-expanded", "false");
    expect(headers[1]).toHaveAttribute("aria-expanded", "false");
  });

  it("choosing egress=none writes spec.sandbox.network.egress", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderSection({}, onChange);
    await openPanel(user, "① Network egress");

    const combobox = within(rowFor("sandbox.network.egress")).getByRole(
      "combobox",
    );
    await pickOption(user, combobox, "None (blocked)");

    const last = onChange.mock.calls.at(-1)?.[0] as AgentManifest;
    expect(last.spec?.sandbox?.network?.egress).toBe("none");
  });

  it("choosing the egress option matching the default deletes the key", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const seed: AgentManifest = {
      spec: { sandbox: { network: { egress: "none" } } },
    };
    renderSection(seed, onChange);
    await openPanel(user, "① Network egress");

    const combobox = within(rowFor("sandbox.network.egress")).getByRole(
      "combobox",
    );
    await pickOption(user, combobox, "Proxy (default)");

    const last = onChange.mock.calls.at(-1)?.[0] as AgentManifest;
    expect(last.spec?.sandbox?.network?.egress).toBeUndefined();
  });

  it("typing two domains into the allowlist writes a string array", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const { rerender } = renderSection({}, onChange);
    await openPanel(user, "① Network egress");

    const combobox = within(rowFor("sandbox.network.allowlist")).getByRole(
      "combobox",
    );
    await user.click(combobox);
    await user.keyboard("a.example.com");
    pressKey(combobox, "Enter");

    let last = onChange.mock.calls.at(-1)?.[0] as AgentManifest;
    expect(last.spec?.sandbox?.network?.allowlist).toEqual(["a.example.com"]);

    // Controlled Select — feed the first patch back into formData before
    // typing the second entry (mirrors field_defs.test.tsx's tags round trip).
    rerender(
      <SecuritySection formData={last} onChange={onChange} />,
    );
    await user.keyboard("b.example.com");
    const opt = await screen.findByText(optionContent("b.example.com"));
    await user.click(opt);

    last = onChange.mock.calls.at(-1)?.[0] as AgentManifest;
    expect(last.spec?.sandbox?.network?.allowlist).toEqual([
      "a.example.com",
      "b.example.com",
    ]);
  });

  it("clearing the denylist back to empty deletes the key", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const seed: AgentManifest = {
      spec: { sandbox: { network: { denylist: ["bad.example.com"] } } },
    };
    renderSection(seed, onChange);
    await openPanel(user, "① Network egress");

    const combobox = within(rowFor("sandbox.network.denylist")).getByRole(
      "combobox",
    );
    await user.click(combobox);
    pressKey(combobox, "Backspace");

    const last = onChange.mock.calls.at(-1)?.[0] as AgentManifest;
    expect(last.spec?.sandbox?.network?.denylist).toBeUndefined();
  });

  it("choosing tool_use_enforcement=on writes policies.tool_use_enforcement", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderSection({}, onChange);
    await openPanel(user, "② Tool-use enforcement");

    const combobox = within(
      rowFor("policies.tool_use_enforcement"),
    ).getByRole("combobox");
    await pickOption(user, combobox, "Always on");

    const last = onChange.mock.calls.at(-1)?.[0] as AgentManifest;
    expect(last.spec?.policies?.tool_use_enforcement).toBe("on");
  });

  it("the deleted af-tool-budget control no longer exists in the embedded governance section", () => {
    renderSection();
    expect(screen.queryByTestId("af-tool-budget")).not.toBeInTheDocument();
  });

  it("renders the closing dict-note", () => {
    renderSection();
    expect(screen.getByTestId("security-gates-dict-note")).toHaveTextContent(
      "Rate limiting / PII / security policy are still a free-form dict",
    );
  });

  // Task 2 (PR7) — trajectory_recording's hint copy was corrected to state
  // it's not wired up (recording is decided by the deployment's ObjectStore
  // config, not this manifest switch). It lives in the embedded "governance"
  // FormView section's collapsed "Advanced" panel.
  it("renders the corrected trajectory-recording hint inside the governance Advanced panel", async () => {
    const user = userEvent.setup();
    renderSection();
    const header = within(document.body)
      .getByText("Advanced", { selector: ".ant-collapse-header-text" })
      .closest(".ant-collapse-header") as HTMLElement;
    await user.click(header);

    fireEvent.mouseEnter(
      screen.getByTestId("field-help-af-trajectory-recording"),
    );
    await waitFor(() => {
      expect(
        screen.getByText(/object-store configuration/),
      ).toBeInTheDocument();
    });
  });
});
