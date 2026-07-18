import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "../../../i18n";

import i18n from "../../../i18n";
import en from "../../../i18n/locales/en";
import { searchGroups } from "../groups";
import { SettingsSearch } from "../SettingsSearch";

// The real i18next instance's bound `t`, exactly as `useTranslation()` hands
// components — keeps these tests honest about what `searchGroups` actually
// receives in production instead of a hand-rolled stub.
const t = (key: string): string => i18n.t(key);

/**
 * In jsdom, Antd's Select-family components (AutoComplete included) render
 * each option twice: a visible, clickable `.ant-select-item-option` div and
 * a hidden ARIA `role="option"` mirror with the same text. This helper
 * clicks the real `.ant-select-item-option-content` carrying the requested
 * label — mirrors the pattern in ModelSelect.test.tsx.
 */
async function pickOption(
  user: ReturnType<typeof userEvent.setup>,
  label: string,
): Promise<void> {
  const item = await screen.findByText(
    (_content, el) =>
      el?.classList.contains("ant-select-item-option-content") === true &&
      el.textContent === label,
  );
  await user.click(item);
}

function getSearchInput(): HTMLInputElement {
  return screen
    .getByTestId("cfg-search")
    .querySelector("input") as HTMLInputElement;
}

describe("searchGroups (pure)", () => {
  it("matches a group via keyword substring ('步数' → budget)", () => {
    const ids = searchGroups("步数", t).map((g) => g.id);
    expect(ids).toContain("budget");
  });

  it("matches a group via keyword substring ('mcp' → capabilities)", () => {
    const ids = searchGroups("mcp", t).map((g) => g.id);
    expect(ids).toContain("capabilities");
  });

  it("matches a group via its i18n-resolved label", () => {
    const ids = searchGroups("memory", t).map((g) => g.id);
    expect(ids).toContain("memory");
  });

  it("matches case-insensitively", () => {
    const ids = searchGroups("MCP", t).map((g) => g.id);
    expect(ids).toContain("capabilities");
  });

  it("returns no results for an empty or whitespace-only query", () => {
    expect(searchGroups("", t)).toEqual([]);
    expect(searchGroups("   ", t)).toEqual([]);
  });

  it("returns no results when nothing matches", () => {
    expect(searchGroups("zzz-nope-zzz", t)).toEqual([]);
  });
});

describe("SettingsSearch", () => {
  it("renders the search box with the i18n placeholder", () => {
    render(<SettingsSearch onPick={vi.fn()} />);
    const box = screen.getByTestId("cfg-search");
    expect(box).toBeInTheDocument();
    // Antd's Select-family components render the placeholder as a sibling
    // span (`.ant-select-selection-placeholder`), not an `<input placeholder>`
    // attribute — assert on the rendered text instead.
    expect(box).toHaveTextContent(en.manifest_editor.search_placeholder);
    expect(getSearchInput()).toHaveAttribute(
      "aria-label",
      en.manifest_editor.search_placeholder,
    );
  });

  it("shows no dropdown options for an empty query", async () => {
    const user = userEvent.setup();
    render(<SettingsSearch onPick={vi.fn()} />);
    await user.click(getSearchInput());
    expect(document.querySelectorAll(".ant-select-item-option")).toHaveLength(
      0,
    );
  });

  it("typing '步数' surfaces the budget group", async () => {
    const user = userEvent.setup();
    render(<SettingsSearch onPick={vi.fn()} />);
    await user.type(getSearchInput(), "步数");
    expect(
      await screen.findByText(en.manifest_editor.group_budget, {
        selector: ".ant-select-item-option-content",
      }),
    ).toBeInTheDocument();
  });

  it("typing 'mcp' surfaces the capabilities group", async () => {
    const user = userEvent.setup();
    render(<SettingsSearch onPick={vi.fn()} />);
    await user.type(getSearchInput(), "mcp");
    expect(
      await screen.findByText(en.manifest_editor.group_capabilities, {
        selector: ".ant-select-item-option-content",
      }),
    ).toBeInTheDocument();
  });

  it("calls onPick with the group id when a result is selected", async () => {
    const user = userEvent.setup();
    const onPick = vi.fn();
    render(<SettingsSearch onPick={onPick} />);
    await user.type(getSearchInput(), "mcp");
    await pickOption(user, en.manifest_editor.group_capabilities);
    expect(onPick).toHaveBeenCalledWith("capabilities");
  });

  it("clears the query after a selection instead of leaving the picked group's raw id", async () => {
    const user = userEvent.setup();
    render(<SettingsSearch onPick={vi.fn()} />);
    await user.type(getSearchInput(), "mcp");
    await pickOption(user, en.manifest_editor.group_capabilities);
    // The AutoComplete's ``value`` is controlled and cleared as soon as
    // ``onSelect`` fires, so the input never settles on the picked option's
    // raw group id ("capabilities") — only the (now empty) query.
    expect(getSearchInput().value).toBe("");
    expect(getSearchInput().value).not.toBe("capabilities");
  });

  it("never surfaces an excluded (hidden) group even when it matches", async () => {
    const user = userEvent.setup();
    render(<SettingsSearch onPick={vi.fn()} exclude={["budget"]} />);
    await user.type(getSearchInput(), "步数");
    expect(
      screen.queryByText(en.manifest_editor.group_budget, {
        selector: ".ant-select-item-option-content",
      }),
    ).not.toBeInTheDocument();
  });
});
