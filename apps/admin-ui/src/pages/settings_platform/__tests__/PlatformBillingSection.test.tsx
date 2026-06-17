import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "antd";
import type { ReactElement } from "react";
import "../../../i18n";
import * as sdk from "../../../api/platform_billing_config";
import { PlatformBillingSection } from "../PlatformBillingSection";

// Wrap in antd <App> so the section's ``App.useApp()`` message API has context.
function renderSection(node: ReactElement) {
  return render(<App>{node}</App>);
}

beforeEach(() =>
  vi
    .spyOn(sdk, "getPlatformBillingConfig")
    .mockResolvedValue({ rollup_enabled: true }),
);
afterEach(() => vi.restoreAllMocks());

describe("PlatformBillingSection", () => {
  it("shows the friendly explanation of cost rollup", async () => {
    renderSection(<PlatformBillingSection />);
    await screen.findByTestId("pb-root");
    expect(screen.getByTestId("pb-help")).toBeInTheDocument();
  });

  it("reflects the enabled toggle from the backend", async () => {
    renderSection(<PlatformBillingSection />);
    await screen.findByTestId("pb-root");
    expect(screen.getByTestId("pb-toggle")).toBeChecked();
  });

  it("PUTs the new value when toggled off", async () => {
    const user = userEvent.setup();
    const put = vi
      .spyOn(sdk, "putPlatformBillingConfig")
      .mockResolvedValue({ rollup_enabled: false });
    renderSection(<PlatformBillingSection />);
    await screen.findByTestId("pb-root");
    await user.click(screen.getByTestId("pb-toggle"));
    await waitFor(() => expect(put).toHaveBeenCalledWith(false));
  });

  it("renders the paused state when disabled", async () => {
    vi.spyOn(sdk, "getPlatformBillingConfig").mockResolvedValueOnce({
      rollup_enabled: false,
    });
    renderSection(<PlatformBillingSection />);
    await screen.findByTestId("pb-root");
    expect(screen.getByTestId("pb-toggle")).not.toBeChecked();
  });
});
