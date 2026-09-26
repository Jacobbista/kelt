import React from "react";
import { useLocation } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { useIsolationSummary } from "../context/IsolationSummaryContext";
import { NAV_GROUPS, NAV_FOOTER } from "../navigation";

// Navigation only: the pages, by group (see navigation.js). Global state (status,
// updates, environment, time, account) is in the header (AppHeader.jsx).
export default function Sidebar({ onNavigate }) {
  const { pathname } = useLocation();
  const auth = useAuth();
  const isolation = useIsolationSummary();
  const isAdmin = auth.roles.includes("dashboard-admin");
  const visible = (item) => !item.adminOnly || isAdmin;
  const isActive = (item) => pathname === item.path || (item.path !== "/" && pathname.startsWith(item.path + "/"));
  const renderItem = (item) => (
    <button
      key={item.id}
      type="button"
      onClick={() => onNavigate(item.id)}
      className={`mb-0.5 flex w-full items-center gap-3 rounded-md px-3 py-2 text-left text-sm transition-colors ${
        isActive(item) ? "bg-indigo-600/20 text-indigo-300 font-medium" : "text-slate-300 hover:bg-slate-800 hover:text-white"
      }`}
    >
      <item.icon size={17} className="shrink-0 text-current opacity-90" />
      {item.label}
      {item.id === "isolation" && isolation.available && isolation.blocked > 0 && (
        <span className="ml-auto text-[10px] font-bold tabular-nums text-amber-400" title={isolation.mode === "observe" ? "Plane crossings not allowed (observe mode), last 24 h" : "Blocked plane crossings, last 24 h"}>
          {isolation.blocked}
        </span>
      )}
    </button>
  );

  return (
    <aside className="fixed left-0 top-0 flex h-screen w-56 flex-col border-r border-slate-800 bg-slate-900">
      <div className="flex h-12 shrink-0 items-center gap-2.5 border-b border-slate-800 px-4">
        <img src="/kelt-mark.svg" alt="KELT" className="h-7 w-7 shrink-0" />
        <h1 className="text-[15px] font-semibold leading-tight text-white" title="KELT · out-of-band control room">5G Dashboard</h1>
      </div>

      {/* Scrolls only on a window too short for every page. */}
      <nav className="flex-1 overflow-y-auto px-2 pt-3">
        {NAV_GROUPS.map((group, gi) => {
          const items = group.items.filter(visible);
          if (items.length === 0) return null;
          return (
            <div key={group.label || gi} className="mb-3">
              {group.label && (
                <div className="px-3 pb-1 text-[10px] font-semibold uppercase tracking-wider text-slate-500">{group.label}</div>
              )}
              {items.map(renderItem)}
            </div>
          );
        })}
        <div className="mt-1 border-t border-slate-800 pt-2">
          {NAV_FOOTER.filter(visible).map(renderItem)}
        </div>
      </nav>
    </aside>
  );
}
