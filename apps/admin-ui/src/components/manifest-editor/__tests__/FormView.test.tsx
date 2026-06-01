import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FormView } from "../FormView";

const schema = {
  type: "object",
  properties: {
    metadata: {
      type: "object",
      properties: { name: { type: "string", title: "Name" } },
    },
  },
} as const;

describe("FormView", () => {
  it("renders fields from the schema", () => {
    render(<FormView schema={schema} formData={{}} onChange={vi.fn()} />);
    expect(screen.getByText("Name")).toBeInTheDocument();
  });

  it("emits changed formData", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<FormView schema={schema} formData={{}} onChange={onChange} />);
    const input = screen.getByLabelText("Name");
    await user.type(input, "bot");
    expect(onChange).toHaveBeenCalled();
  });
});
