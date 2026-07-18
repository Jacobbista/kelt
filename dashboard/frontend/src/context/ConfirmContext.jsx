/**
 * Promise-based confirmation dialogs. Twin idiom to ToastContext: any page calls
 * `const confirm = useConfirm()` then `if (!(await confirm({...}))) return;`,
 * replacing the native `window.confirm()` (which is unstyled, blocks the thread,
 * and reads as "Not secure" on some browsers). One dialog at a time — confirms
 * are modal by nature.
 *
 * Usage:
 *   const confirm = useConfirm();
 *   if (!(await confirm({ title: "Delete app?", body: "...", confirmLabel: "Delete", danger: true }))) return;
 */
import React, { createContext, useCallback, useContext, useRef, useState } from "react";
import { Modal, btn } from "../components/ui";

const Ctx = createContext(null);

export function ConfirmProvider({ children }) {
  const [req, setReq] = useState(null);
  const resolver = useRef(null);

  const confirm = useCallback((opts = {}) => new Promise((resolve) => {
    resolver.current = resolve;
    setReq({
      title: opts.title || "Are you sure?",
      body: opts.body || "",
      confirmLabel: opts.confirmLabel || "Confirm",
      cancelLabel: opts.cancelLabel || "Cancel",
      danger: !!opts.danger,
    });
  }), []);

  const settle = useCallback((value) => {
    const r = resolver.current;
    resolver.current = null;
    setReq(null);
    if (r) r(value);
  }, []);

  return (
    <Ctx.Provider value={confirm}>
      {children}
      {req && (
        <Modal title={req.title} onClose={() => settle(false)}>
          {req.body && <p className="whitespace-pre-line text-xs leading-relaxed text-slate-300">{req.body}</p>}
          <div className="mt-5 flex justify-end gap-2">
            <button type="button" className={btn.ghost} onClick={() => settle(false)}>{req.cancelLabel}</button>
            <button
              type="button"
              autoFocus
              className={req.danger
                ? "rounded bg-rose-600/20 px-3 py-1.5 text-xs font-medium text-rose-300 transition-colors hover:bg-rose-600/30"
                : btn.sky}
              onClick={() => settle(true)}
            >
              {req.confirmLabel}
            </button>
          </div>
        </Modal>
      )}
    </Ctx.Provider>
  );
}

export function useConfirm() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useConfirm must be inside ConfirmProvider");
  return ctx;
}
