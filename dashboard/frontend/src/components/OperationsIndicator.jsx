import React, { useCallback, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import { getOperations, getPieces } from "../api";
import useResource from "../data/useResource";
import usePopover, { belowRight } from "../hooks/usePopover";
import { headerSummary, progressOf, resultOf } from "../lib/operations";
import SignalBars from "./SignalBars";

const TONE = { run: "text-indigo-300", ok: "text-emerald-400", bad: "text-rose-400", muted: "text-slate-500" };
const MARK = { ok: "✓", bad: "✗" };
const hhmm = (iso) => {
  const d = iso ? new Date(iso) : null;
  return d && !Number.isNaN(d.getTime()) ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false }) : "";
};

function Panel({ summary, pieces, onClose, anchorRef, onOpen }) {
  const { ref, style } = usePopover(anchorRef, onClose, belowRight);
  if (!style) return null;
  const n = summary.running.length + summary.more;
  const line = (r, left, tone) => (
    <button key={r.id} type="button" onClick={() => onOpen(r.id)}
      className="flex w-full items-baseline gap-3 px-3 py-2 text-left text-xs transition-colors hover:bg-slate-800/60">
      <span className={`w-24 shrink-0 font-mono ${TONE[tone]}`}>{left}</span>
      <span className="min-w-0 flex-1 truncate text-slate-200">{r.title || r.piece}</span>
    </button>
  );
  const last = summary.last;
  const lastRes = last && resultOf(last);
  return createPortal(
    <div ref={ref} style={style} role="dialog" aria-label="Operations"
      className="fixed z-50 w-80 rounded-lg border border-slate-700 bg-slate-900 shadow-xl shadow-black/40">
      <div className="divide-y divide-slate-800">
        {summary.running.map((r) => line(r, progressOf(r, pieces), "run"))}
        {last && line(last, `${MARK[lastRes.tone] || "·"} ${hhmm(last.ended || last.started)}`, lastRes.tone)}
        {!n && !last && <div className="px-3 py-3 text-xs text-slate-500">No operations yet.</div>}
      </div>
      <div className="flex items-center justify-between border-t border-slate-800 px-3 py-2 text-[11px] text-slate-500">
        <span>{summary.more ? `+${summary.more} more running` : n ? `${n} running` : "nothing running"}</span>
        <button type="button" onClick={() => onOpen(null)} className="text-indigo-400 hover:text-indigo-300">All operations →</button>
      </div>
    </div>,
    document.body,
  );
}

// What the runner is doing now and the last result (Foundations spec, 4):
// three running lines at most, never a scroll; the full record is the
// Operations page. Polls the records; the loader shows only while a run is on.
// An error keeps the last state (the cache keeps its data).
export default function OperationsIndicator() {
  const ops = useResource("operations", () => getOperations(), { every: 5000 });
  const pieces = useResource("pieces", getPieces);
  const [open, setOpen] = useState(false);
  const anchorRef = useRef(null);
  const navigate = useNavigate();
  const close = useCallback(() => setOpen(false), []);
  const summary = headerSummary(ops.data);
  const n = summary.running.length + summary.more;
  const go = (id) => { setOpen(false); navigate(id ? `/operations#${id}` : "/operations"); };
  return (
    <>
      <button ref={anchorRef} type="button" onClick={() => setOpen((o) => !o)} aria-haspopup="dialog" aria-expanded={open}
        className={`flex items-center gap-1.5 rounded-full border bg-slate-800/60 px-2.5 py-0.5 text-[11px] text-slate-300 transition-colors hover:bg-slate-800 ${open ? "border-indigo-500/60 text-slate-100" : "border-slate-700"}`}>
        {n > 0 && <SignalBars />}Operations <span className="font-mono tabular-nums text-slate-100">{n}</span>
      </button>
      {open && <Panel summary={summary} pieces={pieces.data} anchorRef={anchorRef} onClose={close} onOpen={go} />}
    </>
  );
}
