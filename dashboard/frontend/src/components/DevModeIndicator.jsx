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
const POLL_INTERVAL_MS = 15000;

function resolveDevUrl(serverUrl) {
  if (serverUrl) return serverUrl;
  const fromRuntime = env("DASHBOARD_DEV_EXTERNAL_URL");
  return fromRuntime || "";
}

const HMR_PROBE_TIMEOUT_MS = 4000;

// Preflight check for the Vite HMR WebSocket bypass on the dev hostname.
// This runs in the prod sidebar, which serves no Vite HMR client, so the
// probe cannot trigger the dev reload loop it is testing for. Vite answers
// the upgrade with 101 for any (or no) token as long as the vite-hmr
// subprotocol is present, so onopen means the upgrade reached Vite and the
// Cloudflare Access bypass on /__vite_hmr* is in place. No onopen (Access
// rewrites the upgrade to a 302) means the bypass is missing and the dev
// page would reload-loop. See docs/deployment/external-tunnel.md.
function probeHmr(devUrl) {
  return new Promise((resolve) => {
    let ws;
    try {
      const wsUrl = devUrl.replace(/\/$/, "").replace(/^http/, "ws") + "/__vite_hmr";
      ws = new WebSocket(wsUrl, "vite-hmr");
    } catch {
      resolve("blocked");
      return;
    }
    let settled = false;
    const done = (result) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      try { ws.close(); } catch {}
      resolve(result);
    };
    const timer = setTimeout(() => done("blocked"), HMR_PROBE_TIMEOUT_MS);
    ws.onopen = () => done("ok");
    ws.onerror = () => done("blocked");
    ws.onclose = () => done("blocked");
  });
}

export default function DevModeIndicator() {
  const auth = useAuth();
  const isAdmin = auth.roles.includes("dashboard-admin");
  const frontendMode = (env("VITE_FRONTEND_MODE") || "").toLowerCase();
  const isDevFrontend = frontendMode === "dev";
  const [status, setStatus] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [hmrStatus, setHmrStatus] = useState(null); // null | "checking" | "ok" | "blocked"

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

  // Re-probe the HMR bypass whenever the dev frontend status refreshes, so
  // the indicator clears itself once the Cloudflare bypass is added. Keyed
  // on status so it rides the existing poll instead of its own timer.
  useEffect(() => {
    const active = !!status?.is_active;
    const url = resolveDevUrl(status?.url);
    if (!isAdmin || isDevFrontend || !active || !url) {
      setHmrStatus(null);
      return undefined;
    }
    let cancelled = false;
    setHmrStatus("checking");
    probeHmr(url).then((result) => {
      if (!cancelled) setHmrStatus(result);
    });
    return () => { cancelled = true; };
  }, [isAdmin, isDevFrontend, status]);

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

      {active && url && hmrStatus === "checking" && (
        <span className="text-[9px] text-slate-500">checking HMR tunnel…</span>
      )}
      {active && url && hmrStatus === "ok" && (
        <span
          className="text-[9px] text-emerald-500"
          title="The Vite HMR WebSocket reaches the dev server: the Cloudflare Access bypass for /__vite_hmr* is in place."
        >
          HMR tunnel ok
        </span>
      )}
      {active && url && hmrStatus === "blocked" && (
        <span
          className="text-[9px] text-rose-400"
          title="Opening the dev UI will reload-loop. Add a Cloudflare Access Bypass app for <dev-host>/__vite_hmr* (Everyone), or disable HMR with DASHBOARD_DEV_HMR_ENABLED=false. See docs/deployment/external-tunnel.md."
        >
          HMR bypass missing: dev UI will reload-loop
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
