/**
 * OIDC code-flow integration — Stream H.1b PR 2b.
 *
 * Thin wrapper over ``oidc-client-ts`` :class:`UserManager`. The library
 * does the heavy lifting (PKCE challenge, JWKS validation, silent
 * renewal); this module narrows the surface to the four operations
 * the UI calls:
 *
 *   - :func:`signIn`   — kick off the redirect to the IdP.
 *   - :func:`handleCallback` — finalize after the IdP redirects back.
 *   - :func:`signOut`  — clear local session (+ optional IdP-side
 *     end-session if the discovery doc advertises one).
 *   - :func:`registerEvents` — subscribe AuthContext to silent-renew
 *     + expiry events so it can swap the token in place.
 *
 * Configuration is build-time via Vite ``import.meta.env`` so a single
 * SPA bundle still works against any IdP — operator only changes the
 * env at deploy. The token-paste fallback in :ref:`Login` is preserved
 * for developers + API key users; this module short-circuits with
 * :func:`isOidcConfigured` when no IdP is configured.
 */
import { UserManager, WebStorageStateStore, type User, type UserManagerSettings } from "oidc-client-ts";

/** Build-time OIDC config — supplied via ``VITE_OIDC_*`` env vars. */
export interface OidcConfig {
  issuer: string;
  clientId: string;
  /** API audience the IdP should put in the ``aud`` claim. Expert Work's
   *  JWTVerifier checks this against the configured backend audience.
   *  When unset, oidc-client-ts uses ``client_id`` (default per OIDC
   *  spec). */
  audience?: string;
  /** OIDC scopes — ``openid profile email`` covers the common case. */
  scopes: string;
  /** Where the IdP redirects to after the user authenticates. */
  redirectUri: string;
  /** Where the IdP redirects after logout (when end-session supported). */
  postLogoutRedirectUri: string;
}

