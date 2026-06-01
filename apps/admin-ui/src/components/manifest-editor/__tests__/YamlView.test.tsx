import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

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

import { YamlView } from "../YamlView";

describe("YamlView", () => {
  it("shows the value and reports edits", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<YamlView value="model: {}" onChange={onChange} />);

    const root = screen.getByTestId("manifest-yaml-view");
    expect(root).toBeInTheDocument();

    const ta = screen.getByTestId("monaco-stub") as HTMLTextAreaElement;
    expect(ta.value).toBe("model: {}");
    await user.type(ta, "!");
    expect(onChange).toHaveBeenCalled();
  });
});
