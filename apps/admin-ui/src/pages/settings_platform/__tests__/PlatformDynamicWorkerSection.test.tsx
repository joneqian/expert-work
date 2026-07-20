import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "antd";
import type { ReactElement } from "react";
import "../../../i18n";
import * as sdk from "../../../api/platform_dynamic_worker_config";
import { PlatformDynamicWorkerSection } from "../PlatformDynamicWorkerSection";

// Wrap in antd <App> so the section's ``App.useApp()`` message API has context.
function renderSection(node: ReactElement) {
  return render(<App>{node}</App>);
}

beforeEach(() =>
  vi.spyOn(sdk, "getPlatformDynamicWorkerConfig").mockResolvedValue({
    configured: null,
    effective: { max_concurrent: 3, max_per_run: 16, max_iterations: 32 },
  }),
);
afterEach(() => vi.restoreAllMocks());

describe("PlatformDynamicWorkerSection", () => {
  it("shows the friendly explanation", async () => {
    renderSection(<PlatformDynamicWorkerSection />);
    await screen.findByTestId("pdw-root");
    expect(screen.getByTestId("pdw-help")).toBeInTheDocument();
  });

  it("renders the three inputs seeded from the effective limits", async () => {
    renderSection(<PlatformDynamicWorkerSection />);
    await screen.findByTestId("pdw-root");
    expect(screen.getByTestId("pdw-max-concurrent")).toHaveValue("3");
    expect(screen.getByTestId("pdw-max-per-run")).toHaveValue("16");
    expect(screen.getByTestId("pdw-max-iterations")).toHaveValue("32");
  });

  it("tags env default when no platform override is set", async () => {
    renderSection(<PlatformDynamicWorkerSection />);
    await screen.findByTestId("pdw-root");
    expect(screen.getByTestId("pdw-env-default")).toBeInTheDocument();
  });

  it("does not tag env default when a platform override is configured", async () => {
    vi.spyOn(sdk, "getPlatformDynamicWorkerConfig").mockResolvedValueOnce({
      configured: { max_concurrent: 5, max_per_run: 20, max_iterations: 40 },
      effective: { max_concurrent: 5, max_per_run: 20, max_iterations: 40 },
    });
    renderSection(<PlatformDynamicWorkerSection />);
    await screen.findByTestId("pdw-root");
    expect(screen.queryByTestId("pdw-env-default")).not.toBeInTheDocument();
  });

  it("PUTs the edited values when saved", async () => {
    const user = userEvent.setup();
    const put = vi.spyOn(sdk, "putPlatformDynamicWorkerConfig").mockResolvedValue({
      configured: { max_concurrent: 5, max_per_run: 16, max_iterations: 32 },
      effective: { max_concurrent: 5, max_per_run: 16, max_iterations: 32 },
    });
    renderSection(<PlatformDynamicWorkerSection />);
    await screen.findByTestId("pdw-root");

    const maxConcurrent = screen.getByTestId("pdw-max-concurrent");
    await user.clear(maxConcurrent);
    await user.type(maxConcurrent, "5");

    await user.click(screen.getByTestId("pdw-save"));

    await waitFor(() =>
      expect(put).toHaveBeenCalledWith({
        max_concurrent: 5,
        max_per_run: 16,
        max_iterations: 32,
      }),
    );
  });
});
