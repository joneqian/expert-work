import { beforeAll, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
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
        // no flag_impact, no flag_default — exercises both omitted
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

function renderList(
  defs: readonly FieldDef[],
  values: Record<string, number | boolean | undefined> = {},
  onPatch: (patch: Record<string, number | boolean | undefined>) => void = vi.fn(),
) {
  return render(
    <PolicyFieldList defs={defs} values={values} onPatch={onPatch} />,
  );
}

function rowFor(fieldId: string): HTMLElement {
  return document.querySelector(`[data-field-id="${fieldId}"]`) as HTMLElement;
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

  it("omits the default badge entirely when the def's i18n subtree has no _default key and the value is at default", () => {
    renderList([SWITCH_DEF], {});
    expect(document.querySelector(".ant-tag")).not.toBeInTheDocument();
  });
});
