import { describe, expect, it } from "vitest";

import { collectHeaders } from "./CreateMcpServerDrawer";

describe("collectHeaders", () => {
  it("returns undefined when there are no rows", () => {
    expect(collectHeaders(undefined)).toBeUndefined();
    expect(collectHeaders([])).toBeUndefined();
  });

  it("skips incomplete rows (blank key or value)", () => {
    expect(
      collectHeaders([
        { key: "", value: "v" },
        { key: "X-Org", value: "  " },
      ]),
    ).toBeUndefined();
  });

  it("collects complete rows and trims the key", () => {
    expect(
      collectHeaders([
        { key: " X-API-Key ", value: "secret" },
        { key: "X-Org", value: "acme" },
        { key: "X-Blank", value: "" },
      ]),
    ).toEqual({ "X-API-Key": "secret", "X-Org": "acme" });
  });
});
