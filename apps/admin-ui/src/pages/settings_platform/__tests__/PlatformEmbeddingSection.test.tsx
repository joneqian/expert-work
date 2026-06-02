import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "../../../i18n";
import * as sdk from "../../../api/platform_embedding_config";
import { ApiError } from "../../../api/client";
import { PlatformEmbeddingSection } from "../PlatformEmbeddingSection";

const VIEW = {
  embedding: { provider: "qwen", model: "text-embedding-v4" },
  rerank: null,
  available_embedding: [
    { provider: "qwen", model: "text-embedding-v4" },
    { provider: "glm", model: "embedding-3" },
  ],
  available_rerank: [{ provider: "qwen", model: "qwen3-vl-rerank" }],
};

beforeEach(() => vi.spyOn(sdk, "getPlatformEmbeddingConfig").mockResolvedValue(VIEW));
afterEach(() => vi.restoreAllMocks());

async function pick(
  user: ReturnType<typeof userEvent.setup>,
  wrapperTestId: string,
  optionText: string,
): Promise<void> {
  await user.click(within(screen.getByTestId(wrapperTestId)).getByRole("combobox"));
  // Antd renders options twice in jsdom (visible + hidden mirror); click the visible content node
  const opts = await screen.findAllByText(optionText);
  const visible =
    opts.find((el) => el.className?.includes("ant-select-item-option-content")) ?? opts[0];
  await user.click(visible);
}

describe("PlatformEmbeddingSection", () => {
  it("shows the current embedding selection", async () => {
    render(<PlatformEmbeddingSection />);
    await waitFor(() => expect(screen.getByTestId("pe-root")).toBeInTheDocument());
    expect(screen.getByTestId("pe-root")).toHaveTextContent("text-embedding-v4");
  });

  it("saves a new embedding selection via PUT", async () => {
    const user = userEvent.setup();
    const put = vi.spyOn(sdk, "putPlatformEmbeddingConfig").mockResolvedValue({
      embedding: { provider: "glm", model: "embedding-3" },
      rerank: null,
    });
    render(<PlatformEmbeddingSection />);
    await screen.findByTestId("pe-root");
    await pick(user, "pe-embedding-provider", "glm");
    await pick(user, "pe-embedding-model", "embedding-3");
    await user.click(screen.getByTestId("pe-save"));
    await waitFor(() =>
      expect(put).toHaveBeenCalledWith(
        expect.objectContaining({ embedding_provider: "glm", embedding_model: "embedding-3" }),
      ),
    );
  });

  it("surfaces a 422 error code as a message", async () => {
    const user = userEvent.setup();
    vi.spyOn(sdk, "putPlatformEmbeddingConfig").mockRejectedValue(
      new ApiError("provider key missing", "EMBEDDING_PROVIDER_KEY_MISSING", 422),
    );
    render(<PlatformEmbeddingSection />);
    await screen.findByTestId("pe-root");
    await user.click(screen.getByTestId("pe-save"));
    expect(await screen.findByTestId("pe-error")).toBeInTheDocument();
  });
});
