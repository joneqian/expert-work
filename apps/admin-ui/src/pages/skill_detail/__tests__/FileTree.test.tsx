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

describe("FileTree", () => {
  it("nests deep paths as real subfolders, leaves shown by basename", () => {
    renderTree([
      "scripts/add_slide.py",
      "scripts/office/helpers/__init__.py",
      "scripts/office/schemas/ecma/fouth-edition/opc-contentTypes.xsd",
    ]);
    const tree = screen.getByTestId("skill-file-tree");

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

  it("keeps SKILL.md pinned and shallow paths flat-grouped", () => {
    renderTree(["reference/notes.md"]);
    const tree = screen.getByTestId("skill-file-tree");
    expect(within(tree).getByText("SKILL.md")).toBeInTheDocument();
    expect(within(tree).getByText("reference/")).toBeInTheDocument();
    expect(within(tree).getByText("notes.md")).toBeInTheDocument();
  });
});
