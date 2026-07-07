import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "antd";
import "../../../i18n";
import * as sdk from "../../../api/platform_quality_config";
import type { PlatformQualityConfigView, QualityConfig } from "../../../api/platform_quality_config";
import * as judgeSdk from "../../../api/platform_judge_config";
import { ApiError } from "../../../api/client";
import { PlatformQualitySection } from "../PlatformQualitySection";

const CONFIG: QualityConfig = {
  enabled: false,
  sampling_rate_pct: 5,
  daily_cap: 500,
  monitor_interval_s: 300,
  monitor_batch_size: 200,
  judge_provider: "anthropic",
  judge_model: "claude-haiku-4-5-20251001",
  drift_interval_s: 3600,
  drift_recent_window_h: 24,
  drift_baseline_window_h: 168,
  drift_min_samples: 10,
  drift_threshold: 0.15,
  drift_cooldown_h: 24,
};

function view(over: Partial<PlatformQualityConfigView> = {}): PlatformQualityConfigView {
  return { config: CONFIG, is_default: true, ...over };
}

function renderSection() {
  return render(
    <App>
      <PlatformQualitySection />
    </App>,
  );
}

beforeEach(() => {
  vi.spyOn(sdk, "getPlatformQualityConfig").mockResolvedValue(view());
  vi.spyOn(judgeSdk, "getPlatformJudgeConfig").mockResolvedValue({
    judge: null,
    available: [
      { provider: "anthropic", model: "claude-haiku-4-5-20251001" },
      { provider: "anthropic", model: "claude-sonnet-4-6" },
      { provider: "openai", model: "gpt-4o-mini" },
    ],
  });
});
afterEach(() => vi.restoreAllMocks());

describe("PlatformQualitySection", () => {
  it("shows the friendly explanation + the default note when unsaved", async () => {
    renderSection();
    await screen.findByTestId("pq-root");
    expect(screen.getByTestId("pq-help")).toBeInTheDocument();
    expect(screen.getByTestId("pq-default-note")).toBeInTheDocument();
  });

  it("hides the default note once a row is saved", async () => {
    vi.spyOn(sdk, "getPlatformQualityConfig").mockResolvedValueOnce(view({ is_default: false }));
    renderSection();
    await screen.findByTestId("pq-root");
    expect(screen.queryByTestId("pq-default-note")).not.toBeInTheDocument();
  });

  it("saves the full config via PUT", async () => {
    const user = userEvent.setup();
    const put = vi
      .spyOn(sdk, "putPlatformQualityConfig")
      .mockResolvedValue({ config: { ...CONFIG, enabled: true } });
    renderSection();
    await screen.findByTestId("pq-root");
    // Toggle enabled on, then save.
    await user.click(screen.getByTestId("pq-enabled"));
    await user.click(screen.getByTestId("pq-save"));
    await waitFor(() => expect(put).toHaveBeenCalledTimes(1));
    expect(put).toHaveBeenCalledWith(expect.objectContaining({ enabled: true, judge_provider: "anthropic" }));
  });

  it("offers judge provider/model as dropdowns and resets model on provider change", async () => {
    const user = userEvent.setup();
    renderSection();
    await screen.findByTestId("pq-root");

    // Provider options come from the judge-config ``available`` list.
    await user.click(
      screen.getByTestId("pq-judge-provider").querySelector(".ant-select-selector")!,
    );
    await user.click(await screen.findByTitle("openai"));

    // Switching provider clears the previously loaded model…
    expect(
      screen.getByTestId("pq-judge-model").textContent,
    ).not.toContain("claude-haiku-4-5-20251001");

    // …and the model dropdown now lists only that provider's models.
    await user.click(
      screen.getByTestId("pq-judge-model").querySelector(".ant-select-selector")!,
    );
    expect(await screen.findByTitle("gpt-4o-mini")).toBeInTheDocument();
    expect(screen.queryByTitle("claude-sonnet-4-6")).not.toBeInTheDocument();
  });

  it("flags a prefilled judge whose provider has no platform key and blocks save", async () => {
    vi.spyOn(judgeSdk, "getPlatformJudgeConfig").mockResolvedValue({
      judge: null,
      available: [{ provider: "qwen", model: "qwen3.6-plus" }],
    });
    const put = vi.spyOn(sdk, "putPlatformQualityConfig").mockResolvedValue({ config: CONFIG });
    const user = userEvent.setup();
    renderSection();
    await screen.findByTestId("pq-root");
    // Prefilled anthropic has no key → the field errors without user input…
    expect(
      await screen.findByText(/platform credentials|平台凭据/i),
    ).toBeInTheDocument();
    // …and client-side validation blocks the PUT.
    await user.click(screen.getByTestId("pq-save"));
    await new Promise((r) => setTimeout(r, 50));
    expect(put).not.toHaveBeenCalled();
  });

  it("surfaces a 422 error code as a message", async () => {
    const user = userEvent.setup();
    vi.spyOn(sdk, "putPlatformQualityConfig").mockRejectedValue(
      new ApiError("bad model", "INVALID_JUDGE_MODEL", 422),
    );
    renderSection();
    await screen.findByTestId("pq-root");
    await user.click(screen.getByTestId("pq-save"));
    expect(await screen.findByTestId("pq-error")).toBeInTheDocument();
  });
});
