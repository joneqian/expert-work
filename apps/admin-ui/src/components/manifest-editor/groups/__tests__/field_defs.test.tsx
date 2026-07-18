import { beforeAll, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "../../../../i18n";

import i18n from "../../../../i18n";
import { PolicyFieldList, type FieldDef } from "../field_defs";

// A synthetic i18n subtree, registered once for this suite, so
// PolicyFieldList's generic behaviour (patch semantics, isDefault,
// optional impact/default copy) can be verified without depending on any
// real field's copy — mirrors the ``${i18nKey}_label/_brief/_impact/_default``
// convention Task 3's FieldDef authors will follow, including the
// "impact/default may be omitted" case called out on ``PolicyFieldListProps``.
const NS = "policy_field_list_fixture";

beforeAll(() => {
  i18n.addResourceBundle(
    "en",
    "translation",
    {
      [NS]: {
        count_label: "Count",
        count_brief: "A count of things",
        count_impact: "Raising this increases X",
        count_default: "10",
        ratio_label: "Ratio",
        ratio_brief: "A ratio value",
        ratio_default: "0.5",
        // no ratio_impact — exercises the omitted-impact branch
        flag_label: "Flag",
        flag_brief: "A boolean flag",
        // no flag_impact — exercises the omitted-impact branch
        flag_default: "true",
        mode_label: "Mode",
        mode_brief: "How the thing runs",
        mode_default: "auto",
        mode_option_auto: "Automatic",
        mode_option_manual: "Manual Mode",
        mode_option_off: "Turned Off",
        labels_label: "Labels",
        labels_brief: "Free-form tags",
      },
    },
    true,
    true,
  );
});

const NUMBER_DEF: FieldDef = {
  fieldId: "workflow.count",
  i18nKey: `${NS}.count`,
  valueKey: "count",
  kind: "number",
  effectiveDefault: 10,
};

const PERCENT_DEF: FieldDef = {
  fieldId: "policies.ratio",
  i18nKey: `${NS}.ratio`,
  valueKey: "ratio",
  kind: "percent",
  effectiveDefault: 0.5,
};

const SWITCH_DEF: FieldDef = {
  fieldId: "policies.flag",
  i18nKey: `${NS}.flag`,
  valueKey: "flag",
  kind: "switch",
  effectiveDefault: false,
};

const SELECT_DEF: FieldDef = {
  fieldId: "policies.mode",
  i18nKey: `${NS}.mode`,
  valueKey: "mode",
  kind: "select",
  effectiveDefault: "auto",
  options: ["auto", "manual", "off"],
  optionLabelKey: `${NS}.mode_option`,
};

const TAGS_DEF: FieldDef = {
  fieldId: "policies.labels",
  i18nKey: `${NS}.labels`,
  valueKey: "labels",
  kind: "tags",
  effectiveDefault: [],
};

type FieldValueOrUndefined =
  | number
  | boolean
  | string
  | readonly string[]
  | undefined;

function renderList(
  defs: readonly FieldDef[],
  values: Record<string, FieldValueOrUndefined> = {},
  onPatch: (patch: Record<string, FieldValueOrUndefined>) => void = vi.fn(),
) {
  return render(
    <PolicyFieldList defs={defs} values={values} onPatch={onPatch} />,
  );
}

function rowFor(fieldId: string): HTMLElement {
  return document.querySelector(`[data-field-id="${fieldId}"]`) as HTMLElement;
}

/**
 * In jsdom, Antd's Select renders each option twice: a visible, clickable
 * ``.ant-select-item-option`` div and a hidden ARIA ``role="option"`` mirror
 * with the same text. This opens the given combobox and clicks the real
 * ``.ant-select-item-option-content`` carrying the requested label (mirrors
 * ``ModelSelect.test.tsx``'s helper of the same shape).
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

/**
 * Matches only the dropdown option's visible content div — plain
 * ``getByText(label)`` also matches the already-selected value's
 * ``.ant-select-selection-item`` chip once a field is at its default,
 * producing a false "multiple elements" failure.
 */
const optionContent =
  (label: string) => (_content: string, el: Element | null) =>
    el?.classList.contains("ant-select-item-option-content") === true &&
    el.textContent === label;

/** rc-select's Enter/Backspace handling checks ``keyCode``, which
 * ``userEvent``'s synthetic ``{Enter}``/``{Backspace}`` don't populate in
 * jsdom — fire the DOM event directly instead (mirrors the rc-slider
 * ``fireEvent.keyDown`` workaround in ``ModelSelect.test.tsx``). */
function pressKey(el: HTMLElement, key: "Enter" | "Backspace"): void {
  const keyCode = key === "Enter" ? 13 : 8;
  fireEvent.keyDown(el, { key, code: key, keyCode, which: keyCode });
}

describe("PolicyFieldList", () => {
  it("renders one FieldRow per def, each carrying its fieldId", () => {
    renderList([NUMBER_DEF, PERCENT_DEF, SWITCH_DEF]);
    for (const def of [NUMBER_DEF, PERCENT_DEF, SWITCH_DEF]) {
      expect(rowFor(def.fieldId)).toBeInTheDocument();
    }
  });

  it("clearing a number field patches an explicit undefined (revert to default)", async () => {
    const user = userEvent.setup();
    const onPatch = vi.fn();
    renderList([NUMBER_DEF], { count: 42 }, onPatch);

    const input = within(rowFor("workflow.count")).getByRole("spinbutton");
    await user.clear(input);

    expect(onPatch).toHaveBeenCalledWith({ count: undefined });
  });

  it("a percent field renders its InputNumber with step 0.05", () => {
    renderList([PERCENT_DEF], { ratio: 0.5 });
    const input = within(rowFor("policies.ratio")).getByRole("spinbutton");
    expect(input).toHaveAttribute("step", "0.05");
  });

  it("switching a switch field to a non-default value patches the explicit value", async () => {
    const user = userEvent.setup();
    const onPatch = vi.fn();
    renderList([SWITCH_DEF], { flag: false }, onPatch);

    await user.click(within(rowFor("policies.flag")).getByRole("switch"));

    expect(onPatch).toHaveBeenCalledWith({ flag: true });
  });

  it("switching a switch field back to its default value deletes the key", async () => {
    const user = userEvent.setup();
    const onPatch = vi.fn();
    renderList([SWITCH_DEF], { flag: true }, onPatch);

    await user.click(within(rowFor("policies.flag")).getByRole("switch"));

    expect(onPatch).toHaveBeenCalledWith({ flag: undefined });
  });

  it("isDefault is true (gray badge) when the raw value is unset", () => {
    renderList([NUMBER_DEF], {});
    const badge = screen.getByText("Default 10");
    expect(badge.closest(".ant-tag")).not.toHaveClass("ant-tag-blue");
  });

  it("isDefault is true (gray badge) when the raw value explicitly equals the default", () => {
    renderList([NUMBER_DEF], { count: 10 });
    const badge = screen.getByText("Default 10");
    expect(badge.closest(".ant-tag")).not.toHaveClass("ant-tag-blue");
  });

  it("isDefault is false (blue badge, current value) once the raw value diverges", () => {
    renderList([NUMBER_DEF], { count: 42 });
    expect(screen.queryByText("Default 10")).not.toBeInTheDocument();
    const badge = screen.getByText("42");
    expect(badge.closest(".ant-tag")).toHaveClass("ant-tag-blue");
  });

  it("omits the impact expander when the def's i18n subtree has no _impact key", () => {
    renderList([PERCENT_DEF], { ratio: 0.5 });
    expect(screen.queryByText("Impact")).not.toBeInTheDocument();
  });

  it("suppresses the badge for switch fields at default, even when the def's i18n subtree defines a _default key", () => {
    renderList([SWITCH_DEF], {});
    expect(document.querySelector(".ant-tag")).not.toBeInTheDocument();
  });

  it("suppresses the badge for switch fields once diverged from default too", () => {
    renderList([SWITCH_DEF], { flag: true });
    expect(document.querySelector(".ant-tag")).not.toBeInTheDocument();
  });

  // ---- select ----

  it("a select field renders its options with i18n-resolved labels", async () => {
    const user = userEvent.setup();
    renderList([SELECT_DEF], {});
    const combobox = within(rowFor("policies.mode")).getByRole("combobox");
    await user.click(combobox);

    // Labels come from `${optionLabelKey}_${option}`, not the raw option value.
    expect(await screen.findByText(optionContent("Automatic"))).toBeInTheDocument();
    expect(screen.getByText(optionContent("Manual Mode"))).toBeInTheDocument();
    expect(screen.getByText(optionContent("Turned Off"))).toBeInTheDocument();
    expect(screen.queryByText(optionContent("manual"))).not.toBeInTheDocument();
  });

  it("choosing a non-default select option patches the raw option value", async () => {
    const user = userEvent.setup();
    const onPatch = vi.fn();
    renderList([SELECT_DEF], {}, onPatch);
    const combobox = within(rowFor("policies.mode")).getByRole("combobox");

    await pickOption(user, combobox, "Manual Mode");

    expect(onPatch).toHaveBeenCalledWith({ mode: "manual" });
  });

  it("choosing the select option matching effectiveDefault deletes the key", async () => {
    const user = userEvent.setup();
    const onPatch = vi.fn();
    renderList([SELECT_DEF], { mode: "manual" }, onPatch);
    const combobox = within(rowFor("policies.mode")).getByRole("combobox");

    await pickOption(user, combobox, "Automatic");

    expect(onPatch).toHaveBeenCalledWith({ mode: undefined });
  });

  it("a select field falls back to the raw value when optionLabelKey is absent", async () => {
    const user = userEvent.setup();
    const bareDef: FieldDef = { ...SELECT_DEF, optionLabelKey: undefined };
    renderList([bareDef], {});
    const combobox = within(rowFor("policies.mode")).getByRole("combobox");
    await user.click(combobox);

    expect(await screen.findByText(optionContent("manual"))).toBeInTheDocument();
    expect(screen.queryByText(optionContent("Manual Mode"))).not.toBeInTheDocument();
  });

  // ---- tags ----

  it("typing two entries into a tags field patches a string array", async () => {
    const user = userEvent.setup();
    const onPatch = vi.fn();
    const { rerender } = renderList([TAGS_DEF], {}, onPatch);
    const combobox = within(rowFor("policies.labels")).getByRole("combobox");

    await user.click(combobox);
    await user.keyboard("alpha");
    pressKey(combobox, "Enter");
    expect(onPatch).toHaveBeenLastCalledWith({ labels: ["alpha"] });

    // Simulate the real round trip (onPatch → formData → values): this is a
    // controlled Select, so without feeding the first patch back into
    // `values`, typing a second entry would re-add "alpha" instead of
    // "beta". Commit via the freshly-typed custom option rather than Enter
    // — rc-select's post-rerender Enter/activedescendant tracking is
    // unreliable in jsdom; clicking the option is the robust path already
    // used for ``select`` above.
    rerender(
      <PolicyFieldList
        defs={[TAGS_DEF]}
        values={{ labels: ["alpha"] }}
        onPatch={onPatch}
      />,
    );
    await user.keyboard("beta");
    const betaOption = await screen.findByText(optionContent("beta"));
    await user.click(betaOption);

    expect(onPatch).toHaveBeenLastCalledWith({ labels: ["alpha", "beta"] });
  });

  it("clearing a tags field back to an empty array deletes the key", async () => {
    const user = userEvent.setup();
    const onPatch = vi.fn();
    const { rerender } = renderList(
      [TAGS_DEF],
      { labels: ["alpha", "beta"] },
      onPatch,
    );
    const combobox = within(rowFor("policies.labels")).getByRole("combobox");
    await user.click(combobox);

    // A Backspace on an empty search removes the last tag; being a
    // controlled Select, the round trip must feed each removal back into
    // `values` before the next Backspace acts on the updated list.
    pressKey(combobox, "Backspace");
    expect(onPatch).toHaveBeenLastCalledWith({ labels: ["alpha"] });

    rerender(
      <PolicyFieldList
        defs={[TAGS_DEF]}
        values={{ labels: ["alpha"] }}
        onPatch={onPatch}
      />,
    );
    pressKey(combobox, "Backspace");

    expect(onPatch).toHaveBeenLastCalledWith({ labels: undefined });
  });

  it("suppresses the badge for tags fields", () => {
    renderList([TAGS_DEF], { labels: ["alpha"] });
    expect(
      document.querySelector('[data-field-id="policies.labels"] .ant-tag'),
    ).not.toBeInTheDocument();
  });
});
