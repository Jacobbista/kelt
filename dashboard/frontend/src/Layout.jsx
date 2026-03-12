import React, { useEffect } from "react";
import Loader from "./components/Loader";
import Sidebar from "./components/Sidebar";
import { useOperations } from "./context/OperationsContext";

export default function Layout({ activePage, onNavigate, runtime, children, backendUnreachable, onRestartBackend, restartingBackend = false }) {
  const ops = useOperations();

  // Auto-dismiss completed operation when user navigates to RAN page (they see the result inline)
  useEffect(() => {
    if (activePage === "ran" && ops.current && ops.current.status !== "running") {
      ops.dismiss();
    }
  }, [activePage, ops.current?.status]);

  return (
    <div className="flex min-h-screen bg-slate-950 text-slate-100">
      <Sidebar
        activePage={activePage}
        onNavigate={onNavigate}
        runtime={runtime}
      />
      <main className="ml-56 flex-1 overflow-y-auto p-6">
        {backendUnreachable && (
          <div className="mb-4 flex items-center justify-between rounded-lg border border-amber-700 bg-amber-950/50 px-4 py-3 text-sm text-amber-200">
            <span className="flex items-center gap-2">
              <span className="h-2 w-2 rounded-full bg-amber-500 animate-pulse" />
              Backend unreachable — reconnecting…
            </span>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={onRestartBackend ?? (() => {})}
                disabled={restartingBackend}
                className="rounded bg-amber-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-amber-500 disabled:opacity-50"
              >
                {restartingBackend ? "Restarting…" : "Restart backend"}
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
        )}
        {ops.current && activePage !== "ran" && (
          <div className={`mb-4 flex items-center justify-between rounded-lg border px-4 py-3 text-sm ${
            ops.current.status === "running"
              ? "border-indigo-600/50 bg-indigo-950/40 text-indigo-200"
              : ops.current.status === "error"
              ? "border-rose-700 bg-rose-950/50 text-rose-200"
              : "border-emerald-700/50 bg-emerald-950/40 text-emerald-200"
          }`}>
            <span className="flex items-center gap-3">
              {ops.current.status === "running" && <Loader size="sm" />}
              {ops.current.status === "done" && <span className="text-emerald-400">✓</span>}
              {ops.current.status === "error" && <span className="text-rose-400">✗</span>}
              <span className="font-medium">{ops.current.label}</span>
              {ops.current.status === "running" && ops.current.progress?.step !== "starting" && (
                <span className="text-xs opacity-70 font-mono">{ops.current.progress?.step} — {ops.current.progress?.message}</span>
              )}
              {ops.current.status === "running" && (
                <span className="text-xs font-mono tabular-nums opacity-50">
                  {Math.floor(ops.elapsed / 60)}:{String(ops.elapsed % 60).padStart(2, "0")}
                </span>
              )}
              {ops.current.error && <span className="text-xs opacity-80">{ops.current.error}</span>}
            </span>
            <div className="flex items-center gap-2">
              {ops.current.status === "running" && (
                <button type="button" onClick={() => onNavigate("ran")} className="rounded bg-indigo-600/30 px-3 py-1 text-xs font-medium hover:bg-indigo-600/50">
                  View
                </button>
              )}
              {ops.current.status !== "running" && (
                <>
                  <button type="button" onClick={() => onNavigate("ran")} className="rounded bg-slate-600/30 px-3 py-1 text-xs font-medium hover:bg-slate-600/50">
                    View
                  </button>
                  <button type="button" onClick={ops.dismiss} className="rounded bg-slate-600/30 px-3 py-1 text-xs font-medium hover:bg-slate-600/50">
                    Dismiss
                  </button>
                </>
              )}
            </div>
          </div>
        )}
        {children}
      </main>
    </div>
  );
}
