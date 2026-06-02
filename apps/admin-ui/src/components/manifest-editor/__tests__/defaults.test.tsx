import { describe, expect, it } from "vitest";
import { buildDefaultManifest } from "../defaults";

type Manifest = { spec: { model: { provider: string; name: string; supports_vision: boolean } } };

describe("buildDefaultManifest", () => {
  it("picks the first configured provider's first chat model and its vision flag", () => {
    const catalog = {
      providers: [
        {
          provider: "openai",
          models: [
            { name: "text-embedding-3-large", vision: false, embeddings: true, context_window: null, deprecated: false },
            { name: "gpt-5.5", vision: true, embeddings: false, context_window: 128000, deprecated: false },
          ],
        },
      ],
    };
    const m = buildDefaultManifest(catalog) as Manifest;
    expect(m.spec.model.provider).toBe("openai");
    expect(m.spec.model.name).toBe("gpt-5.5");
    expect(m.spec.model.supports_vision).toBe(true);
  });

  it("falls back to the base template when no provider is configured", () => {
    const m = buildDefaultManifest({ providers: [] }) as Manifest;
    expect(m.spec.model.provider).toBeTruthy();
    expect(m).not.toHaveProperty("spec.memory.long_term");
  });
});
