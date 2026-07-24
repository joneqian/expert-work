/**
 * form_model_output_schema.test.ts — config-page redesign v2 Task 7's
 * structured-output field editor (``readOutputSchemaRows``/
 * ``setOutputSchemaRows``, form_model.ts). Split from ``form_model.test.ts``
 * for the same reason ``form_model_profiles.test.ts`` was: a single,
 * self-contained feature over one manifest path.
 *
 * The linchpin invariants:
 *  - round-trip: rows → setOutputSchemaRows → readOutputSchemaRows returns
 *    the SAME rows for every flat shape (required/description/both array
 *    item types).
 *  - the unrepresentable guardrail: anything not flat (nested object
 *    property, ``$ref``, ``oneOf``, an extra top-level key, a malformed
 *    ``required``) reads as ``"unrepresentable"`` — never silently
 *    misread as an empty/partial row list.
 *  - ``name``/``strict`` (and any other unknown sibling key) on an existing
 *    ``output_schema`` block survive a rows write untouched.
 */
import { describe, expect, it } from "vitest";

import {
  readOutputSchemaRows,
  setOutputSchemaRows,
  type AgentManifest,
  type SchemaFieldRow,
} from "../form_model";

const BLANK: AgentManifest = { spec: {} };

describe("readOutputSchemaRows", () => {
  it("returns undefined when output_schema is absent", () => {
    expect(readOutputSchemaRows(BLANK)).toBeUndefined();
  });

  it("returns undefined when output_schema is explicit null", () => {
    const m = { spec: { output_schema: null } };
    expect(readOutputSchemaRows(m)).toBeUndefined();
  });

  it("reads a flat schema back as rows (string/number/integer/boolean/array_string/array_number)", () => {
    const m = {
      spec: {
        output_schema: {
          json_schema: {
            type: "object",
            properties: {
              title: { type: "string", description: "the title" },
              score: { type: "number" },
              rank: { type: "integer" },
              ok: { type: "boolean" },
              tags: { type: "array", items: { type: "string" } },
              scores: { type: "array", items: { type: "number" } },
            },
            required: ["title", "ok"],
            additionalProperties: false,
          },
        },
      },
    };
    const rows = readOutputSchemaRows(m);
    expect(rows).toEqual([
      { name: "title", type: "string", required: true, description: "the title" },
      { name: "score", type: "number", required: false, description: "" },
      { name: "rank", type: "integer", required: false, description: "" },
      { name: "ok", type: "boolean", required: true, description: "" },
      { name: "tags", type: "array_string", required: false, description: "" },
      { name: "scores", type: "array_number", required: false, description: "" },
    ]);
  });

  it("reads a schema with no properties key as an empty row list", () => {
    const m = { spec: { output_schema: { json_schema: { type: "object" } } } };
    expect(readOutputSchemaRows(m)).toEqual([]);
  });

  it("reads a schema with no type key (defaults to object) as flat rows", () => {
    const m = {
      spec: {
        output_schema: {
          json_schema: { properties: { a: { type: "string" } } },
        },
      },
    };
    expect(readOutputSchemaRows(m)).toEqual([
      { name: "a", type: "string", required: false, description: "" },
    ]);
  });

  it("unrepresentable: top-level type is not 'object'", () => {
    const m = {
      spec: { output_schema: { json_schema: { type: "array" } } },
    };
    expect(readOutputSchemaRows(m)).toBe("unrepresentable");
  });

  it("unrepresentable: nested object property", () => {
    const m = {
      spec: {
        output_schema: {
          json_schema: {
            type: "object",
            properties: { nested: { type: "object", properties: {} } },
          },
        },
      },
    };
    expect(readOutputSchemaRows(m)).toBe("unrepresentable");
  });

  it("unrepresentable: $ref property", () => {
    const m = {
      spec: {
        output_schema: {
          json_schema: {
            type: "object",
            properties: { a: { $ref: "#/definitions/Foo" } },
          },
        },
      },
    };
    expect(readOutputSchemaRows(m)).toBe("unrepresentable");
  });

  it("unrepresentable: top-level oneOf", () => {
    const m = {
      spec: {
        output_schema: {
          json_schema: { oneOf: [{ type: "object" }, { type: "string" }] },
        },
      },
    };
    expect(readOutputSchemaRows(m)).toBe("unrepresentable");
  });

  it("unrepresentable: extra top-level key", () => {
    const m = {
      spec: {
        output_schema: {
          json_schema: {
            type: "object",
            properties: {},
            $schema: "http://json-schema.org/draft-07/schema#",
          },
        },
      },
    };
    expect(readOutputSchemaRows(m)).toBe("unrepresentable");
  });

  it("unrepresentable: required contains a name not in properties", () => {
    const m = {
      spec: {
        output_schema: {
          json_schema: {
            type: "object",
            properties: { a: { type: "string" } },
            required: ["a", "ghost"],
          },
        },
      },
    };
    expect(readOutputSchemaRows(m)).toBe("unrepresentable");
  });

  it("unrepresentable: array item type outside string/number", () => {
    const m = {
      spec: {
        output_schema: {
          json_schema: {
            type: "object",
            properties: { a: { type: "array", items: { type: "boolean" } } },
          },
        },
      },
    };
    expect(readOutputSchemaRows(m)).toBe("unrepresentable");
  });

  it("unrepresentable: items key on a scalar (non-array) property", () => {
    const m = {
      spec: {
        output_schema: {
          json_schema: {
            type: "object",
            properties: { a: { type: "string", items: { type: "string" } } },
          },
        },
      },
    };
    expect(readOutputSchemaRows(m)).toBe("unrepresentable");
  });

  it("unrepresentable: json_schema missing/not an object", () => {
    expect(readOutputSchemaRows({ spec: { output_schema: {} } })).toBe(
      "unrepresentable",
    );
    expect(
      readOutputSchemaRows({ spec: { output_schema: { json_schema: "nope" } } }),
    ).toBe("unrepresentable");
  });
});

