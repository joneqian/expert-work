import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "antd";
import "../../../i18n";
import * as sdk from "../../../api/platform_quality_config";
import type { PlatformQualityConfigView, QualityConfig } from "../../../api/platform_quality_config";
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

beforeEach(() => vi.spyOn(sdk, "getPlatformQualityConfig").mockResolvedValue(view()));
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
