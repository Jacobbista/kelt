import React, { useState, useEffect, useCallback, useRef } from "react";
import { useLocation } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { useConfirm } from "../context/ConfirmContext";
import { useUpdates } from "../context/UpdateContext";
import TimeSyncPopover from "./TimeSyncPopover";
import DevModeIndicator from "./DevModeIndicator";
import { env } from "../runtime-env";

const NAV_ITEMS = [
  { id: "overview",      label: "Overview",    icon: "\u25A3", path: "/"           },
  { id: "kubernetes",    label: "Kubernetes",  icon: "\u2638", path: "/kubernetes" },
  { id: "core",          label: "5G Core",     icon: "\u2B22", path: "/core"       },
  { id: "topology",      label: "Topology",    icon: "\u2B95", path: "/topology"   },
  // RAN and Subscribers are backed by admin-only routers end to end (mode
  // switching, K/OPc). Showing them to a viewer only produced 403 banners.
  { id: "ran",           label: "RAN",         icon: "\u2699", path: "/ran",        adminOnly: true },
  { id: "subscribers",   label: "Subscribers", icon: "\u2263", path: "/subscribers", adminOnly: true },
  { id: "ue-monitoring", label: "UE Monitor",  icon: "\u25C9", path: "/ue-monitor" },
  { id: "diagnostics",   label: "Diagnostics", icon: "\u2295", path: "/diagnostics"},
  { id: "metrics",       label: "Metrics",     icon: "\u2261", path: "/metrics"    },
  // Services hub (positioning/CAMARA now; NEF/MEC later). Visible to viewers
  // (read-only); write controls inside each page are gated on dashboard-admin.
  { id: "services",      label: "Services",    icon: "\u25a6", path: "/services"   },
  // Settings (admin only): identity reference + front-door branding as tabs, so
  // these config surfaces share one sidebar entry instead of two.
  { id: "settings",      label: "Settings",    icon: "\uD83C\uDF9B\uFE0F", path: "/settings",   adminOnly: true },
  // Manual + Learn: docs links into the live site + short in-app concept notes.
  { id: "manual",        label: "Manual",      icon: "\u24D8", path: "/manual"     },
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
  const confirm = useConfirm();
  const { available } = useUpdates();
  const [showSync, setShowSync] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);
  const clockStr = useServerClock(serverTime);
  const toggleSync = useCallback(() => setShowSync((v) => !v), []);
  const closeSync = useCallback(() => setShowSync(false), []);
  // What the account can DO, in plain words rather than the role's system name:
  // a viewer must see at a glance that writes will be refused. Orthogonal roles
  // (camara/positioning) are listed in full on the IAM page.
  const roleBadge = auth.roles.includes("dashboard-admin")
    ? { label: "Full access", cls: "bg-emerald-500/15 text-emerald-300", textCls: "text-emerald-400" }
    : auth.roles.includes("dashboard-viewer")
      ? { label: "Read-only", cls: "bg-amber-500/15 text-amber-300", textCls: "text-amber-400" }
      : { label: "No access", cls: "bg-rose-500/15 text-rose-300", textCls: "text-rose-400" };
  // Which tenant's CAMARA assets the account sees: its own org, or all of them
  // when the token carries no org claim (operator).
  const scopeLabel = auth.org ? `tenant ${auth.org}` : "all tenants";
  const scopeTitle = auth.org
    ? `CAMARA tenant: sees only assets of org "${auth.org}"`
    : "No org claim: sees assets of every tenant (operator)";

  const handleLogout = useCallback(async () => {
    if (loggingOut) return;
    if (!(await confirm({ title: "Log out?", body: "End this dashboard session.", confirmLabel: "Log out" }))) return;
    setLoggingOut(true);
    try {
      await auth.logout();
    } catch (err) {
      console.error("Logout failed:", err);
      setLoggingOut(false);
    }
  }, [auth, loggingOut, confirm]);

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
      <div className="flex items-center gap-2.5 px-4 py-5">
        <img src="/kelt-mark.svg" alt="KELT" className="h-7 w-7 shrink-0" />
        <div>
          <h1 className="text-lg font-semibold leading-tight text-white">5G Dashboard</h1>
          <p className="mt-0.5 text-[11px] text-slate-400">KELT · out-of-band control room</p>
        </div>
      </div>

      <nav className="flex-1 px-2">
        {NAV_ITEMS.filter((item) => !item.adminOnly || auth.roles.includes("dashboard-admin")).map((item) => (
          <button
            key={item.id}
            type="button"
            onClick={() => onNavigate(item.id)}
            className={`mb-1 flex w-full items-center gap-3 rounded-md px-3 py-2.5 text-left text-sm transition-colors ${
              pathname === item.path || (item.path !== "/" && pathname.startsWith(item.path + "/"))
                ? "bg-indigo-600/20 text-indigo-300 font-medium"
                : "text-slate-300 hover:bg-slate-800 hover:text-white"
            }`}
          >
            <span className="text-base">{item.icon}</span>
            {item.label}
            {/* Stays until the update is applied: a dot that clears itself would
                be a notification, and this is a state. */}
            {item.id === "manual" && available.length > 0 && (
              <span
                className="ml-auto h-1.5 w-1.5 rounded-full bg-amber-400"
                title={`${available.length} update available`}
              />
            )}
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
            {/* One identity block, two lines: who you are, then what that lets
                you do and whose data you see. A stack of bare chips ("admin",
                "all orgs") did not read as an answer to either question. */}
            <div className="flex min-w-0 items-center gap-2">
              <span
                className={`flex h-6 w-6 flex-none items-center justify-center rounded-full text-[10px] font-semibold uppercase ${roleBadge.cls}`}
                aria-hidden="true"
              >
                {(auth.username || "?").charAt(0)}
              </span>
              <div className="flex min-w-0 flex-col">
                <span className="truncate text-[11px] font-medium text-slate-200" title={auth.username || ""}>
                  {auth.username || "unknown-user"}
                </span>
                <span
                  className="truncate text-[10px] text-slate-400"
                  title={`Roles: ${auth.roles.join(", ") || "none"}\n${scopeTitle}`}
                >
                  <span className={roleBadge.textCls}>{roleBadge.label}</span>
                  <span className="text-slate-600"> · </span>
                  {scopeLabel}
                </span>
              </div>
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
