/**
 * Silent renew target — Stream H.1b PR 2b.
 *
 * oidc-client-ts mounts an invisible iframe pointing at the
 * ``silent_redirect_uri`` whenever the access token is about to expire.
 * The page here only needs to call ``signinSilentCallback`` so the
 * library can finalize the renewal — there is no UI to render.
 */
import { useEffect } from "react";

import { getUserManager } from "../auth/oidc";

export function AuthSilent() {
  useEffect(() => {
    const manager = getUserManager();
    if (manager === null) return;
    void manager.signinSilentCallback().catch(() => {
      // Errors here propagate via the UserManager events the parent
      // window subscribes to (silent-renew-error); the iframe just
      // needs to swallow rejections so it doesn't surface as an
      // uncaught error in the parent.
    });
  }, []);
  return null;
}
