/**
 * Global transient notifications (toasts). Complements the header's
 * Operations (piece runs, lib/operations.js); toasts are short success /
 * error / info messages that any page can raise without duplicating banner
 * state.
 *
 * Usage:
 *   import { useToast } from "../context/ToastContext";
 *   const toast = useToast();
 *   toast.success("adapter registered");
 *   toast.error(`deploy failed: ${e.message}`);
 *   toast.info("engine restarting…");
 */
import React, { createContext, useCallback, useContext, useEffect, useState } from "react";

const Ctx = createContext(null);
let _id = 0;

// Read from index.css so the unmount waits exactly as long as the slide out.
function closeMs() {
  const v = getComputedStyle(document.documentElement).getPropertyValue("--toast-close").trim();
  return parseFloat(v) || 250;
}

// Solid surface (not see-through over the page), a coloured edge by kind.
const KIND = {
  ok: { edge: "bg-emerald-400", text: "text-emerald-100" },
  err: { edge: "bg-rose-400", text: "text-rose-100" },
  info: { edge: "bg-sky-400", text: "text-slate-100" },
};

function ToastItem({ t, onClose }) {
  // Mount closed, open on the next frame: the slide-in needs a start state.
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const id = requestAnimationFrame(() => setOpen(true));
    return () => cancelAnimationFrame(id);
  }, []);
  const k = KIND[t.kind] || KIND.info;
  return (
    <div className={`t-toast-slot${t.leaving ? " is-gone" : ""}`}>
      <div className="pb-2">
        <div
          role={t.kind === "err" ? "alert" : "status"}
          className={`t-toast${open && !t.leaving ? " is-open" : ""} pointer-events-auto relative flex items-start gap-3 overflow-hidden rounded-lg border border-slate-700 bg-slate-900 py-3 pl-4 pr-3 text-sm shadow-2xl shadow-black/60 ring-1 ring-black/40`}
        >
          <span className={`absolute inset-y-0 left-0 w-1 ${k.edge}`} aria-hidden="true" />
          <span className={`flex-1 break-words leading-snug ${k.text}`}>{t.text}</span>
          <button type="button" onClick={onClose} className="shrink-0 text-slate-500 transition-colors hover:text-slate-200" aria-label="Dismiss">✕</button>
        </div>
      </div>
    </div>
  );
}

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);

  // Slide out first (leaving), unmount once the close transition is over.
  const remove = useCallback((id) => {
    setToasts((list) => list.map((x) => (x.id === id ? { ...x, leaving: true } : x)));
    setTimeout(() => setToasts((list) => list.filter((x) => x.id !== id)), closeMs());
  }, []);

  const push = useCallback((kind, text, ttl) => {
    const id = ++_id;
    setToasts((list) => [...list, { id, kind, text, leaving: false }]);
    const life = ttl ?? (kind === "err" ? 7000 : 4000);
    if (life) setTimeout(() => remove(id), life);
    return id;
  }, [remove]);

  const api = {
    push,
    success: (text, ttl) => push("ok", text, ttl),
    error: (text, ttl) => push("err", text, ttl),
    info: (text, ttl) => push("info", text, ttl),
    remove,
  };

  return (
    <Ctx.Provider value={api}>
      {children}
      {/* Under the header (h-12), aligned with its right edge: never over its controls. */}
      <div className="pointer-events-none fixed right-6 top-16 z-[120] flex w-96 max-w-[90vw] flex-col" aria-live="polite">
        {toasts.map((t) => (
          <ToastItem key={t.id} t={t} onClose={() => remove(t.id)} />
        ))}
      </div>
    </Ctx.Provider>
  );
}

export function useToast() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useToast must be inside ToastProvider");
  return ctx;
}
