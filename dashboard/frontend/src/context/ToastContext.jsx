/**
 * Global transient notifications (toasts). Complements OperationsContext
 * (which tracks one long-running streaming op); toasts are short success /
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
import React, { createContext, useCallback, useContext, useState } from "react";

const Ctx = createContext(null);
let _id = 0;

const STYLE = {
  ok: "border-emerald-700/50 bg-emerald-950/80 text-emerald-200",
  err: "border-rose-700/50 bg-rose-950/80 text-rose-200",
  info: "border-slate-600/50 bg-slate-900/90 text-slate-200",
};

function ToastItem({ t, onClose }) {
  return (
    <div className={`svc-fade pointer-events-auto flex items-start gap-2 rounded-lg border px-3 py-2 text-xs shadow-lg backdrop-blur ${STYLE[t.kind] || STYLE.info}`}>
      <span className="flex-1 break-words">{t.text}</span>
      <button type="button" onClick={onClose} className="shrink-0 text-current/70 hover:text-current" aria-label="dismiss">✕</button>
    </div>
  );
}

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);

  const remove = useCallback((id) => setToasts((list) => list.filter((x) => x.id !== id)), []);

  const push = useCallback((kind, text, ttl) => {
    const id = ++_id;
    setToasts((list) => [...list, { id, kind, text }]);
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
      <div className="pointer-events-none fixed right-4 top-4 z-[120] flex w-80 max-w-[90vw] flex-col gap-2">
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
