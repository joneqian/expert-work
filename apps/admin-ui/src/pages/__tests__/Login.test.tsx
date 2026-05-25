/**
 * Login page tests — Stream H.1b PR 2b.
 *
 * Two surfaces, picked by the build-time OIDC config:
 *
 *   - No OIDC env → token-paste form is the primary CTA.
 *   - OIDC env → "Sign in with SSO" is primary; the paste form is
 *     hidden until the user clicks "Developer login".
 *
 * The "Sign in" click handler is exercised indirectly via the
 * ``signIn`` mock — the real OAuth redirect is browser-only and is
 * covered E2E in PR 4.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import "../../i18n";

import { AuthProvider } from "../../auth/AuthContext";
import { setStoredToken } from "../../api/client";
import { Login } from "../Login";

const signInMock = vi.fn().mockResolvedValue(undefined);

vi.mock("../../auth/oidc", () => ({
  isOidcConfigured: vi.fn(),
  signIn: (path?: string) => signInMock(path),
  signOut: vi.fn().mockResolvedValue(undefined),
  registerEvents: () => () => {},
}));

import { isOidcConfigured } from "../../auth/oidc";

const isOidcConfiguredMock = vi.mocked(isOidcConfigured);

beforeEach(() => {
  signInMock.mockClear();
  isOidcConfiguredMock.mockReset();
});

afterEach(() => {
  setStoredToken(null);
});

function renderLogin() {
  return render(
    <MemoryRouter initialEntries={["/login"]}>
      <AuthProvider>
        <Login />
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe("Login — OIDC unavailable", () => {
  it("shows the token form as the primary surface", async () => {
    isOidcConfiguredMock.mockReturnValue(false);
    renderLogin();
    expect(await screen.findByTestId("login-dev-form")).toBeInTheDocument();
    expect(screen.queryByTestId("login-sso")).toBeNull();
    expect(screen.queryByTestId("login-dev-toggle")).toBeNull();
  });
});

describe("Login — OIDC available", () => {
  it("shows SSO button + collapses dev form", async () => {
    isOidcConfiguredMock.mockReturnValue(true);
    renderLogin();
    expect(await screen.findByTestId("login-sso")).toBeInTheDocument();
    expect(screen.queryByTestId("login-dev-form")).toBeNull();
    expect(screen.getByTestId("login-dev-toggle")).toBeInTheDocument();
  });

  it("toggle reveals the dev form", async () => {
    isOidcConfiguredMock.mockReturnValue(true);
    renderLogin();
    const toggle = await screen.findByTestId("login-dev-toggle");
    await userEvent.click(toggle);
    expect(await screen.findByTestId("login-dev-form")).toBeInTheDocument();
  });

  it("SSO button invokes signIn", async () => {
    isOidcConfiguredMock.mockReturnValue(true);
    renderLogin();
    const sso = await screen.findByTestId("login-sso");
    await userEvent.click(sso);
    await waitFor(() => {
      expect(signInMock).toHaveBeenCalledTimes(1);
    });
    // returnPath defaults to /agents when no ``from`` location state.
    expect(signInMock).toHaveBeenCalledWith("/agents");
  });
});
