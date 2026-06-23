/**
 * SettingsRateCard tests — 模型定价简化.
 *
 * The rate-card + model-catalog SDKs are stubbed. Covers: the frontend gate
 * (no fetch for non-admins), list render with ¥/百万 prices, the immutable
 * identity edit drawer (patches price fields only), delete confirm, and the
 * 元↔micro-元/百万token conversion helpers.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "antd";
import { MemoryRouter } from "react-router-dom";
import "../../i18n";

import * as rateCardSdk from "../../api/rate_card";
import { SettingsRateCard } from "../SettingsRateCard";

let mockIsSystemAdmin = true;
vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({
    identity: { isSystemAdmin: mockIsSystemAdmin },
  }),
}));

vi.mock("../../api/model_catalog", () => ({
  fetchModelCatalog: vi.fn().mockResolvedValue({
    providers: [
      {
        provider: "anthropic",
        models: [
          {
            name: "claude-opus-4-8",
            vision: true,
            embeddings: false,
            context_window: 200000,
            deprecated: false,
          },
        ],
      },
    ],
  }),
}));

const RECORD: rateCardSdk.RateCardRecord = {
  id: "11111111-1111-1111-1111-111111111111",
  tenant_id: null,
  provider: "anthropic",
  model: "claude-opus-4-8",
  input_per_mtok_micros: 5_000_000,
  output_per_mtok_micros: 25_000_000,
  cache_creation_per_mtok_micros: 6_000_000,
  cache_read_per_mtok_micros: 1_000_000,
};

function renderPage() {
  return render(
    <MemoryRouter>
      <App>
        <SettingsRateCard />
      </App>
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
  mockIsSystemAdmin = true;
});

describe("SettingsRateCard", () => {
  it("gates non-system-admins without fetching", async () => {
    mockIsSystemAdmin = false;
    const listSpy = vi.spyOn(rateCardSdk, "listRateCards");

    renderPage();

    await waitFor(() =>
      expect(screen.getByTestId("rate-card-forbidden")).toBeInTheDocument(),
    );
    expect(listSpy).not.toHaveBeenCalled();
  });

  it("renders rows with ¥ / 百万 prices", async () => {
    vi.spyOn(rateCardSdk, "listRateCards").mockResolvedValue([RECORD]);

    renderPage();

    await waitFor(() => expect(screen.getByText("claude-opus-4-8")).toBeInTheDocument());
    // input 5_000_000 micro-元/百万 → ¥5; output 25_000_000 → ¥25.
    expect(screen.getByText("¥5")).toBeInTheDocument();
    expect(screen.getByText("¥25")).toBeInTheDocument();
  });

  it("edit drawer shows the identity note and patches only price fields", async () => {
    vi.spyOn(rateCardSdk, "listRateCards").mockResolvedValue([RECORD]);
    const patchSpy = vi.spyOn(rateCardSdk, "patchRateCard").mockResolvedValue(RECORD);

    renderPage();
    await waitFor(() => expect(screen.getByText("claude-opus-4-8")).toBeInTheDocument());
    await userEvent.click(screen.getByTestId(`rc-edit-${RECORD.id}`));

    await waitFor(() => expect(screen.getByTestId("rc-identity-note")).toBeInTheDocument());

    await userEvent.click(screen.getByText("Save"));
    await waitFor(() => expect(patchSpy).toHaveBeenCalled());
    const [, patch] = patchSpy.mock.calls[0];
    // Price fields only — identity fields never appear in the payload.
    expect(patch).not.toHaveProperty("provider");
    expect(patch).not.toHaveProperty("model");
    // 元 5 round-trips back to 5_000_000 micro-元/百万token.
    expect(patch).toHaveProperty("input_per_mtok_micros", 5_000_000);
  });

  it("deletes after confirm", async () => {
    vi.spyOn(rateCardSdk, "listRateCards").mockResolvedValue([RECORD]);
    const deleteSpy = vi.spyOn(rateCardSdk, "deleteRateCard").mockResolvedValue();

    renderPage();
    await waitFor(() => expect(screen.getByText("claude-opus-4-8")).toBeInTheDocument());
    await userEvent.click(screen.getByTestId(`rc-delete-${RECORD.id}`));
    await userEvent.click(await screen.findByText("Delete", { selector: "button span" }));

    await waitFor(() => expect(deleteSpy).toHaveBeenCalledWith(RECORD.id));
  });

  it("converts 元 ↔ micro-元/百万token", () => {
    expect(rateCardSdk.mtokMicrosToCny(5_000_000)).toBe(5);
    expect(rateCardSdk.mtokMicrosToCny(500_000)).toBe(0.5);
    expect(rateCardSdk.cnyToMtokMicros(0.5)).toBe(500_000);
    expect(rateCardSdk.cnyToMtokMicros(2.5)).toBe(2_500_000);
  });
});
