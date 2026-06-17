import React, { useState, useEffect, useCallback } from "react";
import { useAuth } from "../auth/AuthContext";
import { env } from "../runtime-env";
import {
  getDevFrontendStatus,
  enableDevFrontend,
  disableDevFrontend,
} from "../api";

// Polls the backend for the dev frontend systemd state and lets admins
// toggle it. The cluster pod is the always-on baseline so this widget
// only governs the opt-in Vite dev frontend on the ansible VM.
//
// NOTE: this widget deliberately does NOT probe the dev HMR WebSocket. A
// prod-origin probe to the dev host triggered the browser's Local Network
// Access block (and a mixed-content "Not secure" when the dev URL was a LAN
// address), for a purely cosmetic "HMR ok" badge. The reload-loop caveat is
// documented in docs/deployment/external-tunnel.md instead.
const POLL_INTERVAL_MS = 15000;

function resolveDevUrl(serverUrl) {
  if (serverUrl) return serverUrl;
  const fromRuntime = env("DASHBOARD_DEV_EXTERNAL_URL");
  return fromRuntime || "";
}

export default function DevModeIndicator() {
  const auth = useAuth();
  const isAdmin = auth.roles.includes("dashboard-admin");
  const frontendMode = (env("VITE_FRONTEND_MODE") || "").toLowerCase();
  const isDevFrontend = frontendMode === "dev";
  const [status, setStatus] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const refresh = useCallback(async () => {
    try {
      const data = await getDevFrontendStatus();
      setStatus(data);
      setError(null);
    } catch (err) {
      setError(err.message || "status failed");
      setStatus(null);
    }
  }, []);

  useEffect(() => {
    if (!isAdmin || isDevFrontend) return undefined;
    refresh();
    const id = setInterval(refresh, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [isAdmin, isDevFrontend, refresh]);

  if (!isAdmin) return null;
  if (isDevFrontend) return null;

  const toggle = async () => {
    if (busy || !status) return;
    setBusy(true);
    try {
      const next = status.is_active ? await disableDevFrontend() : await enableDevFrontend();
      setStatus(next);
      setError(null);
    } catch (err) {
      setError(err.message || "toggle failed");
    } finally {
      setBusy(false);
    }
  };

  const active = !!status?.is_active;
  const dotClass = active ? "bg-amber-400" : "bg-slate-600";
  const url = resolveDevUrl(status?.url);

  return (
    <div className="mt-2 flex flex-col gap-1 border-t border-slate-800 pt-2">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className={`inline-block h-2 w-2 rounded-full ${dotClass}`} />
          <span className="text-[10px] uppercase tracking-wide text-slate-400">
            Dev frontend
          </span>
        </div>
        <button
          type="button"
          onClick={toggle}
          disabled={busy || !status}
          className="rounded bg-slate-800 px-2 py-0.5 text-[10px] text-slate-300 hover:bg-slate-700 hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
          title={active ? "Stop the dev Vite server" : "Start the dev Vite server"}
        >
          {busy ? "..." : active ? "Stop" : "Start"}
        </button>
      </div>

      {active && url && (
        <a
          href={url}
          target="_blank"
          rel="noreferrer"
          className="flex items-center gap-2 rounded px-1 py-0.5 text-[10px] text-amber-400 hover:text-amber-300"
        >
          <span className="text-[9px]">&#x2197;</span>
          Open dev UI
        </a>
      )}
      {active && !url && (
        <span className="text-[9px] text-slate-500" title="Set DASHBOARD_DEV_EXTERNAL_URL to expose a clickable link">
          dev URL not configured
        </span>
      )}
      {error && (
        <span className="truncate text-[9px] text-rose-400" title={error}>
          {error}
        </span>
      )}
    </div>
  );
}
