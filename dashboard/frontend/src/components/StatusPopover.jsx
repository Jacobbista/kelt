import React from "react";
import { createPortal } from "react-dom";
import usePopover, { belowRight } from "../hooks/usePopover";

const SEVERITY = {
  error: "bg-rose-400",
  warn: "bg-amber-400",
};

// What the status pill summarises, readable without hovering (touch, keyboard):
// every problem with its area, or what was checked when nothing is wrong.
export default function StatusPopover({ status, onOpenHealth, onClose, anchorRef }) {
  const { ref, style } = usePopover(anchorRef, onClose, belowRight);
  if (!style) return null; // first frame, before the anchor rect is measured
  const problems = status.problems || [];

  return createPortal(
    <div
      ref={ref}
      role="dialog"
      aria-label="Testbed status"
      style={style}
      className="fixed z-50 w-80 rounded-lg border border-slate-700 bg-slate-900 p-3 text-xs shadow-xl shadow-black/40"
    >
      {status.error ? (
        <p className="text-slate-400">Status unavailable: {status.error}</p>
      ) : problems.length === 0 ? (
        <p className="text-slate-300">
          All systems up: every node Ready, the 5G core pods running, no failed network check, no AMF alert.
        </p>
      ) : (
        <ul className="grid gap-2">
          {problems.map((p, i) => (
            <li key={`${p.area}-${i}`} className="grid grid-cols-[0.5rem_4.5rem_1fr] items-baseline gap-2">
              <span className={`h-1.5 w-1.5 translate-y-[-1px] rounded-full ${SEVERITY[p.severity] || "bg-slate-500"}`} />
              <span className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">{p.area}</span>
              <span className="leading-snug text-slate-200">{p.text}</span>
            </li>
          ))}
        </ul>
      )}
      <p className="mt-3 border-t border-slate-800 pt-2 text-[10.5px] leading-snug text-slate-500">
        Network checks count when Health ran them in the last 10 minutes.
      </p>
      <button
        type="button"
        onClick={onOpenHealth}
        className="mt-2 w-full rounded-md border border-slate-700 px-2 py-1.5 text-slate-200 transition-colors hover:bg-slate-800"
      >
        Open Health
      </button>
    </div>,
    document.body,
  );
}
