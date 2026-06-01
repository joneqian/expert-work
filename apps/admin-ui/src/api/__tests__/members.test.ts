/**
 * Members SDK tests — Stream R W2.
 *
 * Each function is a thin wrapper that builds a URL + query/body and
 * unwraps the envelope. We swap the axios adapter to capture the wire
 * shape (URL + method + params + body) and assert it matches the
 * ``/v1/members`` contract.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import type { InternalAxiosRequestConfig } from "axios";

import { apiClient } from "../client";
import {
  inviteMembers,
  listMembers,
  resendMember,
  revokeMember,
  type InvitationItem,
} from "../members";

interface Capture {
  url: string;
  params: Record<string, unknown> | undefined;
  data: unknown;
  method: string;
}

function captureAdapter(body: unknown, status = 200) {
  const calls: Capture[] = [];
  apiClient.defaults.adapter = (config: InternalAxiosRequestConfig) => {
    calls.push({
      url: config.url ?? "",
      params: config.params as Record<string, unknown> | undefined,
      data: config.data,
      method: (config.method ?? "get").toLowerCase(),
    });
    return Promise.resolve({
      data: body,
      status,
      statusText: "OK",
      headers: {},
      config,
      request: {},
    });
  };
  return calls;
}

function listEnvelope() {
  return { success: true, data: { items: [], total: 0 }, error: null };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("listMembers", () => {
  it("GETs /v1/members and unwraps the envelope", async () => {
    const calls = captureAdapter({
      success: true,
      data: { items: [], total: 0 },
      error: null,
    });
    const result = await listMembers();
    expect(calls[0].url).toBe("/v1/members");
    expect(calls[0].method).toBe("get");
    expect(result).toEqual({ items: [], total: 0 });
  });

  it("threads the status filter into params", async () => {
    const calls = captureAdapter(listEnvelope());
    await listMembers({ status: "invited", limit: 25, offset: 50 });
    expect(calls[0].params?.status).toBe("invited");
    expect(calls[0].params?.limit).toBe(25);
    expect(calls[0].params?.offset).toBe(50);
  });

  it("omits status when not provided", async () => {
    const calls = captureAdapter(listEnvelope());
    await listMembers();
    expect(calls[0].params?.status).toBeUndefined();
  });
});

describe("inviteMembers", () => {
  it("POSTs /v1/members/invite with the invitations array", async () => {
    const calls = captureAdapter({
      success: true,
      data: {
        results: [
          {
            email: "a@example.com",
            member_id: "m1",
            status: "invited",
            error_code: null,
          },
        ],
      },
      error: null,
    });
    const invitations: InvitationItem[] = [
      { email: "a@example.com", role: "viewer", display_name: "Ann" },
    ];
    const result = await inviteMembers(invitations);
    expect(calls[0].url).toBe("/v1/members/invite");
    expect(calls[0].method).toBe("post");
    const parsed = JSON.parse(calls[0].data as string);
    expect(parsed.invitations).toEqual(invitations);
    expect(result.results[0].member_id).toBe("m1");
    expect(result.results[0].error_code).toBeNull();
  });

  it("surfaces per-item error_code from the envelope", async () => {
    captureAdapter({
      success: true,
      data: {
        results: [
          {
            email: "dup@example.com",
            member_id: null,
            status: null,
            error_code: "MEMBER_KEYCLOAK_CONFLICT",
          },
        ],
      },
      error: null,
    });
    const result = await inviteMembers([
      { email: "dup@example.com", role: "operator" },
    ]);
    expect(result.results[0].error_code).toBe("MEMBER_KEYCLOAK_CONFLICT");
    expect(result.results[0].member_id).toBeNull();
  });
});

describe("resendMember", () => {
  it("POSTs the id-scoped /resend path", async () => {
    const calls = captureAdapter({
      success: true,
      data: { member_id: "m1", status: "invited", keycloak_user_id: "kc1" },
      error: null,
    });
    const result = await resendMember("m1");
    expect(calls[0].url).toBe("/v1/members/m1/resend");
    expect(calls[0].method).toBe("post");
    expect(result.keycloak_user_id).toBe("kc1");
  });

  it("URL-encodes the member id", async () => {
    const calls = captureAdapter({
      success: true,
      data: { member_id: "a/b", status: "invited", keycloak_user_id: null },
      error: null,
    });
    await resendMember("a/b");
    expect(calls[0].url).toBe("/v1/members/a%2Fb/resend");
  });
});

describe("revokeMember", () => {
  it("DELETEs the id-scoped path", async () => {
    const calls = captureAdapter("", 204);
    await revokeMember("m1");
    expect(calls[0].url).toBe("/v1/members/m1");
    expect(calls[0].method).toBe("delete");
  });
});
