import React, { useCallback, useState } from "react";
import { createPortal } from "react-dom";
import { useAuth } from "../auth/AuthContext";
import { useConfirm } from "../context/ConfirmContext";
import DevModeIndicator from "./DevModeIndicator";
import usePopover, { belowRight } from "../hooks/usePopover";

// What the account can DO, in plain words rather than the role's system name:
// a viewer must see at a glance that writes will be refused. Orthogonal roles
// (camara/positioning) are listed in full on the IAM page.
export function roleBadge(roles) {
  if (roles.includes("dashboard-admin")) return { label: "Full access", cls: "bg-emerald-500/15 text-emerald-300", textCls: "text-emerald-400" };
  if (roles.includes("dashboard-viewer")) return { label: "Read-only", cls: "bg-amber-500/15 text-amber-300", textCls: "text-amber-400" };
  return { label: "No access", cls: "bg-rose-500/15 text-rose-300", textCls: "text-rose-400" };
}

export function Avatar({ name, cls }) {
  return (
    <span className={`flex h-6 w-6 flex-none items-center justify-center rounded-full text-[10px] font-semibold uppercase ${cls}`} aria-hidden="true">
      {(name || "?").charAt(0)}
    </span>
  );
}

// The account button's popover: who you are, where this frontend comes from,
// the dev frontend switch (admin, on prod), log out. See hooks/usePopover.js.
export default function AccountMenu({ runtimeSource, modeBadge, onClose, anchorRef }) {
  const auth = useAuth();
  const confirm = useConfirm();
  const { ref, style } = usePopover(anchorRef, onClose, belowRight);
  const [loggingOut, setLoggingOut] = useState(false);
  const badge = roleBadge(auth.roles);
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

  if (!style) return null; // first frame, before the anchor rect is measured

  return createPortal(
    <div
      ref={ref}
      role="dialog"
      aria-label="Account"
      style={style}
      className="fixed z-50 w-64 rounded-lg border border-slate-700 bg-slate-900 p-1.5 text-xs shadow-xl shadow-black/40"
    >
      {auth.enabled && auth.user && (
        <div className="flex items-center gap-2 border-b border-slate-800 px-2 pb-2 pt-1">
          <Avatar name={auth.username} cls={badge.cls} />
          <div className="flex min-w-0 flex-col">
            <span className="truncate text-[11px] font-medium text-slate-200">{auth.username || "unknown-user"}</span>
            <span className="truncate text-[10px] text-slate-400" title={`Roles: ${auth.roles.join(", ") || "none"}\n${scopeTitle}`}>
              <span className={badge.textCls}>{badge.label}</span>
              <span className="text-slate-600"> · </span>
              {scopeLabel}
            </span>
          </div>
        </div>
      )}

      <div className="px-2 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-wider text-slate-500">Frontend</div>
      <div className="flex items-center gap-2 px-2 pb-1.5">
        {modeBadge}
        <span className="truncate font-mono text-[10px] text-slate-500" title={runtimeSource}>{runtimeSource}</span>
      </div>
      <div className="px-2 pb-1">
        <DevModeIndicator />
      </div>

      {auth.enabled && auth.user && (
        <button
          type="button"
          onClick={handleLogout}
          disabled={loggingOut}
          className="mt-1 flex w-full items-center rounded border-t border-slate-800 px-2 py-1.5 text-left text-slate-300 hover:bg-slate-800 hover:text-white disabled:cursor-not-allowed disabled:opacity-60"
        >
          {loggingOut ? "Logging out..." : "Log out"}
        </button>
      )}
    </div>,
    document.body,
  );
}
