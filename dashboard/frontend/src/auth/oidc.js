// OpenID Connect client wired against the Keycloak realm provisioned by
// ansible phase 08. Uses oidc-client-ts in browser mode with the PKCE
// authorization-code flow. Token is held in sessionStorage so it survives
// reloads but not browser shutdown.
//
// All endpoints are derived from a single env-driven authority URL so no
// hostname is hardcoded. See docs/security/iam.md for the realm structure.

import { UserManager, WebStorageStateStore } from "oidc-client-ts";
import { env } from "../runtime-env";

// When VITE_AUTH_ENABLED is "false" (or unset) the AuthContext skips the
// login redirect entirely and the app behaves as before. Useful while the
// backend runs with DASHBOARD_SKIP_AUTH=true.
export const AUTH_ENABLED = env("VITE_AUTH_ENABLED", "false") === "true";

// Authority is the realm root. The dashboard ships under two domains
// (prod cluster pod, opt-in dev frontend) so the authority must be
// same-origin per request, otherwise the browser fights a cross-origin
// .well-known/openid-configuration fetch behind Cloudflare Access. The
// SPA reverse proxies /auth/ to Keycloak on its own origin (nginx in the
// cluster pod, Vite proxy in dev), so derive the realm URL from
// window.location.origin plus the operator-defined prefix and realm.
//
// VITE_KEYCLOAK_AUTHORITY still honored as an explicit override for
// deployments that pin a single Keycloak hostname across many origins.
const PATH_PREFIX = env("VITE_KEYCLOAK_PATH_PREFIX", "");
const REALM = env("VITE_KEYCLOAK_REALM", "5g-testbed");
const AUTHORITY = env("VITE_KEYCLOAK_AUTHORITY")
  || `${typeof window !== "undefined" ? window.location.origin : ""}${PATH_PREFIX}/realms/${REALM}`;
const CLIENT_ID = env("VITE_KEYCLOAK_CLIENT_ID", "dashboard");

function buildUserManager() {
  if (!AUTH_ENABLED || !AUTHORITY) return null;
  return new UserManager({
    authority: AUTHORITY,
    client_id: CLIENT_ID,
    redirect_uri: `${window.location.origin}/auth/callback`,
    post_logout_redirect_uri: window.location.origin,
    response_type: "code",
    scope: "openid profile email",
    automaticSilentRenew: true,
    userStore: new WebStorageStateStore({ store: window.sessionStorage }),
    loadUserInfo: false,
  });
}

let _userManager = buildUserManager();

export function getUserManager() {
  return _userManager;
}

export function extractRoles(user) {
  if (!user || !user.access_token) return [];
  try {
    const [, payload] = user.access_token.split(".");
    const claims = JSON.parse(atob(payload.replace(/-/g, "+").replace(/_/g, "/")));
    return claims?.realm_access?.roles || [];
  } catch {
    return [];
  }
}
