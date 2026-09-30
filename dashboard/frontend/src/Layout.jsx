import React, { useEffect, useRef, useState, useCallback } from "react";
import Sidebar from "./components/Sidebar";
import AppHeader from "./components/AppHeader";
import { useAuth } from "./auth/AuthContext";
import { getWatchdogToken } from "./api";

export default function Layout({ onNavigate, runtime, children, backendUnreachable, sessionExpired, serverTime }) {
  const auth = useAuth();
  const [statusExpanded, setStatusExpanded] = useState(false);
  const [serviceStatus, setServiceStatus] = useState(null);
  const [statusLoading, setStatusLoading] = useState(false);

  // Watchdog runs on a separate port (127.0.0.1 only); Vite proxies /watchdog
  // so it stays reachable via tunnel without exposing another origin. The
  // watchdog requires an X-Watchdog-Token header; fetch the token while the
  // backend is up so we can still restart it after a crash. Gate behind the
  // auth context resolving a user, otherwise the very first render fires the
  // request before any Bearer token is attached and the backend returns 401.
  const watchdogBase = "/watchdog";
  const watchdogTokenRef = useRef(null);

  useEffect(() => {
    if (watchdogTokenRef.current) return;
    if (auth.enabled && (auth.loading || !auth.user)) return;
    // The token endpoint is admin-only, and the restart it unlocks is an admin
    // action, so for a viewer this was a guaranteed 403 on every page load.
    if (auth.enabled && !auth.roles.includes("dashboard-admin")) return;
    getWatchdogToken()
      .then((t) => { watchdogTokenRef.current = t || null; })
      .catch(() => { /* viewer or unauthenticated caller: restart button fails gracefully */ });
  }, [auth.enabled, auth.loading, auth.user, auth.roles]);

  const _wdHeader = () => (
    watchdogTokenRef.current ? { "X-Watchdog-Token": watchdogTokenRef.current } : {}
  );

  const fetchStatus = useCallback(async () => {
    setStatusLoading(true);
    setServiceStatus(null);
    try {
      const ctrl = new AbortController();
      const to = setTimeout(() => ctrl.abort(), 4000);
      const res = await fetch(`${watchdogBase}/status`, { signal: ctrl.signal, headers: _wdHeader() });
      clearTimeout(to);
      if (!res.ok) throw new Error(`${res.status}`);
      setServiceStatus(await res.json());
    } catch {
      setServiceStatus({ journal: "Watchdog unreachable or unauthorized — try restarting manually:\n  sudo systemctl restart dashboard-backend", status_output: "" });
    }
    setStatusLoading(false);
  }, [watchdogBase]);

  const restartViaWatchdog = useCallback(async () => {
    try {
      const ctrl = new AbortController();
      const to = setTimeout(() => ctrl.abort(), 12000);
      await fetch(`${watchdogBase}/restart`, { method: "POST", signal: ctrl.signal, headers: _wdHeader() });
      clearTimeout(to);
    } catch { /* watchdog may be slow while systemctl runs */ }
    // Wait for backend to come back, then refresh status
    await new Promise((r) => setTimeout(r, 3000));
    fetchStatus();
  }, [watchdogBase, fetchStatus]);

  return (
    <div className="flex min-h-screen bg-slate-950 text-slate-100">
      <Sidebar onNavigate={onNavigate} />
      <div className="ml-56 flex min-h-screen min-w-0 flex-1 flex-col">
        <AppHeader runtime={runtime} serverTime={serverTime} />
        <main className="flex-1 overflow-y-auto p-6">
          {backendUnreachable && (
            <div className="mb-4 rounded-lg border border-amber-700 bg-amber-950/50 text-sm text-amber-200">
              <div className="flex items-center justify-between px-4 py-3">
                <span className="flex items-center gap-2">
                  <span className="h-2 w-2 rounded-full bg-amber-500 animate-pulse" />
                  {sessionExpired
                    ? "Session expired — reload to sign in again."
                    : "Backend unreachable — reconnecting…"}
                </span>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => {
                      if (!statusExpanded) fetchStatus();
                      setStatusExpanded(!statusExpanded);
                    }}
                    className="rounded bg-slate-600/60 px-3 py-1.5 text-xs font-medium hover:bg-slate-600"
                  >
                    {statusExpanded ? "Hide status" : "Show status"}
                  </button>
                  <button
                    type="button"
                    onClick={restartViaWatchdog}
                    className="rounded bg-amber-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-amber-500"
                  >
                    Restart backend
                  </button>
                  <button
                    type="button"
                    onClick={() => window.location.reload()}
                    className="rounded bg-slate-600/60 px-3 py-1.5 text-xs font-medium hover:bg-slate-600"
                  >
                    Reload page
                  </button>
                </div>
              </div>
              {statusExpanded && (
                <div className="border-t border-amber-800/50 px-4 py-3">
                  <div className="mb-2 flex items-center justify-between">
                    <span className="text-xs font-medium text-amber-300">Service journal</span>
                    <button
                      type="button"
                      onClick={fetchStatus}
                      disabled={statusLoading}
                      className="rounded bg-slate-700/60 px-2 py-0.5 text-[10px] text-slate-300 hover:bg-slate-600 disabled:opacity-50"
                    >
                      {statusLoading ? "Loading…" : "Refresh"}
                    </button>
                  </div>
                  <pre className="max-h-64 overflow-auto rounded bg-slate-950/80 p-3 text-xs font-mono text-slate-300 leading-relaxed whitespace-pre-wrap">
                    {statusLoading && !serviceStatus
                      ? "Fetching service status…"
                      : serviceStatus?.journal || "No data — click Refresh after backend restarts"}
                  </pre>
                </div>
              )}
            </div>
          )}
          {children}
        </main>
      </div>
    </div>
  );
}
