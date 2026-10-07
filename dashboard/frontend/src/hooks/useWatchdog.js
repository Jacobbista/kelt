import { useCallback, useEffect, useRef, useState } from "react";
import { getWatchdogToken } from "../api";

const BASE = "/watchdog";
// Kept for the tab (sessionStorage, like the OIDC tokens; cleared at logout,
// auth/AuthContext.jsx): a reload while the backend is down cannot ask it for
// the token again, and without it there is no restart.
export const WATCHDOG_TOKEN_KEY = "kelt_watchdog_token";

function storedToken() {
  try { return sessionStorage.getItem(WATCHDOG_TOKEN_KEY); } catch { return null; }
}

// The backend's watchdog (a separate service on the backend VM, proxied at
// /watchdog): the service journal and a restart, for when the backend itself
// does not answer. It wants an X-Watchdog-Token, which only the backend hands
// out (admin-only), so the token is fetched while the backend is up and kept.
export function useWatchdog(auth) {
  const tokenRef = useRef(storedToken());
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (tokenRef.current) return;
    if (auth.enabled && (auth.loading || !auth.user)) return;
    if (auth.enabled && !auth.roles.includes("dashboard-admin")) return;
    getWatchdogToken()
      .then((t) => {
        tokenRef.current = t || null;
        try { if (t) sessionStorage.setItem(WATCHDOG_TOKEN_KEY, t); } catch { /* noop */ }
      })
      .catch(() => { /* restart then fails gracefully */ });
  }, [auth.enabled, auth.loading, auth.user, auth.roles]);

  const headers = () => (tokenRef.current ? { "X-Watchdog-Token": tokenRef.current } : {});

  const fetchStatus = useCallback(async () => {
    setLoading(true);
    try {
      const ctrl = new AbortController();
      const to = setTimeout(() => ctrl.abort(), 4000);
      const res = await fetch(`${BASE}/status`, { signal: ctrl.signal, headers: headers() });
      clearTimeout(to);
      if (res.status === 401) throw new Error("The watchdog refused the request: no token (only an admin gets one, while the backend is up).");
      if (!res.ok) throw new Error(`The watchdog answered ${res.status}.`);
      setStatus(await res.json());
    } catch (e) {
      setStatus({ error: e?.name === "AbortError" || e instanceof TypeError ? "The watchdog does not answer either. On the backend VM: sudo systemctl restart dashboard-backend" : e.message });
    }
    setLoading(false);
  }, []);

  // Resolves to { ok } or { error }: the gate says which.
  const restart = useCallback(async () => {
    try {
      const ctrl = new AbortController();
      const to = setTimeout(() => ctrl.abort(), 12000);
      const res = await fetch(`${BASE}/restart`, { method: "POST", signal: ctrl.signal, headers: headers() });
      clearTimeout(to);
      if (res.status === 401) return { error: "The watchdog refused the restart: no token (only an admin gets one, while the backend is up)." };
      if (!res.ok) return { error: `The watchdog answered ${res.status}.` };
      return { ok: true };
    } catch {
      return { error: "The watchdog does not answer. On the backend VM: sudo systemctl restart dashboard-backend" };
    }
  }, []);

  return { status, loading, fetchStatus, restart };
}
