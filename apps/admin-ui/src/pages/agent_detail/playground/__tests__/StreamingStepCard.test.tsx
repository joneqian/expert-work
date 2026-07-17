import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import "../../../../i18n";

import { StreamingStepCard } from "../StreamingStepCard";

describe("StreamingStepCard", () => {
  it("renders the accumulated text as plain text (not markdown)", () => {
    render(<StreamingStepCard step={0} text={"# not a heading\n**still literal**"} interrupted={false} ttftMs={null} />);
    const card = screen.getByTestId("streaming-step-card");
    // Plain-text render: the raw markdown chars are present verbatim; no <h1>/<strong>.
    expect(card).toHaveTextContent("# not a heading");
    expect(card).toHaveTextContent("**still literal**");
    expect(card.querySelector("h1")).toBeNull();
    expect(card.querySelector("strong")).toBeNull();
  });

  it("shows the streaming badge while not interrupted", () => {
    render(<StreamingStepCard step={1} text="hi" interrupted={false} ttftMs={null} />);
    expect(screen.getByTestId("streaming-badge")).toBeInTheDocument();
    expect(screen.queryByTestId("interrupted-badge")).toBeNull();
  });

  it("shows the interrupted badge when interrupted", () => {
    render(<StreamingStepCard step={1} text="partial" interrupted={true} ttftMs={null} />);
    expect(screen.getByTestId("interrupted-badge")).toBeInTheDocument();
    expect(screen.queryByTestId("streaming-badge")).toBeNull();
  });

  it("shows a TTFT badge when ttftMs is set, hides it when null", () => {
    const { rerender } = render(<StreamingStepCard step={0} text="x" interrupted={false} ttftMs={1234} />);
    expect(screen.getByTestId("ttft-badge")).toBeInTheDocument();
    rerender(<StreamingStepCard step={0} text="x" interrupted={false} ttftMs={null} />);
    expect(screen.queryByTestId("ttft-badge")).toBeNull();
  });

  it("labels the step by index", () => {
    render(<StreamingStepCard step={3} text="x" interrupted={false} ttftMs={null} />);
    expect(screen.getByTestId("streaming-step-card")).toHaveAttribute("data-step", "3");
  });
});
