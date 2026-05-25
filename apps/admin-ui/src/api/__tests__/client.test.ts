/**
 * Unit tests for ``api/client`` — Stream H.1b PR 1.
 *
 * Covers the two pieces the UI relies on for Stream N integration:
 *
 *   1. ``withTenantScope`` — omits ``tenant_id`` for the home view,
 *      injects ``"*"`` for cross-tenant, injects a specific UUID for
 *      a tenant switch.
 *   2. ``unwrap`` — surfaces ``error.code`` / ``error.message`` from
 *      the envelope as :class:`ApiError`.
 */
import { describe, expect, it } from "vitest";

import { ApiError, unwrap, withTenantScope } from "../client";

describe("withTenantScope", () => {
  it("returns params unchanged when scope is undefined", () => {
    const got = withTenantScope({ limit: 50 }, undefined);
    expect(got).toEqual({ limit: 50 });
    expect("tenant_id" in got).toBe(false);
  });

  it("injects tenant_id=* for cross-tenant", () => {
    const got = withTenantScope({ status: "active" }, "*");
    expect(got).toEqual({ status: "active", tenant_id: "*" });
  });

  it("injects the specific UUID for a tenant switch", () => {
    const tenantId = "00000000-0000-0000-0000-0000000000a1";
    const got = withTenantScope({}, tenantId);
    expect(got).toEqual({ tenant_id: tenantId });
  });
});

describe("unwrap", () => {
  it("returns data when success=true", () => {
    expect(unwrap({ success: true, data: { x: 1 }, error: null })).toEqual({ x: 1 });
  });

  it("throws ApiError carrying code+message when success=false", () => {
    expect(() =>
      unwrap({
        success: false,
        data: null,
        error: { code: "CROSS_TENANT_FORBIDDEN", message: "nope" },
      }),
    ).toThrowError(ApiError);
  });

  it("throws when data is null even with success=true (defensive)", () => {
    expect(() => unwrap({ success: true, data: null, error: null })).toThrowError(
      "envelope.data was null",
    );
  });
});
