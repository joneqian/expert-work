import { describe, expect, it } from "vitest";
import { cleanUntrusted } from "../untrusted_clean";

describe("cleanUntrusted", () => {
  it("strips the UNTRUSTED fence + ▁ glyph and flags hadUntrusted", () => {
    const raw = "«UNTRUSTED nonce=0ce9b28d1a1e»\n2026年▁ 12时▁ 星期一\n«/UNTRUSTED nonce=0ce9b28d1a1e»";
    const { text, hadUntrusted } = cleanUntrusted(raw);
    expect(hadUntrusted).toBe(true);
    expect(text).toBe("2026年 12时 星期一");
    expect(text).not.toContain("▁");
    expect(text).not.toContain("UNTRUSTED");
  });
  it("passes clean text through untouched", () => {
    const { text, hadUntrusted } = cleanUntrusted("hello world");
    expect(text).toBe("hello world");
    expect(hadUntrusted).toBe(false);
  });
  it("strips the ▁ glyph even without a fence, and hadUntrusted stays false", () => {
    const raw = "2026年▁ 12时▁ 星期一";
    const { text, hadUntrusted } = cleanUntrusted(raw);
    expect(hadUntrusted).toBe(false);
    expect(text).toBe("2026年 12时 星期一");
    expect(text).not.toContain("▁");
  });
});
