/**
 * FileTree tests — nested folder rendering.
 *
 * Regression: deep skills (e.g. anthropics/skills pptx ships
 * ``scripts/office/schemas/ecma/fouth-edition/*.xsd``) used to render every
 * path below the first segment as one flat full-path leaf. The tree must now
 * nest real subfolders and show leaf files by basename only.
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "../../../i18n";

import { FileTree } from "../FileTree";

function renderTree(paths: string[]) {
  return render(
    <FileTree
      paths={paths}
      selected={null}
      onSelect={vi.fn()}
      onAddFile={vi.fn()}
    />,
  );
}

/** Folders default to collapsed (GitHub-style); expand every level so the
 * nesting structure can be asserted. Clicks all closed switchers until none
 * remain (deep trees reveal new switchers per level). */
async function expandAll(tree: HTMLElement): Promise<void> {
  for (let i = 0; i < 12; i += 1) {
    const closed = tree.querySelectorAll(".ant-tree-switcher_close");
    if (closed.length === 0) break;
    for (const sw of closed) {
      await userEvent.click(sw as HTMLElement);
    }
  }
}

describe("FileTree", () => {
  it("nests deep paths as real subfolders, leaves shown by basename", async () => {
    renderTree([
      "scripts/add_slide.py",
      "scripts/office/helpers/__init__.py",
      "scripts/office/schemas/ecma/fouth-edition/opc-contentTypes.xsd",
    ]);
    const tree = screen.getByTestId("skill-file-tree");
    await expandAll(tree);

    // Each path segment renders as its own folder node (trailing slash),
    // not a flattened ``office/helpers/…`` label.
    expect(within(tree).getByText("scripts/")).toBeInTheDocument();
    expect(within(tree).getByText("office/")).toBeInTheDocument();
    expect(within(tree).getByText("helpers/")).toBeInTheDocument();
    expect(within(tree).getByText("ecma/")).toBeInTheDocument();
    expect(within(tree).getByText("fouth-edition/")).toBeInTheDocument();

    // Leaf files are basenames only — no embedded slashes.
    expect(within(tree).getByText("opc-contentTypes.xsd")).toBeInTheDocument();
    expect(within(tree).getByText("__init__.py")).toBeInTheDocument();
    expect(
      within(tree).queryByText("office/helpers/__init__.py"),
    ).not.toBeInTheDocument();
  });

  it("keeps SKILL.md pinned and shallow paths flat-grouped", async () => {
    renderTree(["reference/notes.md"]);
    const tree = screen.getByTestId("skill-file-tree");
    // SKILL.md + the top folder show without expanding; the leaf needs a click.
    expect(within(tree).getByText("SKILL.md")).toBeInTheDocument();
    expect(within(tree).getByText("reference/")).toBeInTheDocument();
    await expandAll(tree);
    expect(within(tree).getByText("notes.md")).toBeInTheDocument();
  });
});
