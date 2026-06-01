import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import Form from "@rjsf/antd";
import validator from "@rjsf/validator-ajv8";

describe("rjsf compat smoke", () => {
  it("renders a string field from a schema", () => {
    const schema = {
      type: "object",
      properties: { greeting: { type: "string", title: "Greeting" } },
    } as const;
    render(<Form schema={schema} validator={validator} />);
    expect(screen.getByText("Greeting")).toBeInTheDocument();
  });

  it("validateFormData reports a missing required field", () => {
    const schema = {
      type: "object",
      required: ["greeting"],
      properties: { greeting: { type: "string" } },
    } as const;
    // @rjsf/validator-ajv8 v5.24.x: validateFormData(formData, schema, ...) => { errors, errorSchema }
    const result = validator.validateFormData({}, schema);
    expect(result.errors.length).toBeGreaterThan(0);
  });
});
