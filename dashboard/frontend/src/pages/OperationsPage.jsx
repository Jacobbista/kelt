import React, { useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { getOperation, getOperations, getPieces } from "../api";
import useResource from "../data/useResource";
import { Card } from "../components/Card";
import { expectedOf, logOpen, progressOf, resultOf, sortNewest, stepsShown } from "../lib/operations";

const TONE = { run: "text-indigo-300", ok: "text-emerald-400", bad: "text-rose-400", muted: "text-slate-500" };
const FILTERS = [["all", "All"], ["running", "Running"], ["failed", "Failed"]];
const MATCH = { all: () => true, running: (r) => r.state === "running", failed: (r) => resultOf(r).tone === "bad" };
const CHEVRON = <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M3 4.5l3 3 3-3" strokeLinecap="round" strokeLinejoin="round" /></svg>;
const when = (iso) => {
  const d = iso ? new Date(iso) : null;
  if (!d || Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString([], { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
};

// One run: its last steps (all on request), its read-back and, on request or
// when it failed, the Ansible output, which follows the end while the run goes on. Read while the row is open, and
// again every 3 s while the run or its read-back is still going (a failed read
// is retried too).
function Detail({ id, open }) {
  const [rec, setRec] = useState(null);
  const [err, setErr] = useState(null);
  const [allSteps, setAllSteps] = useState(false);
  const [showLog, setShowLog] = useState(null);
  const logRef = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    let alive = true;
    let timer = null;
    let going = true;
    const load = async () => {
      try {
        const r = await getOperation(id);
        if (!alive) return;
        setRec(r); setErr(null);
        going = r.state === "running" || !!r.check?.pending;
      } catch (e) {
        if (alive) setErr(String(e?.message || e));
      }
      if (alive && going) timer = setTimeout(load, 3000);
    };
    load();
    return () => { alive = false; clearTimeout(timer); };
  }, [id, open]);
  useEffect(() => {
    const el = logRef.current;
    if (el && rec?.state === "running") el.scrollTop = el.scrollHeight;
  }, [rec?.log_tail, rec?.state, showLog]);
  if (err && !rec) return <p className="text-xs text-rose-300">{err}</p>;
  if (!rec) return <p className="text-xs text-slate-500">Reading…</p>;
  const { shown, hidden, first, total } = stepsShown(rec.steps, allSteps);
  const logShown = showLog ?? logOpen(rec);
  return (
    <div className="flex flex-col gap-2">
      {shown.length ? (
        <div className="flex flex-col gap-1">
          {hidden > 0 && (
            <button type="button" onClick={() => setAllSteps(true)} className="self-start text-[11px] text-indigo-400 hover:text-indigo-300">
              Show all {total} steps
            </button>
          )}
          <ol className="flex flex-col gap-0.5 text-xs text-slate-400">
            {shown.map((st, i) => (
              <li key={first + i} className="flex gap-2"><span className="w-5 shrink-0 text-right font-mono text-slate-600">{first + i}</span>{st}</li>
            ))}
          </ol>
        </div>
      ) : <p className="text-xs text-slate-500">No steps recorded.</p>}
      {rec.check && <p className={`text-xs ${rec.check.ok === false ? "text-rose-300" : "text-slate-400"}`}>Read back: {rec.check.message}</p>}
      {rec.error && <p className="text-xs text-rose-300">{rec.error}</p>}
      {err && <p className="text-[11px] text-rose-300">Last read failed: {err}. Showing what was read before.</p>}
      <button type="button" onClick={() => setShowLog(!logShown)} aria-expanded={logShown}
        className="self-start text-[11px] text-indigo-400 hover:text-indigo-300">{logShown ? "Hide the Ansible output" : "Show the Ansible output"}</button>
      {logShown && <pre ref={logRef} className="h-56 overflow-auto whitespace-pre-wrap rounded border border-slate-800 bg-slate-950 p-2 font-mono text-[11px] text-slate-400">{rec.log_tail || "No output yet."}</pre>}
    </div>
  );
}

// Every piece run, from the dashboard and the CLI (Foundations spec, 4):
// newest first, a row opens its steps, read-back and log. /operations#<id>
// opens that run.
export default function OperationsPage() {
  const ops = useResource("operations", () => getOperations(), { every: 5000 });
  const pieces = useResource("pieces", getPieces);
  const { hash } = useLocation();
  const [filter, setFilter] = useState("all");
  const [open, setOpen] = useState(() => new Set(hash ? [hash.slice(1)] : []));
  useEffect(() => { if (hash) setOpen((s) => new Set([...s, hash.slice(1)])); }, [hash]);
  const rows = sortNewest(ops.data).filter(MATCH[filter]);
  const toggle = (id) => setOpen((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  return (
    <div>
      <h3 className="mb-3 text-sm font-medium uppercase tracking-wide text-slate-400">Operations</h3>
      <Card title="Piece runs" sub="The 50 newest runs, from the dashboard and from kelt run-piece." updatedAt={ops.updatedAt}
        busy={!ops.data && !ops.error} error={ops.error ? String(ops.error.message || ops.error) : null}
        actions={<div className="flex gap-1">{FILTERS.map(([k, l]) => (
          <button key={k} type="button" onClick={() => setFilter(k)} aria-pressed={filter === k}
            className={`rounded-full border px-2.5 py-0.5 text-[11px] transition-colors ${filter === k ? "border-indigo-500/60 bg-indigo-600/20 text-indigo-200" : "border-slate-700 text-slate-400 hover:bg-slate-800"}`}>{l}</button>))}</div>}>
        {rows.length === 0 ? (
          <p className="px-4 py-6 text-xs text-slate-500">
            {!ops.data ? "Reading the record…" : filter === "all" ? "No runs yet." : `No ${filter === "running" ? "running" : "failed"} runs.`}
          </p>
        ) : (
          <ul className="divide-y divide-slate-800">
            {rows.map((r) => {
              const res = resultOf(r);
              const isOpen = open.has(r.id);
              return (
                <li key={r.id} id={r.id} className="t-acc" data-open={isOpen ? "true" : "false"}>
                  <button type="button" onClick={() => toggle(r.id)} aria-expanded={isOpen}
                    className="grid w-full grid-cols-[5.5rem_minmax(0,1fr)_auto_14px] items-center gap-3 px-4 py-2.5 text-left text-xs transition-colors hover:bg-slate-800/40 md:grid-cols-[6.5rem_minmax(0,1fr)_9rem_8rem_7rem_14px]">
                    <span className="font-mono tabular-nums text-slate-500">{when(r.started)}</span>
                    <span className="min-w-0 truncate text-slate-200">{r.title || r.piece}<span className="ml-2 text-slate-500">from {r.source || "cli"}{r.user ? ` · ${r.user}` : ""}</span></span>
                    <span className="hidden truncate font-mono text-indigo-200 md:block">{r.piece}</span>
                    <span className={TONE[res.tone]}>{res.label}</span>
                    <span className="hidden font-mono tabular-nums text-slate-500 md:block" title={expectedOf(r, pieces.data) ? `expected ${expectedOf(r, pieces.data)}` : undefined}>{progressOf(r, pieces.data)}</span>
                    <span className="t-acc-chevron text-slate-500">{CHEVRON}</span>
                  </button>
                  <div className="t-acc-panel"><div className="t-acc-panel-inner"><div className="px-4 pb-3">
                    <Detail id={r.id} open={isOpen} />
                  </div></div></div>
                </li>
              );
            })}
          </ul>
        )}
      </Card>
    </div>
  );
}