function readEnv(key: string): string | undefined {
  // ``import.meta.env`` is replaced at build time; reading dynamically
  // (via bracket access) breaks Vite's substitution. The pattern below
  // keeps the constant-folded behaviour while still letting us iterate
  // over keys in tests via ``vi.stubEnv``.
  const env = import.meta.env as Record<string, string | undefined>;
  const value = env[key];
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

export function readOidcConfig(): OidcConfig | null {
  const issuer = readEnv("VITE_OIDC_ISSUER");
  const clientId = readEnv("VITE_OIDC_CLIENT_ID");
  if (issuer === undefined || clientId === undefined) {
    return null;
  }
  const origin = typeof window === "undefined" ? "" : window.location.origin;
  return {
    issuer,
    clientId,
    audience: readEnv("VITE_OIDC_AUDIENCE"),
    scopes: readEnv("VITE_OIDC_SCOPES") ?? "openid profile email",
    redirectUri: readEnv("VITE_OIDC_REDIRECT_URI") ?? `${origin}/auth/callback`,
    postLogoutRedirectUri:
      readEnv("VITE_OIDC_POST_LOGOUT_REDIRECT_URI") ?? `${origin}/login`,
  };
}

export function isOidcConfigured(): boolean {
  return readOidcConfig() !== null;
}

let cachedManager: UserManager | null = null;

/** Build the UserManager lazily — most pages don't need it, and
 *  ``oidc-client-ts`` would otherwise schedule renewal timers at
 *  import time. */
export function getUserManager(): UserManager | null {
  if (cachedManager !== null) return cachedManager;
  const config = readOidcConfig();
  if (config === null) return null;
  const extraQueryParams: Record<string, string> = {};
  if (config.audience !== undefined) {
    extraQueryParams.audience = config.audience;
  }
  const settings: UserManagerSettings = {
    authority: config.issuer,
    client_id: config.clientId,
    redirect_uri: config.redirectUri,
    post_logout_redirect_uri: config.postLogoutRedirectUri,
    response_type: "code",
    scope: config.scopes,
    automaticSilentRenew: true,
    // Silent renew uses an invisible iframe — anchor it at a stable
    // route so the renew callback knows where to hand off. Keycloak,
    // Okta, Auth0 all support this; some IdPs require listing
    // /auth/silent as an allowed redirect URI alongside /auth/callback.
    silent_redirect_uri: config.redirectUri.replace(/\/callback$/, "/silent"),
    loadUserInfo: false,
    // Persist user state in sessionStorage (not localStorage) so the
    // session is tab-scoped; an operator logged into two tenants in
    // separate windows doesn't accidentally cross identities.
    userStore: new WebStorageStateStore({
      store: typeof window === "undefined" ? undefined : window.sessionStorage,
      prefix: "expert_work.admin.oidc.",
    }),
    extraQueryParams,
  };
  cachedManager = new UserManager(settings);
  return cachedManager;
}

/** Test-only: reset the singleton between cases. */
export function _resetUserManagerForTests(): void {
  cachedManager = null;
  callbackPromise = null;
}

export async function signIn(returnPath = "/agents"): Promise<void> {
  const manager = getUserManager();
  if (manager === null) {
    throw new Error("OIDC is not configured");
  }
  // Start the sign-in from a clean slate. A stale OIDC session left in the store
  // keeps ``automaticSilentRenew`` re-loading an expired user, churning the auth
  // state; combined with leftover interrupted sign-in state, that is the kind of
  // thing that used to need a manual "clear site data" before SSO would work.
  // ``removeUser`` drops the stale session (stopping the renew churn) and
  // ``clearStaleState`` prunes abandoned sign-in state. Both are best-effort — a
  // failed purge must not block the redirect.
  await manager.removeUser().catch(() => {});
  await manager.clearStaleState().catch(() => {});
  await manager.signinRedirect({ state: { returnPath } });
}

export interface SignInResult {
  /** id_token used as the bearer the expert_work backend verifies. */
  idToken: string;
  /** ``state.returnPath`` from :func:`signIn`, or ``"/agents"`` default. */
  returnPath: string;
}

/** In-flight (and settled) code-exchange promise. The authorization code
 *  is single-use, but React StrictMode mounts the callback effect twice in
 *  dev (and a stray re-render could re-invoke it anywhere), so a second
 *  ``signinRedirectCallback`` would hit the IdP with an already-redeemed
 *  code and fail "Code not valid". Caching the promise makes duplicate
 *  calls share one exchange. A full-page redirect from the IdP reloads the
 *  module, so the cache is naturally scoped to a single callback navigation. */
let callbackPromise: Promise<SignInResult> | null = null;

export async function handleCallback(): Promise<SignInResult> {
  if (callbackPromise !== null) return callbackPromise;
  const manager = getUserManager();
  if (manager === null) {
    // Not cached: a misconfiguration isn't a consumed-code situation.
    throw new Error("OIDC is not configured");
  }
  callbackPromise = manager.signinRedirectCallback().then(extractSignInResult);
  return callbackPromise;
}

export async function signOut(): Promise<void> {
  const manager = getUserManager();
  if (manager === null) return;
  // Local sign-out always works; remote (IdP) end-session is
  // best-effort because some IdPs require the id_token_hint.
  await manager.removeUser();
  try {
    await manager.signoutRedirect();
  } catch {
    // Silent failure — local session is already cleared.
  }
}

export function extractSignInResult(user: User): SignInResult {
  if (user.id_token === undefined) {
    throw new Error("OIDC response missing id_token");
  }
  const state = user.state as { returnPath?: string } | undefined;
  return {
    idToken: user.id_token,
    returnPath: state?.returnPath ?? "/agents",
  };
}

export interface OidcEventHandlers {
  /** Fired when oidc-client-ts has refreshed the token (silent renew
   *  or explicit) — AuthContext should swap the token in storage. */
  onUserLoaded(idToken: string): void;
  /** Token expired without a successful renew — AuthContext should
   *  log out. */
  onExpired(): void;
  /** Background renew failure — surfaced but doesn't force logout. */
  onSilentRenewError(error: Error): void;
}

/** Subscribe to UserManager events. Returns an unsubscribe callback.
 *  No-op when OIDC isn't configured. */
export function registerEvents(handlers: OidcEventHandlers): () => void {
  const manager = getUserManager();
  if (manager === null) return () => {};

  const userLoaded = (user: User) => {
    if (user.id_token !== undefined) {
      handlers.onUserLoaded(user.id_token);
    }
  };
  const expired = () => handlers.onExpired();
  const silentRenewError = (error: Error) => handlers.onSilentRenewError(error);

  manager.events.addUserLoaded(userLoaded);
  manager.events.addAccessTokenExpired(expired);
  manager.events.addSilentRenewError(silentRenewError);

  return () => {
    manager.events.removeUserLoaded(userLoaded);
    manager.events.removeAccessTokenExpired(expired);
    manager.events.removeSilentRenewError(silentRenewError);
  };
}
