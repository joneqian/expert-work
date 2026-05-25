/**
 * OIDC module tests — Stream H.1b PR 2b.
 *
 * The real ``UserManager`` requires a working browser environment +
 * IdP. We test only the wiring this module owns:
 *
 *   - :func:`isOidcConfigured` reads the env correctly
 *   - :func:`extractSignInResult` returns ``idToken`` + ``returnPath``
 *   - :func:`signIn` / :func:`handleCallback` / :func:`signOut` short-
 *     circuit when not configured (no crash on token-paste-only
 *     deploys).
 *
 * End-to-end OAuth flow is exercised by Playwright in PR 4 against
 * the local Keycloak documented in ``docs/dev/oidc-keycloak.md``.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import type { User } from "oidc-client-ts";

import {
  _resetUserManagerForTests,
  extractSignInResult,
  handleCallback,
  isOidcConfigured,
  readOidcConfig,
  signIn,
} from "../oidc";

afterEach(() => {
  vi.unstubAllEnvs();
  _resetUserManagerForTests();
});

describe("OIDC config detection", () => {
  it("returns null when neither issuer nor client_id are set", () => {
    expect(readOidcConfig()).toBeNull();
    expect(isOidcConfigured()).toBe(false);
  });

  it("requires both issuer and client_id to declare configured", () => {
    vi.stubEnv("VITE_OIDC_ISSUER", "https://keycloak.example/realms/helix");
    expect(isOidcConfigured()).toBe(false);
    vi.stubEnv("VITE_OIDC_CLIENT_ID", "helix-admin-ui");
    expect(isOidcConfigured()).toBe(true);
    const config = readOidcConfig();
    expect(config?.issuer).toBe("https://keycloak.example/realms/helix");
    expect(config?.clientId).toBe("helix-admin-ui");
    expect(config?.scopes).toBe("openid profile email");
  });

  it("respects custom redirect URIs when provided", () => {
    vi.stubEnv("VITE_OIDC_ISSUER", "https://idp.example");
    vi.stubEnv("VITE_OIDC_CLIENT_ID", "ui");
    vi.stubEnv(
      "VITE_OIDC_REDIRECT_URI",
      "https://admin.example/auth/callback",
    );
    expect(readOidcConfig()?.redirectUri).toBe(
      "https://admin.example/auth/callback",
    );
  });
});

describe("extractSignInResult", () => {
  it("returns the id_token + state.returnPath", () => {
    const user = {
      id_token: "id-token-abc",
      state: { returnPath: "/runs/123" },
    } as unknown as User;
    const result = extractSignInResult(user);
    expect(result.idToken).toBe("id-token-abc");
    expect(result.returnPath).toBe("/runs/123");
  });

  it("falls back to /agents when state is missing", () => {
    const user = { id_token: "id-token-abc" } as unknown as User;
    expect(extractSignInResult(user).returnPath).toBe("/agents");
  });

  it("throws when id_token is missing", () => {
    const user = { state: { returnPath: "/" } } as unknown as User;
    expect(() => extractSignInResult(user)).toThrow(/id_token/);
  });
});

describe("OIDC short-circuit when unconfigured", () => {
  it("signIn rejects with a clear error", async () => {
    await expect(signIn()).rejects.toThrow(/OIDC is not configured/);
  });

  it("handleCallback rejects with a clear error", async () => {
    await expect(handleCallback()).rejects.toThrow(/OIDC is not configured/);
  });
});
