/**
 * Global operations store. Survives page navigation.
 *
 * Usage in any component:
 *   import { useOperations } from "../context/OperationsContext";
 *   const ops = useOperations();
 *
 *   // Start a streaming operation
 *   ops.run("ran-enable", "Enabling Physical RAN", streamFn, onProgress);
 *
 *   // Read state
 *   ops.current          // { id, label, status, progress, steps, error, startedAt }
 *   ops.busy             // true while an operation is running
 *   ops.elapsed          // seconds since start
 *   ops.dismiss()        // clear completed/failed operation
 */
import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";

const Ctx = createContext(null);

export function OperationsProvider({ children }) {
  const [op, setOp] = useState(null);
  const [elapsed, setElapsed] = useState(0);
  const startRef = useRef(null);

  const busy = op?.status === "running";

  useEffect(() => {
    if (!busy) return;
    startRef.current = Date.now();
    setElapsed(0);
    const iv = setInterval(() => {
      setElapsed(Math.floor((Date.now() - startRef.current) / 1000));
    }, 1000);
    return () => clearInterval(iv);
  }, [busy]);

  /**
   * Run a streaming operation.
   * @param {string}   id          Unique key (e.g. "ran-enable", "ran-disable")
   * @param {string}   label       Human label ("Enabling Physical RAN")
   * @param {Function} streamFn    (onProgress) => Promise<result>  — the streaming API call
   * @param {Function} [onDone]    Optional callback when done: (result, error) => void
   */
  const run = useCallback((id, label, streamFn, onDone) => {
    setOp({
      id,
      label,
      status: "running",
      progress: { step: "starting", status: "in_progress", message: "Starting…" },
      steps: [],
      error: null,
      startedAt: Date.now(),
    });

    streamFn((ev) => {
      setOp((prev) => prev ? { ...prev, progress: ev } : prev);
    })
      .then((result) => {
        setOp((prev) => prev ? {
          ...prev,
          status: result?.error ? "error" : "done",
          progress: null,
          steps: result?.steps || [],
          error: result?.error || null,
        } : prev);
        if (onDone) onDone(result, null);
      })
      .catch((err) => {
        const msg = String(err?.message || err);
        setOp((prev) => prev ? {
          ...prev,
          status: "error",
          progress: null,
          error: msg,
        } : prev);
        if (onDone) onDone(null, msg);
      });
  }, []);

  const dismiss = useCallback(() => {
    if (op?.status !== "running") setOp(null);
  }, [op?.status]);

  return (
    <Ctx.Provider value={{ current: op, busy, elapsed, run, dismiss }}>
      {children}
    </Ctx.Provider>
  );
}

export function useOperations() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useOperations must be inside OperationsProvider");
  return ctx;
}
