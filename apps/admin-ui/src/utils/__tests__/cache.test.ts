import { describe, expect, it } from "vitest";

import { cacheHitRate, formatHitRate } from "../cache";

describe("cacheHitRate", () => {
  it("returns cache_read over the input-side total", () => {
    // 20 / (1200 + 20 + 10) = 0.016260…
    expect(cacheHitRate({ input_tokens: 1200, cache_read_tokens: 20, cache_creation_tokens: 10 })).toBeCloseTo(
      20 / 1230,
      6,
    );
  });

  it("is 1 when every input-side token is a cache read", () => {
    expect(
      cacheHitRate({ input_tokens: 0, cache_read_tokens: 500, cache_creation_tokens: 0 }),
    ).toBe(1);
  });

  it("is 0 when there are no cache reads", () => {
    expect(
      cacheHitRate({ input_tokens: 1000, cache_read_tokens: 0, cache_creation_tokens: 0 }),
    ).toBe(0);
  });

  it("returns null (undefined rate) when there are no input-side tokens", () => {
    expect(
      cacheHitRate({ input_tokens: 0, cache_read_tokens: 0, cache_creation_tokens: 0 }),
    ).toBeNull();
  });

  it("coerces malformed / negative counts to 0 rather than throwing", () => {
    expect(
      cacheHitRate({ input_tokens: Number.NaN, cache_read_tokens: -5, cache_creation_tokens: 100 }),
    ).toBe(0);
    expect(
      cacheHitRate({ input_tokens: -1, cache_read_tokens: -1, cache_creation_tokens: -1 }),
    ).toBeNull();
  });
});

describe("formatHitRate", () => {
  it("renders a percentage with one decimal", () => {
    expect(formatHitRate(0.732)).toBe("73.2%");
    expect(formatHitRate(1)).toBe("100.0%");
    expect(formatHitRate(0)).toBe("0.0%");
  });

  it("renders an em dash for a null rate", () => {
    expect(formatHitRate(null)).toBe("—");
  });
});
