import React, { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { useLocation } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import TimeSyncPopover from "./TimeSyncPopover";
import DevModeIndicator from "./DevModeIndicator";
import { env } from "../runtime-env";
import { KEYCLOAK_AUTHORITY } from "../auth/oidc";

const NAV_ITEMS = [
  { id: "overview",      label: "Overview",    icon: "\u25A3", path: "/"           },
  { id: "kubernetes",    label: "Kubernetes",  icon: "\u2638", path: "/kubernetes" },
  { id: "core",          label: "5G Core",     icon: "\u2B22", path: "/core"       },
  { id: "topology",      label: "Topology",    icon: "\u2B95", path: "/topology"   },
  { id: "ran",           label: "RAN",         icon: "\u2699", path: "/ran"        },
  { id: "subscribers",   label: "Subscribers", icon: "\u2263", path: "/subscribers"},
  { id: "ue-monitoring", label: "UE Monitor",  icon: "\u25C9", path: "/ue-monitor" },
  { id: "diagnostics",   label: "Diagnostics", icon: "\u2295", path: "/diagnostics"},
  { id: "metrics",       label: "Metrics",     icon: "\u2261", path: "/metrics"    },
];

const _localFmt = new Intl.DateTimeFormat(undefined, {
  hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
});
const _tzAbbr = (() => {
  // Extract timezone abbreviation (e.g. "CET", "EST") from a formatted date
  const parts = new Intl.DateTimeFormat(undefined, { timeZoneName: "short" }).formatToParts(new Date());
  return parts.find((p) => p.type === "timeZoneName")?.value ?? "LOC";
})();

function useServerClock(serverTime) {
  const offsetRef = useRef(0);
  const [display, setDisplay] = useState(() => _localFmt.format(new Date()));

  useEffect(() => {
    if (serverTime) {
      offsetRef.current = new Date(serverTime).getTime() - Date.now();
    }
  }, [serverTime]);

  useEffect(() => {
    function tick() {
      setDisplay(_localFmt.format(new Date(Date.now() + offsetRef.current)));
    }
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, []);

  return display;
}

export default function Sidebar({ onNavigate, runtime, serverTime }) {
  const { pathname } = useLocation();
  const auth = useAuth();
  const [showSync, setShowSync] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);
  const clockStr = useServerClock(serverTime);
  const toggleSync = useCallback(() => setShowSync((v) => !v), []);
  const closeSync = useCallback(() => setShowSync(false), []);
  const roleLabel = auth.roles.includes("dashboard-admin")
    ? "role: admin"
    : auth.roles.includes("dashboard-viewer")
      ? "role: viewer"
      : "role: none";

  const keycloakAdminUrl = useMemo(() => {
    if (!KEYCLOAK_AUTHORITY) return null;
    try {
      const u = new URL(KEYCLOAK_AUTHORITY);
      const seg = u.pathname.split("/").filter(Boolean);
      const realmsIdx = seg.indexOf("realms");
      if (realmsIdx < 0) return null;
      const prefix = seg.slice(0, realmsIdx).join("/");
      // Keycloak administration privileges live in the master realm console.
      u.pathname = `/${prefix ? `${prefix}/` : ""}admin/master/console/`;
      u.search = "";
      u.hash = "";
      return u.toString();
    } catch {
      return null;
    }
  }, []);

  const handleLogout = useCallback(async () => {
    if (loggingOut) return;
    const ok = window.confirm("Logout from dashboard session?");
    if (!ok) return;
    setLoggingOut(true);
    try {
      await auth.logout();
    } catch (err) {
      console.error("Logout failed:", err);
      setLoggingOut(false);
    }
  }, [auth, loggingOut]);

  // Frontend mode is set at the frontend layer, not the backend. The cluster
  // nginx pod injects VITE_FRONTEND_MODE=prod via env-config.js; the Vite dev
  // server injects VITE_FRONTEND_MODE=dev via .env. Falls back to the backend
  // runtime.mode when the frontend variable is unset (older bundles).
  const frontendMode = (env("VITE_FRONTEND_MODE") || runtime.mode || "unknown").toLowerCase();
  const modeBadgeClass =
    frontendMode === "dev"
      ? "bg-amber-600 text-amber-50"
      : frontendMode === "prod"
        ? "bg-emerald-600 text-emerald-50"
        : "bg-slate-600 text-slate-100";

  return (
    <aside className="fixed left-0 top-0 flex h-screen w-56 flex-col bg-slate-900 border-r border-slate-800">
      <div className="px-4 py-5">
        <h1 className="text-lg font-semibold text-white">5G Dashboard</h1>
        <p className="mt-0.5 text-xs text-slate-400">Out-of-band control room</p>
      </div>

      <nav className="flex-1 px-2">
        {NAV_ITEMS.map((item) => (
          <button
            key={item.id}
            type="button"
            onClick={() => onNavigate(item.id)}
            className={`mb-1 flex w-full items-center gap-3 rounded-md px-3 py-2.5 text-left text-sm transition-colors ${
              pathname === item.path
                ? "bg-indigo-600/20 text-indigo-300 font-medium"
                : "text-slate-300 hover:bg-slate-800 hover:text-white"
            }`}
          >
            <span className="text-base">{item.icon}</span>
            {item.label}
          </button>
        ))}
      </nav>

      <div className="relative border-t border-slate-800 px-3 py-3">
        <button
          type="button"
          onClick={toggleSync}
          className="mb-2 flex w-full items-center gap-2 rounded px-2 py-1.5 text-xs text-slate-400 hover:bg-slate-800 hover:text-slate-200 transition-colors"
          title="Cluster time — click for sync details"
        >
          <span className="text-[10px]">&#x25F7;</span>
          <span className="font-mono tabular-nums">
            {clockStr}
          </span>
          <span className="text-[9px] text-slate-600">{_tzAbbr}</span>
        </button>

        {showSync && <TimeSyncPopover onClose={closeSync} />}

        <a
          href="http://192.168.56.11:30300"
          target="_blank"
          rel="noreferrer"
          className="flex items-center gap-2 rounded px-2 py-1.5 text-xs text-slate-500 hover:bg-slate-800 hover:text-slate-300 transition-colors"
        >
          <span className="text-[10px]">&#x2197;</span>
          Grafana (advanced)
        </a>

        {keycloakAdminUrl && (
          <a
            href={keycloakAdminUrl}
            target="_blank"
            rel="noreferrer"
            className="mt-1 flex items-center gap-2 rounded px-2 py-1.5 text-xs text-slate-500 hover:bg-slate-800 hover:text-slate-300 transition-colors"
          >
            <span className="text-[10px]">&#x2197;</span>
            IAM Admin (master)
          </a>
        )}

        <div className="mt-2 flex items-center gap-2">
          <span className={`rounded px-1.5 py-0.5 text-[10px] font-bold uppercase ${modeBadgeClass}`}>
            {frontendMode}
          </span>
          <span className="truncate text-[10px] text-slate-500" title={runtime.runtime_source}>
            {runtime.runtime_source}
          </span>
        </div>

        <DevModeIndicator />

        {auth.enabled && auth.user && (
          <div className="mt-2 flex items-center justify-between gap-2 border-t border-slate-800 pt-2">
            <div className="flex flex-col">
              <span className="truncate text-[10px] font-medium text-slate-300" title={auth.username || ""}>
                {auth.username || "unknown-user"}
              </span>
              <span className="truncate text-[9px] text-slate-500" title={auth.roles.join(", ") || "no-role"}>
                {roleLabel}
              </span>
            </div>
            <button
              type="button"
              onClick={handleLogout}
              disabled={loggingOut}
              className="rounded bg-slate-800 px-2 py-0.5 text-[10px] text-slate-400 hover:bg-slate-700 hover:text-slate-200 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {loggingOut ? "Logging out..." : "Logout"}
            </button>
          </div>
        )}
      </div>
    </aside>
  );
}
