/**
 * Auth context — Stream H.1b.
 *
 * M0 keeps auth deliberately simple: an operator pastes a JWT or
 * helix API key into ``/login``. We decode the JWT locally (no
 * signature verification — the control-plane re-verifies on every
 * request; the client just needs to read ``tenant_id`` / ``sub_type``
 * to drive UI affordances).
 *
 * For API keys we cannot read claims; the UI shows the prefix as
 * ``displayName`` and disables the TenantSwitcher cross-tenant option
 * until a server-side ``GET /v1/me`` arrives in H.1b PR 2.
 *
 * Stream N integration: ``isSystemAdmin`` is derived from the JWT's
 * ``roles`` claim including ``system_admin``. The middleware on the
 * server side is the source of truth — this is just a UI hint.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { getStoredToken, setStoredToken } from "../api/client";

export interface AuthIdentity {
  /** ``"jwt"`` for OIDC tokens, ``"api_key"`` for helix bearer keys. */
  kind: "jwt" | "api_key";
  /** Subject id (UUID for users / service accounts; "?" for API keys). */
  subject: string;
  /** ``user`` / ``service_account`` / ``service`` (mTLS) — best-effort
   *  from the JWT, falls back to ``api_key`` when token is opaque. */
  subjectType: string;
  /** Home tenant. ``null`` when unknown (API key path). */
  homeTenantId: string | null;
  roles: readonly string[];
  isSystemAdmin: boolean;
  /** Short string for the user menu — JWT subject head or API key prefix. */
  displayName: string;
}

export interface AuthState {
  status: "loading" | "anonymous" | "authenticated";
  identity: AuthIdentity | null;
  token: string | null;
}

interface AuthContextValue extends AuthState {
  login(token: string): void;
  logout(): void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

function decodeJwtPayload(token: string): Record<string, unknown> | null {
  const parts = token.split(".");
  if (parts.length !== 3) return null;
  try {
    const payload = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    const padded = payload.padEnd(payload.length + ((4 - (payload.length % 4)) % 4), "=");
    if (typeof atob !== "function") {
      // Browser/jsdom should always have atob; defend in node-only
      // contexts (eg. SSR pre-render) by returning null instead of
      // pulling in Buffer's node typings.
      return null;
    }
    return JSON.parse(atob(padded));
  } catch {
    return null;
  }
}

function asString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function asStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((v): v is string => typeof v === "string");
}

function identityFromToken(token: string): AuthIdentity {
  // helix API keys are opaque; we cannot read claims.
  if (token.startsWith("aforge_pat_") || token.startsWith("helix_")) {
    return {
      kind: "api_key",
      subject: token.slice(0, 16),
      subjectType: "service_account",
      homeTenantId: null,
      roles: [],
      isSystemAdmin: false,
      displayName: `${token.slice(0, 12)}…`,
    };
  }
  const payload = decodeJwtPayload(token);
  if (payload === null) {
    return {
      kind: "jwt",
      subject: "?",
      subjectType: "user",
      homeTenantId: null,
      roles: [],
      isSystemAdmin: false,
      displayName: "anonymous",
    };
  }
  const subject = asString(payload.sub) ?? "?";
  const roles = asStringArray(payload.roles);
  return {
    kind: "jwt",
    subject,
    subjectType: asString(payload.sub_type) ?? "user",
    homeTenantId: asString(payload.tenant_id),
    roles,
    isSystemAdmin: roles.includes("system_admin"),
    displayName: subject.length > 12 ? `${subject.slice(0, 8)}…` : subject,
  };
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({
    status: "loading",
    identity: null,
    token: null,
  });

  useEffect(() => {
    const stored = getStoredToken();
    if (stored) {
      setState({
        status: "authenticated",
        identity: identityFromToken(stored),
        token: stored,
      });
    } else {
      setState({ status: "anonymous", identity: null, token: null });
    }
  }, []);

  const login = useCallback((token: string) => {
    setStoredToken(token);
    setState({
      status: "authenticated",
      identity: identityFromToken(token),
      token,
    });
  }, []);

  const logout = useCallback(() => {
    setStoredToken(null);
    setState({ status: "anonymous", identity: null, token: null });
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ ...state, login, logout }),
    [state, login, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (value === null) {
    throw new Error("useAuth() must be called inside <AuthProvider>");
  }
  return value;
}

export { identityFromToken as _identityFromTokenForTests };