describe("setOutputSchemaRows", () => {
  it("rows === null deletes the whole output_schema block", () => {
    const m = {
      spec: { output_schema: { json_schema: { type: "object", properties: {} } } },
    };
    const next = setOutputSchemaRows(m, null);
    expect(next.spec?.output_schema).toBeUndefined();
  });

  it("empty rows write the documented non-empty empty-object schema", () => {
    const next = setOutputSchemaRows(BLANK, []);
    expect(next.spec?.output_schema).toEqual({
      json_schema: { type: "object", properties: {}, additionalProperties: false },
    });
  });

  it("two fields produce the exact documented json_schema shape", () => {
    const rows: SchemaFieldRow[] = [
      { name: "title", type: "string", required: true, description: "the title" },
      { name: "count", type: "integer", required: false, description: "" },
    ];
    const next = setOutputSchemaRows(BLANK, rows);
    expect(next.spec?.output_schema).toEqual({
      json_schema: {
        type: "object",
        properties: {
          title: { type: "string", description: "the title" },
          count: { type: "integer" },
        },
        required: ["title"],
        additionalProperties: false,
      },
    });
  });

  it("preserves an existing name and strict:false across a rows write", () => {
    const m = {
      spec: {
        output_schema: {
          name: "custom",
          strict: false,
          json_schema: { type: "object", properties: {} },
        },
      },
    };
    const rows: SchemaFieldRow[] = [
      { name: "a", type: "string", required: false, description: "" },
    ];
    const next = setOutputSchemaRows(m, rows);
    expect(next.spec?.output_schema?.name).toBe("custom");
    expect(next.spec?.output_schema?.strict).toBe(false);
    expect(next.spec?.output_schema?.json_schema).toEqual({
      type: "object",
      properties: { a: { type: "string" } },
      required: [],
      additionalProperties: false,
    });
  });

  it("a brand-new block (rows on a previously-unconfigured manifest) writes no name/strict", () => {
    const next = setOutputSchemaRows(BLANK, []);
    expect(next.spec?.output_schema).not.toHaveProperty("name");
    expect(next.spec?.output_schema).not.toHaveProperty("strict");
  });
});

describe("round-trip", () => {
  const CASES: SchemaFieldRow[][] = [
    [],
    [{ name: "title", type: "string", required: true, description: "" }],
    [
      { name: "a", type: "string", required: true, description: "desc a" },
      { name: "b", type: "number", required: false, description: "" },
      { name: "c", type: "integer", required: true, description: "desc c" },
      { name: "d", type: "boolean", required: false, description: "" },
      { name: "e", type: "array_string", required: true, description: "list" },
      { name: "f", type: "array_number", required: false, description: "" },
    ],
  ];

  it.each(CASES.map((rows, i) => [i, rows] as const))(
    "rows[%i] round-trips through set → read",
    (_i, rows) => {
      const next = setOutputSchemaRows(BLANK, rows);
      expect(readOutputSchemaRows(next)).toEqual(rows);
    },
  );

  it("round-trips on top of an existing name/strict block", () => {
    const m = {
      spec: {
        output_schema: { name: "review_verdict", strict: true, json_schema: {} },
      },
    };
    const rows: SchemaFieldRow[] = [
      { name: "verdict", type: "string", required: true, description: "" },
    ];
    const next = setOutputSchemaRows(m, rows);
    expect(readOutputSchemaRows(next)).toEqual(rows);
    expect(next.spec?.output_schema?.name).toBe("review_verdict");
  });
});
