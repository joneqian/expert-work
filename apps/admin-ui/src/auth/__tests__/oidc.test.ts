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

// Fake UserManager so we can count code exchanges without a real IdP.
const { signinSpy, clearStaleStateSpy, signinRedirectSpy } = vi.hoisted(() => ({
  signinSpy: vi.fn(),
  clearStaleStateSpy: vi.fn().mockResolvedValue(undefined),
  signinRedirectSpy: vi.fn().mockResolvedValue(undefined),
}));
vi.mock("oidc-client-ts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("oidc-client-ts")>();
  return {
    ...actual,
    UserManager: class {
      constructor(_settings: unknown) {}
      signinRedirectCallback = signinSpy;
      clearStaleState = clearStaleStateSpy;
      signinRedirect = signinRedirectSpy;
    },
  };
});

afterEach(() => {
  vi.unstubAllEnvs();
  _resetUserManagerForTests();
  signinSpy.mockReset();
  clearStaleStateSpy.mockClear();
  signinRedirectSpy.mockClear();
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

describe("signIn (configured)", () => {
  it("clears stale sign-in state BEFORE redirecting to the IdP", async () => {
    vi.stubEnv("VITE_OIDC_ISSUER", "https://idp.example/realms/helix");
    vi.stubEnv("VITE_OIDC_CLIENT_ID", "ui");
    await signIn("/runs/7");
    expect(clearStaleStateSpy).toHaveBeenCalledTimes(1);
    expect(signinRedirectSpy).toHaveBeenCalledTimes(1);
    // Purge must happen before the redirect, else leftover state can bounce the
    // user back to /login without ever reaching the IdP (the C1 fix).
    expect(clearStaleStateSpy.mock.invocationCallOrder[0]).toBeLessThan(
      signinRedirectSpy.mock.invocationCallOrder[0],
    );
    expect(signinRedirectSpy).toHaveBeenCalledWith({
      state: { returnPath: "/runs/7" },
    });
  });

  it("still redirects when clearStaleState rejects (best-effort purge)", async () => {
    vi.stubEnv("VITE_OIDC_ISSUER", "https://idp.example/realms/helix");
    vi.stubEnv("VITE_OIDC_CLIENT_ID", "ui");
    clearStaleStateSpy.mockRejectedValueOnce(new Error("store unavailable"));
    await signIn();
    expect(signinRedirectSpy).toHaveBeenCalledTimes(1);
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

describe("handleCallback single-use code guard (StrictMode double-invoke)", () => {
  it("exchanges the one-time code only once across duplicate calls", async () => {
    vi.stubEnv("VITE_OIDC_ISSUER", "https://idp.example");
    vi.stubEnv("VITE_OIDC_CLIENT_ID", "ui");
    signinSpy.mockResolvedValue({
      id_token: "tok-1",
      state: { returnPath: "/agents" },
    });

    const [a, b] = await Promise.all([handleCallback(), handleCallback()]);

    expect(signinSpy).toHaveBeenCalledTimes(1);
    expect(a.idToken).toBe("tok-1");
    expect(b.idToken).toBe("tok-1");
    expect(a.returnPath).toBe("/agents");
  });

  it("a sequential second call also reuses the cached exchange", async () => {
    vi.stubEnv("VITE_OIDC_ISSUER", "https://idp.example");
    vi.stubEnv("VITE_OIDC_CLIENT_ID", "ui");
    signinSpy.mockResolvedValue({
      id_token: "tok-2",
      state: { returnPath: "/runs" },
    });

    const first = await handleCallback();
    const second = await handleCallback();

    expect(signinSpy).toHaveBeenCalledTimes(1);
    expect(first.idToken).toBe("tok-2");
    expect(second.idToken).toBe("tok-2");
  });
});
