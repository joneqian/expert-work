import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import "../../../../i18n";

import { StreamingStepCard } from "../StreamingStepCard";
import type { LiveStep } from "../useTokenStream";

function mkLive(p: Partial<LiveStep> = {}): LiveStep {
  return { content: "", reasoning: "", toolNames: new Map(), reasoningMs: null, ...p };
}

describe("StreamingStepCard", () => {
  it("renders content as plain text (not markdown)", () => {
    render(
      <StreamingStepCard
        step={0}
        live={mkLive({ content: "# not a heading\n**still literal**" })}
        interrupted={false}
        ttftMs={null}
      />,
    );
    const card = screen.getByTestId("streaming-step-card");
    expect(card).toHaveTextContent("# not a heading");
    expect(card).toHaveTextContent("**still literal**");
    expect(card.querySelector("h1")).toBeNull();
    expect(card.querySelector("strong")).toBeNull();
  });

  it("shows the streaming badge while not interrupted, interrupted badge when interrupted", () => {
    const { rerender } = render(
      <StreamingStepCard step={1} live={mkLive({ content: "hi" })} interrupted={false} ttftMs={null} />,
    );
    expect(screen.getByTestId("streaming-badge")).toBeInTheDocument();
    expect(screen.queryByTestId("interrupted-badge")).toBeNull();
    rerender(<StreamingStepCard step={1} live={mkLive({ content: "hi" })} interrupted={true} ttftMs={null} />);
    expect(screen.getByTestId("interrupted-badge")).toBeInTheDocument();
    expect(screen.queryByTestId("streaming-badge")).toBeNull();
  });

  it("shows a TTFT badge when ttftMs is set, hides it when null", () => {
    const { rerender } = render(
      <StreamingStepCard step={0} live={mkLive({ content: "x" })} interrupted={false} ttftMs={1234} />,
    );
    expect(screen.getByTestId("ttft-badge")).toBeInTheDocument();
    rerender(<StreamingStepCard step={0} live={mkLive({ content: "x" })} interrupted={false} ttftMs={null} />);
    expect(screen.queryByTestId("ttft-badge")).toBeNull();
  });

  it("hides reasoning/tool regions when those channels are empty", () => {
    render(<StreamingStepCard step={0} live={mkLive({ content: "x" })} interrupted={false} ttftMs={null} />);
    expect(screen.queryByTestId("reasoning-region")).toBeNull();
    expect(screen.queryByTestId("tool-chip")).toBeNull();
  });

  it("auto-expands reasoning while streaming (reasoningMs null) and shows the thinking label", () => {
    render(
      <StreamingStepCard
        step={0}
        live={mkLive({ reasoning: "let me think" })}
        interrupted={false}
        ttftMs={null}
      />,
    );
    expect(screen.getByTestId("reasoning-region")).toBeInTheDocument();
    expect(screen.getByText("let me think")).toBeInTheDocument(); // expanded
    expect(screen.getByTestId("reasoning-summary")).toHaveTextContent("Thinking…");
  });

  it("auto-collapses reasoning once a duration is known, and re-expands on click", () => {
    render(
      <StreamingStepCard
        step={0}
        live={mkLive({ reasoning: "hidden thought", content: "answer", reasoningMs: 8000 })}
        interrupted={false}
        ttftMs={null}
      />,
    );
    // Collapsed: summary shows duration, body hidden.
    expect(screen.getByTestId("reasoning-summary")).toHaveTextContent("Thought for 8.0s");
    expect(screen.queryByText("hidden thought")).toBeNull();
    // Click re-expands.
    fireEvent.click(screen.getByTestId("reasoning-summary"));
    expect(screen.getByText("hidden thought")).toBeInTheDocument();
  });

  it("renders a tool chip per tool name, sorted by index", () => {
    render(
      <StreamingStepCard
        step={0}
        live={mkLive({ toolNames: new Map([[1, "read_file"], [0, "search_web"]]) })}
        interrupted={false}
        ttftMs={null}
      />,
    );
    const chips = screen.getAllByTestId("tool-chip");
    expect(chips).toHaveLength(2);
    expect(chips[0]).toHaveTextContent("search_web"); // index 0 first
    expect(chips[1]).toHaveTextContent("read_file");
  });

  it("labels the step by index", () => {
    render(<StreamingStepCard step={3} live={mkLive({ content: "x" })} interrupted={false} ttftMs={null} />);
    expect(screen.getByTestId("streaming-step-card")).toHaveAttribute("data-step", "3");
  });
});
