import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { AUTH_ENABLED, extractRoles, getUserManager, KEYCLOAK_AUTHORITY } from "./oidc";
import { env } from "../runtime-env";

// AuthContext exposes the currently authenticated user (or null), the role
// list extracted from the JWT, and helpers for login/logout. When
// VITE_AUTH_ENABLED is false the context returns a permissive
// "auth-disabled" shape so existing pages keep working untouched.

const AuthCtx = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(AUTH_ENABLED);

  useEffect(() => {
    if (!AUTH_ENABLED) return;
    const um = getUserManager();
    if (!um) {
      setLoading(false);
      return;
    }

    let mounted = true;

    um.getUser()
      .then((u) => {
        if (mounted) setUser(u || null);
      })
      .catch(() => {})
      .finally(() => {
        if (mounted) setLoading(false);
      });

    const onUserLoaded = (u) => mounted && setUser(u);
    const onUserUnloaded = () => mounted && setUser(null);

    um.events.addUserLoaded(onUserLoaded);
    um.events.addUserUnloaded(onUserUnloaded);
    um.events.addAccessTokenExpired(onUserUnloaded);
    um.events.addSilentRenewError(() => {});

    return () => {
      mounted = false;
      um.events.removeUserLoaded(onUserLoaded);
      um.events.removeUserUnloaded(onUserUnloaded);
      um.events.removeAccessTokenExpired(onUserUnloaded);
    };
  }, []);

  const login = useCallback(async () => {
    const um = getUserManager();
    if (!um) return;
    await um.signinRedirect();
  }, []);

  const logout = useCallback(async () => {
    const um = getUserManager();
    if (!um) return;
    await um.signoutRedirect();
  }, []);

  const roles = extractRoles(user);
  const value = {
    enabled: AUTH_ENABLED,
    loading,
    user,
    accessToken: user?.access_token || null,
    username: user?.profile?.preferred_username || user?.profile?.email || null,
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
    return parsed?.access_token || null;
  } catch {
    return null;
  }
}
