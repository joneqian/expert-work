import { afterEach, describe, expect, it, vi } from "vitest";
import * as sdk from "../../../api/model_catalog";
import {
  loadModelCatalog,
  __resetCatalogCacheForTest,
  providerNames,
  modelsFor,
  lookupModel,
} from "../catalog";

const CATALOG = {
  providers: [
    {
      provider: "deepseek",
      models: [
        { name: "deepseek-v4-pro", vision: false, embeddings: false, context_window: 1000000, deprecated: false },
      ],
    },
    {
      provider: "openai",
      models: [
        { name: "gpt-5.5", vision: true, embeddings: false, context_window: 128000, deprecated: false },
        { name: "text-embedding-3-large", vision: false, embeddings: true, context_window: null, deprecated: false },
      ],
    },
  ],
};

afterEach(() => {
  __resetCatalogCacheForTest();
  vi.restoreAllMocks();
});

describe("model catalog", () => {
  it("fetches once and caches", async () => {
    const spy = vi.spyOn(sdk, "fetchModelCatalog").mockResolvedValue(CATALOG);
    const a = await loadModelCatalog();
    const b = await loadModelCatalog();
    expect(a).toBe(b);
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("lookups work", () => {
    expect(providerNames(CATALOG)).toEqual(["deepseek", "openai"]);
    expect(modelsFor(CATALOG, "openai").map((m) => m.name)).toEqual(["gpt-5.5", "text-embedding-3-large"]);
    expect(lookupModel(CATALOG, "openai", "gpt-5.5")?.vision).toBe(true);
    expect(lookupModel(CATALOG, "openai", "nope")).toBeUndefined();
  });
});
