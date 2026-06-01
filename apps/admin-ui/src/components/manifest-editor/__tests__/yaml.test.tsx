import { describe, expect, it } from "vitest";
import { dumpYaml, parseYaml } from "../yaml";

describe("yaml helpers", () => {
  it("round-trips an object without losing fields", () => {
    const obj = { a: 1, b: { c: ["x", "y"], d: true }, e: "hello" };
    const restored = parseYaml(dumpYaml(obj));
    expect(restored).toEqual(obj);
  });

  it("parseYaml throws on malformed YAML", () => {
    expect(() => parseYaml("a:\n  - b\n - c")).toThrow();
  });

  it("dumpYaml emits block style, not inline JSON", () => {
    const text = dumpYaml({ model: { provider: "deepseek" } });
    expect(text).toContain("model:");
    expect(text).toContain("provider: deepseek");
  });
});
