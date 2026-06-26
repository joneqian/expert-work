/**
 * FieldHelp tests — the "?" affordance after a field label.
 * Verifies the accessible trigger (button + aria-label + testid) and that the
 * tooltip text (meaning + example, multi-line) reveals on hover.
 */
import { describe, expect, it } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import "../../i18n";

import { FieldHelp } from "../FieldHelp";

describe("FieldHelp", () => {
  it("renders an accessible help button with a testid", () => {
    render(<FieldHelp text={"含义\n示例:abc"} testId="af-name" />);
    const btn = screen.getByTestId("field-help-af-name");
    expect(btn.tagName).toBe("BUTTON");
    // aria-label comes from the shared common.field_help key.
    expect(btn).toHaveAttribute("aria-label");
  });

  it("reveals the help text (meaning + example) on hover", async () => {
    render(<FieldHelp text={"字段含义说明\n示例:support-bot"} />);
    fireEvent.mouseEnter(screen.getByTestId("field-help"));
    await waitFor(() => {
      expect(screen.getByText("字段含义说明")).toBeInTheDocument();
      expect(screen.getByText("示例:support-bot")).toBeInTheDocument();
    });
  });
});
