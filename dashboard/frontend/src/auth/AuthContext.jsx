import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { AUTH_ENABLED, extractRoles, getUserManager, KEYCLOAK_AUTHORITY } from "./oidc";
import { env } from "../runtime-env";

// Keycloak end-session URL for a real RP-initiated logout: a TOP-LEVEL redirect
// carrying id_token_hint plus post_logout_redirect_uri. Both matter. The hint is
// what makes Keycloak honour the requested return URI instead of guessing the
// client's first redirectUri (which is what previously dumped the browser on
// /auth/callback and re-authenticated over SSO). The realm registers both the
// prod and dev origins under post.logout.redirect.uris, so /logged-out matches.
//
// This replaces an earlier hidden-iframe logout: framing Keycloak is blocked by
// `frame-ancestors 'self'` whenever the app is served from the dev origin, so the
// SSO session was never actually terminated and only the local tab forgot it.
function buildEndSessionUrl(idToken, clientId, postLogoutUri) {
  const params = new URLSearchParams();
  params.set("client_id", clientId);
  if (idToken) params.set("id_token_hint", idToken);
  if (postLogoutUri) params.set("post_logout_redirect_uri", postLogoutUri);
  return `${KEYCLOAK_AUTHORITY}/protocol/openid-connect/logout?${params.toString()}`;
}

// AuthContext exposes the currently authenticated user (or null), the role
// list extracted from the JWT, and helpers for login/logout. When
// VITE_AUTH_ENABLED is false the context returns a permissive
// "auth-disabled" shape so existing pages keep working untouched.

const AuthCtx = createContext(null);

// OIDC with PKCE needs crypto.subtle, which browsers give only to secure
// contexts (HTTPS or localhost): on the plain-HTTP LAN address the login cannot
// even start. App.jsx then sends the user to the HTTPS origin instead.
const INSECURE_ORIGIN = AUTH_ENABLED && typeof window !== "undefined" && !window.isSecureContext;

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(AUTH_ENABLED && !INSECURE_ORIGIN);
  // True from the moment logout starts until the browser leaves the page.
  // removeUser() sets the user to null, which would otherwise trip the
  // auto-login effect (App.jsx) and re-authenticate through the still-alive
  // SSO session BEFORE we reach Keycloak's end-session endpoint, bouncing the
  // user straight back to the dashboard via /auth/callback. This flag lets that
  // effect stand down while a logout is in flight.
  const [loggingOut, setLoggingOut] = useState(false);
  // True when this tab had a session and it ended (expired, or not renewed):
  // App.jsx then asks for a click instead of redirecting (lib/authFlow.js).
  const [ended, setEnded] = useState(false);

  useEffect(() => {
    if (!AUTH_ENABLED || INSECURE_ORIGIN) return;
    const um = getUserManager();
    if (!um) {
      setLoading(false);
      return;
    }

    let mounted = true;

    // Warm the OIDC metadata cache so signinRedirect/signoutRedirect do not
    // pay a cross-origin .well-known round-trip on the first user click. On
    // tunneled dev (cross-origin to the IAM authority) that round-trip is
    // ~1s and made the logout button appear unresponsive on the first press.
    um.metadataService?.getMetadata().catch(() => {});

    um.getUser()
      .then((u) => {
        // getUser() returns the stored session even when its access token has
        // already expired. Treating that as "logged in" let the first render
        // fire API calls with a dead token: a burst of 401s, then a reauth
        // redirect. An expired session is no session.
        if (!mounted) return;
        setUser(u && !u.expired ? u : null);
        if (u?.expired) setEnded(true);
      })
      .catch(() => {})
      .finally(() => {
        if (mounted) setLoading(false);
      });

    const onUserLoaded = (u) => {
      if (!mounted) return;
      setUser(u);
      setEnded(false);
    };
    const onUserUnloaded = () => mounted && setUser(null);
    const onExpired = () => {
      if (!mounted) return;
      setUser(null);
      setEnded(true);
    };

    um.events.addUserLoaded(onUserLoaded);
    um.events.addUserUnloaded(onUserUnloaded);
    um.events.addAccessTokenExpired(onExpired);
    um.events.addSilentRenewError(() => {});

    return () => {
      mounted = false;
      um.events.removeUserLoaded(onUserLoaded);
      um.events.removeUserUnloaded(onUserUnloaded);
      um.events.removeAccessTokenExpired(onExpired);
    };
  }, []);

  const login = useCallback(async () => {
    const um = getUserManager();
    if (!um || INSECURE_ORIGIN) return;
    // Preserve where the user was so the callback returns them there, not to "/"
    // (CallbackPage navigates to user.state). Keeps deep links across login.
    const here = window.location.pathname + window.location.search;
    const state = here && here !== "/auth/callback" ? here : "/";
    await um.signinRedirect({ state });
  }, []);

  const logout = useCallback(async () => {
    const um = getUserManager();
    if (!um) return;
    // Set BEFORE removeUser so the user-is-null render that follows finds the
    // guard already up and does not fire auto-login.
    setLoggingOut(true);
    const idToken = user?.id_token;
    const clientId = env("VITE_KEYCLOAK_CLIENT_ID", "dashboard");
    // Hand the browser to Keycloak so the SSO session really ends, and let it
    // bring the user back to /logged-out. Without an id_token there is nothing to
    // prove the session with, so fall back to clearing this tab only.
    const back = `${window.location.origin}/logged-out`;
    const target = idToken ? buildEndSessionUrl(idToken, clientId, back) : `${window.location.origin}/logged-out`;
    try {
      await um.removeUser();
    } catch {
      // ignore: still proceed with the navigation below
    }
    window.location.assign(target);
  }, [user]);

  const roles = extractRoles(user);
  const value = {
    enabled: AUTH_ENABLED,
    insecureOrigin: INSECURE_ORIGIN,
    loading,
    loggingOut,
    ended,
    user,
    accessToken: user?.access_token || null,
    username: user?.profile?.preferred_username || user?.profile?.email || null,
    // CAMARA tenant from the `org` token claim (user/service-account attribute). Absent
    // for an operator (god-mode, sees all orgs). Surfaced in the sidebar for clarity.
    org: user?.profile?.org || null,
    roles,
    hasRole: (r) => !AUTH_ENABLED || roles.includes(r),
    login,
    logout,
  };

  return <AuthCtx.Provider value={value}>{children}</AuthCtx.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthCtx);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}

// Token accessor for non-React modules (api.js fetch wrapper). The token is
// read from sessionStorage so it survives module-level imports without
// going through React state.
export function getCurrentAccessToken() {
  if (!AUTH_ENABLED) return null;
  try {
    // Key must match the authority oidc.js used when storing the user
    // (which is computed same-origin when VITE_KEYCLOAK_AUTHORITY is not
    // set). Reading the env var directly here previously produced an
    // empty key under same-origin deployments, so no token was attached
    // to API calls and every request returned 401.
    const key = `oidc.user:${KEYCLOAK_AUTHORITY}:${env("VITE_KEYCLOAK_CLIENT_ID", "dashboard")}`;
    const raw = sessionStorage.getItem(key);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    // Same reason as the expired-session check in AuthProvider: sending a
    // token we already know is dead only produces a 401. expires_at is in
    // seconds (OIDC), with a small skew allowance.
    if (parsed?.expires_at && parsed.expires_at * 1000 <= Date.now() + 5000) return null;
    return parsed?.access_token || null;
  } catch {
    return null;
  }
}
