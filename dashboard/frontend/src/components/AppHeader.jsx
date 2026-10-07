import React, { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { useUpdates } from "../context/UpdateContext";
import { useStatusSummary } from "../context/StatusSummaryContext";
import { crumbsFor } from "../navigation";
import { env } from "../runtime-env";
import TimeSyncPopover from "./TimeSyncPopover";
import AccountMenu, { Avatar, roleBadge } from "./AccountMenu";
import StatusPopover from "./StatusPopover";
import OperationsIndicator from "./OperationsIndicator";
import { IconRefresh } from "./icons";

// Above every page: where you are (breadcrumb), then what holds for the whole
// dashboard: overall state, a pending update, the environment, cluster time,
// the account. The sidebar only navigates.

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

const PILL = {
  loading: { cls: "border-slate-700 bg-slate-800/60 text-slate-400", dot: "bg-slate-500 animate-pulse" },
  ok:      { cls: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300 hover:bg-emerald-500/20", dot: "bg-emerald-400" },
  warn:    { cls: "border-amber-500/30 bg-amber-500/10 text-amber-300 hover:bg-amber-500/20", dot: "bg-amber-400" },
  error:   { cls: "border-rose-500/30 bg-rose-500/10 text-rose-300 hover:bg-rose-500/20", dot: "bg-rose-400" },
  unknown: { cls: "border-slate-700 bg-slate-800/60 text-slate-400 hover:bg-slate-800", dot: "bg-slate-500" },
};

// Overall state (nodes, 5G pods, last network checks, AMF CNI alert); the click
// opens the list of problems (StatusPopover).
const StatusPill = React.forwardRef(function StatusPill({ status, onClick, expanded }, ref) {
  const kind = status.loading ? "loading" : (PILL[status.state] ? status.state : "unknown");
  const n = status.problems?.length || 0;
  const label = {
    loading: "Checking…",
    ok: "All systems up",
    warn: `${n} problem${n === 1 ? "" : "s"}`,
    error: `${n} problem${n === 1 ? "" : "s"}`,
    unknown: "Status unavailable",
  }[kind];
  return (
    <button
      ref={ref}
      type="button"
      onClick={onClick}
      aria-haspopup="dialog"
      aria-expanded={expanded}
      className={`flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] transition-colors ${PILL[kind].cls}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${PILL[kind].dot}`} />
      {label}
    </button>
  );
});

export default function AppHeader({ runtime, serverTime }) {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const auth = useAuth();
  const status = useStatusSummary();
  const { available } = useUpdates();
  const clockStr = useServerClock(serverTime);
  const [showSync, setShowSync] = useState(false);
  const [showMenu, setShowMenu] = useState(false);
  const [showStatus, setShowStatus] = useState(false);
  const statusRef = useRef(null);
  const syncRef = useRef(null);
  const menuRef = useRef(null);
  const closeSync = useCallback(() => setShowSync(false), []);
  const closeMenu = useCallback(() => setShowMenu(false), []);
  const closeStatus = useCallback(() => setShowStatus(false), []);

  // Updates are an admin matter; with auth off everyone is one.
  const isAdmin = !auth.enabled || auth.roles.includes("dashboard-admin");
  // Before a session exists (login callback, logged-out page) there is no page
  // to name and nothing the account may read: breadcrumb and status wait.
  const authReady = !auth.enabled || (!auth.loading && !!auth.user);
  const crumbs = crumbsFor(pathname);
  // Frontend mode is set at the frontend layer, not the backend. The cluster
  // nginx pod injects VITE_FRONTEND_MODE=prod via env-config.js; the Vite dev
  // server injects VITE_FRONTEND_MODE=dev via .env. Falls back to the backend
  // runtime.mode when the frontend variable is unset (older bundles).
  const mode = (env("VITE_FRONTEND_MODE") || runtime.mode || "unknown").toLowerCase();
  const modeCls = mode === "dev" ? "bg-amber-600 text-amber-50" : mode === "prod" ? "bg-emerald-600 text-emerald-50" : "bg-slate-600 text-slate-100";
  const modeBadge = <span className={`rounded px-1.5 py-0.5 text-[10px] font-bold uppercase ${modeCls}`}>{mode}</span>;
  const badge = roleBadge(auth.roles);
  const signedIn = auth.enabled && auth.user;

  return (
    <header className="sticky top-0 z-30 flex h-12 shrink-0 items-center gap-3 border-b border-slate-800 bg-slate-900/90 px-6 backdrop-blur">
      <nav aria-label="Breadcrumb" className="flex min-w-0 items-baseline gap-2 truncate text-sm">
        {authReady && crumbs.map((c, i) => {
          const last = i === crumbs.length - 1;
          return (
            <React.Fragment key={`${c.label}-${i}`}>
              {i > 0 && <span className="text-slate-600" aria-hidden="true">/</span>}
              {last ? (
                <span aria-current="page" className="font-semibold text-slate-100">{c.label}</span>
              ) : c.path ? (
                <button type="button" onClick={() => navigate(c.path)} className="text-slate-400 underline-offset-4 hover:text-slate-100 hover:underline">
                  {c.label}
                </button>
              ) : (
                <span className="text-slate-500">{c.label}</span>
              )}
            </React.Fragment>
          );
        })}
      </nav>

      <div className="ml-auto flex shrink-0 items-center gap-1.5">
        {authReady && (
          <StatusPill ref={statusRef} status={status} expanded={showStatus} onClick={() => setShowStatus((v) => !v)} />
        )}
        {showStatus && (
          <StatusPopover
            status={status}
            anchorRef={statusRef}
            onClose={closeStatus}
            onOpenHealth={() => { setShowStatus(false); navigate("/network/health"); }}
          />
        )}
        {/* Admins only: the operations API is admin-only. */}
        {authReady && isAdmin && <OperationsIndicator />}
        {/* Stays until the update is applied: a state, not a notification. */}
        {authReady && isAdmin && available.length > 0 && (
          <button
            type="button"
            onClick={() => navigate("/manual#updates")}
            title={`Update available: ${available.map((c) => c.name).join(", ")}`}
            className="flex items-center gap-1.5 rounded-full border border-amber-500/30 bg-amber-500/10 px-2.5 py-0.5 text-[11px] text-amber-300 transition-colors hover:bg-amber-500/20"
          >
            <IconRefresh size={12} />
            {available.length} update{available.length === 1 ? "" : "s"}
          </button>
        )}

        <span className="mx-1 h-5 w-px bg-slate-800" aria-hidden="true" />
        <span title={`${mode === "dev" ? "Dev frontend (Vite)" : mode === "prod" ? "Cluster frontend" : "Frontend"}\nBackend code: ${runtime.runtime_source}`}>{modeBadge}</span>
        <button
          ref={syncRef}
          type="button"
          onClick={() => setShowSync((v) => !v)}
          title="Cluster time: click for sync details"
          className="flex items-center gap-1.5 rounded px-2 py-1 font-mono text-xs tabular-nums text-slate-300 transition-colors hover:bg-slate-800"
        >
          {clockStr}
          <span className="font-sans text-[9px] text-slate-600">{_tzAbbr}</span>
        </button>
        {showSync && <TimeSyncPopover onClose={closeSync} anchorRef={syncRef} />}

        <span className="mx-1 h-5 w-px bg-slate-800" aria-hidden="true" />
        <button
          ref={menuRef}
          type="button"
          onClick={() => setShowMenu((v) => !v)}
          aria-haspopup="menu"
          aria-expanded={showMenu}
          className="flex items-center gap-2 rounded-md px-2 py-1 transition-colors hover:bg-slate-800"
        >
          {signedIn ? (
            <>
              <Avatar name={auth.username} cls={badge.cls} />
              <span className="max-w-[10rem] truncate text-xs text-slate-200">{auth.username || "unknown-user"}</span>
            </>
          ) : (
            <span className="text-xs text-slate-400">Frontend</span>
          )}
          <span className="text-[10px] text-slate-500" aria-hidden="true">▾</span>
        </button>
        {showMenu && (
          <AccountMenu onClose={closeMenu} anchorRef={menuRef} />
        )}
      </div>
    </header>
  );
}
