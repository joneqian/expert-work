/**
 * RunStatusBanner tests — Task 7.
 *
 * Test-time i18n resolves to English (jsdom's navigator.language, per
 * src/i18n's LanguageDetector — see TraceView.test.tsx precedent), so
 * state-text assertions use the English copy (rb_failed_at / rb_jump).
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import "../../../../i18n";

import { RunStatusBanner } from "../RunStatusBanner";

describe("RunStatusBanner", () => {
  it("ok state renders the summary and right-aligned metric chip values, no jump button", () => {
    render(
      <RunStatusBanner
        status="ok"
        summary="运行成功 · 6 步 · 2 次 LLM 调用 · 1 次工具"
        metrics={[
          { label: "耗时", value: "5.9s" },
          { label: "tokens", value: "13,459 / 116" },
          { label: "$", value: "0.0021" },
        ]}
      />,
    );

    expect(screen.getByTestId("run-status-banner")).toBeInTheDocument();
    expect(screen.getByText("运行成功 · 6 步 · 2 次 LLM 调用 · 1 次工具")).toBeInTheDocument();
    expect(screen.getByText("5.9s")).toBeInTheDocument();
    expect(screen.getByText("13,459 / 116")).toBeInTheDocument();
    expect(screen.getByText("0.0021")).toBeInTheDocument();
    expect(screen.queryByTestId("run-status-jump")).not.toBeInTheDocument();
  });

  it("error state renders the failed-at line (rb_failed_at + errorLabel) + errorMessage + a jump button that calls onJump when clicked", () => {
    const onJump = vi.fn();
    render(
      <RunStatusBanner
        status="error"
        summary="运行失败"
        errorLabel="工具调用 · exec_python"
        errorMessage="SandboxTimeout: 执行超过 30s"
        onJump={onJump}
      />,
    );

    const banner = screen.getByTestId("run-status-banner");
    expect(banner).toHaveTextContent("Failed at 工具调用 · exec_python");
    expect(screen.getByText("SandboxTimeout: 执行超过 30s")).toBeInTheDocument();

    const jump = screen.getByTestId("run-status-jump");
    expect(jump).toHaveTextContent("Jump to error");
    fireEvent.click(jump);
    expect(onJump).toHaveBeenCalledTimes(1);
  });

  it("error state without onJump renders no jump button", () => {
    render(
      <RunStatusBanner
        status="error"
        summary="运行失败"
        errorLabel="工具调用 · exec_python"
        errorMessage="SandboxTimeout: 执行超过 30s"
      />,
    );
    expect(screen.queryByTestId("run-status-jump")).not.toBeInTheDocument();
  });
});
