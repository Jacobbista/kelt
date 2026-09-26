import React, { useMemo, useState } from "react";

// Plain meaning of the crossings the architecture allows; everything else is
// explained by its rule alone.
const ALLOWED_MEANING = {
  "RAN>N2": "NGAP from the gNB to the AMF.",
  "N2>RAN": "NGAP from the AMF back to the gNB.",
  "RAN>N3": "GTP-U uplink from the gNB to the UPF.",
  "N3>RAN": "GTP-U downlink from the UPF to the gNB.",
  "N6c>Internet": "UE internet traffic, NATed by the UPF, leaving the worker.",
};

// In observe mode the filter counts what is not allowed but lets it through:
// the words must not say "dropped" then.
export const stopWord = (mode) => (mode === "observe" ? "seen" : "dropped");

export function fmtCount(n) {
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e4) return `${Math.round(n / 1e3)}k`;
  return n.toLocaleString("en");
}

// From/to matrix of the 5G planes on the worker: green where a crossing is
// allowed (with its packets), amber where one was blocked, dim where nothing
// is allowed and nothing was tried.
export default function PlaneMatrix({ planes }) {
  const cols = useMemo(() => [...planes.planes, "Internet"], [planes.planes]);
  const byKey = useMemo(() => Object.fromEntries(planes.pairs.map((p) => [`${p.from}>${p.to}`, p])), [planes.pairs]);
  const [selected, setSelected] = useState(() => {
    const busiest = [...planes.pairs].filter((p) => p.verdict === "allowed").sort((a, b) => b.packets - a.packets)[0];
    return busiest ? `${busiest.from}>${busiest.to}` : null;
  });

  const cell = (from, to) => {
    const key = `${from}>${to}`;
    if (from === to) {
      return <td key={key} className="h-9 w-16 rounded border border-dashed border-slate-800 text-center text-[10px] text-slate-600">same</td>;
    }
    const p = byKey[key];
    if (!p && to === "Internet") {
      // Only N6c has an egress rule; anything else leaving a plane for the host
      // is counted once per plane, under "from <plane>" beside the matrix.
      return (
        <td key={key} title={`Traffic from ${from} to anywhere outside the planes is counted under "from ${from}" beside the matrix`}
            className="h-9 w-16 rounded border border-dashed border-slate-800 text-center text-[10px] text-slate-600">–</td>
      );
    }
    const allowed = p?.verdict === "allowed";
    const dropped = p && !allowed && p.packets > 0;
    const tone = allowed
      ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
      : dropped
        ? "border-amber-500/50 bg-amber-500/10 text-amber-300"
        : "border-slate-800 bg-slate-950/40 text-slate-600";
    return (
      <td key={key} className="p-0">
        <button
          type="button"
          onClick={() => setSelected(key)}
          disabled={!p}
          className={`flex h-9 w-16 flex-col items-center justify-center rounded border tabular-nums transition-colors ${tone} ${
            selected === key ? "ring-2 ring-indigo-400 ring-offset-1 ring-offset-slate-900" : ""
          } ${p ? "hover:brightness-125" : "cursor-default"}`}
        >
          <span className="text-[11px] font-semibold leading-none">{allowed || dropped ? fmtCount(p.packets) : "·"}</span>
          {(allowed || dropped) && (
            <span className="mt-0.5 text-[8px] uppercase leading-none tracking-wider opacity-80">{allowed ? "allowed" : stopWord(planes.mode)}</span>
          )}
        </button>
      </td>
    );
  };

  const sel = selected && byKey[selected];
  return (
    <div>
      <div className="overflow-x-auto">
        <table className="border-separate border-spacing-1 text-xs">
          <thead>
            <tr>
              <th className="px-1 text-left text-[10px] font-medium text-slate-600">from ↓ to →</th>
              {cols.map((c) => (
                <th key={c} className="whitespace-nowrap px-1 text-center text-[10px] font-semibold text-slate-400">{c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {planes.planes.map((from) => (
              <tr key={from}>
                <th className="whitespace-nowrap pr-2 text-right text-[10px] font-semibold text-slate-400">{from}</th>
                {cols.map((to) => cell(from, to))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt-2 flex flex-wrap gap-4 text-[10px] text-slate-500">
        <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm border border-emerald-500/40 bg-emerald-500/10" />Allowed</span>
        <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm border border-amber-500/50 bg-amber-500/10" />Not allowed, with packets</span>
        <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm border border-slate-700 bg-slate-950" />Not allowed, nothing tried</span>
      </div>
      <div className="mt-3 min-h-[3.5rem] border-t border-slate-800 pt-3 text-xs leading-relaxed text-slate-400">
        {sel ? (
          <>
            <span className="font-semibold text-slate-200">
              {sel.from} → {sel.to}: {sel.verdict === "allowed" ? "allowed" : "not allowed"}.
            </span>{" "}
            {sel.verdict === "allowed"
              ? `${ALLOWED_MEANING[selected] || ""} ${sel.packets.toLocaleString("en")} packets in the last ${planes.window}.`
              : sel.packets > 0
                ? `${sel.packets.toLocaleString("en")} packets ${planes.mode === "observe" ? "seen (observe mode: counted, not dropped)" : "dropped"} in the last ${planes.window}. The samples show what they were.`
                : `No rule lets ${sel.from} traffic reach ${sel.to}; nothing tried in the last ${planes.window}.`}
            {sel.replies > 0 && ` Replies back in: ${sel.replies.toLocaleString("en")} packets.`}
            <div className="mt-1 font-mono text-[10px] text-slate-500">rule: {sel.verdict === "allowed" ? "allowed" : "not allowed"}: {sel.rule}</div>
          </>
        ) : (
          <span className="text-slate-500">Select a cell to see its rule.</span>
        )}
      </div>
    </div>
  );
}
