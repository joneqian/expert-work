import { describe, expect, it } from "vitest";

import { fmtDuration } from "../duration_format";

describe("fmtDuration", () => {
  it("renders sub-second as integer ms", () => {
    expect(fmtDuration(0)).toBe("0ms");
    expect(fmtDuration(820)).toBe("820ms");
    expect(fmtDuration(999)).toBe("999ms");
  });
  it("renders seconds with one decimal at/above 1s", () => {
    expect(fmtDuration(1000)).toBe("1.0s");
    expect(fmtDuration(1200)).toBe("1.2s");
    expect(fmtDuration(59900)).toBe("59.9s");
  });
  it("renders minutes+seconds at/above 60s", () => {
    expect(fmtDuration(60000)).toBe("1m0s");
    expect(fmtDuration(62000)).toBe("1m2s");
  });
  it("carries rounding that would hit 60s into the next minute", () => {
    expect(fmtDuration(119500)).toBe("2m0s"); // 1m + round(59.5)=60 → 2m0s
  });
  it("promotes the 59950–59999ms seam to 1m0s instead of '60.0s'", () => {
    expect(fmtDuration(59950)).toBe("1m0s");
    expect(fmtDuration(59999)).toBe("1m0s");
    expect(fmtDuration(59949)).toBe("59.9s"); // just below the seam, still seconds
  });
});
