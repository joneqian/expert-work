/**
 * Stream 8.5 — pure helpers for the role-binding ABAC conditions editor.
 *
 * The antd Select/Form interactions are covered by the page test; these cover
 * the deterministic parse/build/summarise logic that shapes the POST body.
 */
import { describe, expect, it } from "vitest";

import {
  buildConditions,
  parseLabels,
  summariseConditions,
} from "../SettingsRoleBindings";

describe("parseLabels", () => {
  it("parses k=v pairs and trims whitespace", () => {
    expect(parseLabels("team=支持, env = dev")).toEqual({ team: "支持", env: "dev" });
  });

  it("ignores blanks and malformed pairs", () => {
    expect(parseLabels("")).toEqual({});
    expect(parseLabels(undefined)).toEqual({});
    expect(parseLabels("nokey, =noval, ok=1")).toEqual({ ok: "1" });
  });
});

describe("buildConditions", () => {
  it("returns undefined when no predicate is set", () => {
    expect(buildConditions({})).toBeUndefined();
    expect(buildConditions({ resource_ids: [], labels_raw: "", owner_only: false })).toBeUndefined();
  });

  it("includes only the predicates that are set", () => {
    expect(buildConditions({ resource_ids: ["agent-foo"] })).toEqual({
      resource_ids: ["agent-foo"],
    });
    expect(buildConditions({ labels_raw: "team=支持" })).toEqual({
      labels: { team: "支持" },
    });
    expect(buildConditions({ owner_only: true })).toEqual({ owner_only: true });
  });

  it("combines all three predicates", () => {
    expect(
      buildConditions({
        resource_ids: ["a", "b"],
        labels_raw: "team=支持",
        owner_only: true,
      }),
    ).toEqual({
      resource_ids: ["a", "b"],
      labels: { team: "支持" },
      owner_only: true,
    });
  });

  it("drops blank resource ids", () => {
    expect(buildConditions({ resource_ids: ["", "  ", "x"] })).toEqual({
      resource_ids: ["x"],
    });
  });
});

describe("summariseConditions", () => {
  it("returns null for unconditioned bindings", () => {
    expect(summariseConditions(null)).toBeNull();
    expect(summariseConditions(undefined)).toBeNull();
    expect(summariseConditions({})).toBeNull();
  });

  it("summarises ids count, labels, owner", () => {
    expect(
      summariseConditions({
        resource_ids: ["a", "b"],
        labels: { team: "支持" },
        owner_only: true,
      }),
    ).toBe("ids:2 · team=支持 · owner");
  });
});
