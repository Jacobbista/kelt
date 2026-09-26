import React from "react";
import { fmtCount, stopWord } from "./PlaneMatrix";

const timeFmt = new Intl.DateTimeFormat(undefined, { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false });

function when(iso) {
  // journalctl short-iso: 2026-09-25T09:59:31+0000 (no colon in the offset)
  const d = new Date(iso.replace(/([+-]\d\d)(\d\d)$/, "$1:$2"));
  return Number.isNaN(d.getTime()) ? iso : timeFmt.format(d);
}

// Drops that did not happen between two planes (from a plane to the host or pod
// network, into a plane from outside, UE addresses seen outside the planes), then
// the latest packets the filter logged, newest first.
export default function BlockedSamples({ outside, window, samples, mode }) {
  const hits = (outside || []).filter((o) => o.packets > 0);
  return (
    <div className="text-xs">
      <div className="border-b border-slate-800 px-4 py-2.5">
        <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-slate-500">Outside the planes, last {window}</div>
        {hits.length === 0 ? (
          <div className="text-slate-500">Nothing {stopWord(mode)}.</div>
        ) : (
          hits.map((o) => (
            <div key={o.rule} className="flex items-baseline justify-between gap-3 py-0.5">
              <span className="text-slate-300">{o.rule}</span>
              <span className="font-semibold tabular-nums text-amber-300">{fmtCount(o.packets)}</span>
            </div>
          ))
        )}
      </div>
      {!samples ? (
        <div className="space-y-2 p-4">
          {[0, 1, 2].map((i) => <div key={i} className="h-10 animate-pulse rounded bg-slate-800/60" />)}
        </div>
      ) : !samples.available ? (
        <div className="px-4 py-3 text-slate-500">Samples unavailable: {samples.reason}</div>
      ) : samples.samples.length === 0 ? (
        <div className="px-4 py-3 text-slate-500">No packet logged in the last 24 h.</div>
      ) : (
        <ul className="divide-y divide-slate-800">
          {samples.samples.map((s, i) => (
            <li key={`${s.time}-${i}`} className="grid gap-0.5 px-4 py-2.5">
              <div className="flex items-center gap-2">
                <span className="font-mono text-[11px] font-semibold text-amber-300">{s.in} → {s.out}</span>
                <span className="ml-auto shrink-0 text-[10px] tabular-nums text-slate-500">{when(s.time)}</span>
              </div>
              <div className="font-mono text-[10.5px] text-slate-400">
                {s.src} → {s.dst} · {s.proto}
                {s.sport != null && ` ${s.sport} → ${s.dport}`}
              </div>
              <div className="text-[10.5px] text-slate-500">
                {s.rule ? <>rule: <span className="font-mono">{s.rule}</span></> : "logged before per-rule sampling"}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
