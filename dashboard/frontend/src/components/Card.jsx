import React, { useEffect, useRef, useState } from "react";
import SignalBars from "./SignalBars";
import { triggerClass } from "../lib/tiers";
import { wordMatches } from "../lib/confirm";

function agoText(at, now) {
  const s = Math.max(0, Math.round((now - at) / 1000));
  if (s < 5) return "updated just now";
  if (s < 60) return `updated ${s} s ago`;
  return `updated ${Math.floor(s / 60)} min ago`;
}

export function UpdatedAgo({ at }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const id = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(id); }, []);
  if (!at) return null;
  return <span className="hidden font-mono text-[11px] text-slate-500 sm:inline">{agoText(at, now)}</span>;
}

// Text that changes in place: the old line blurs out upward, the new one in.
export function TextSwap({ text, className = "" }) {
  const [shown, setShown] = useState(text);
  const [cls, setCls] = useState("");
  useEffect(() => {
    if (text === shown) { setCls(""); return undefined; } // A→B→A within the swap
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) { setShown(text); return undefined; }
    setCls("is-exit");
    const id = setTimeout(() => {
      setShown(text);
      setCls("is-enter-start");
      requestAnimationFrame(() => requestAnimationFrame(() => setCls("")));
    }, 150);
    return () => clearTimeout(id);
  }, [text, shown]);
  return <span className={`t-text-swap ${cls} ${className}`}>{shown}</span>;
}

// A card of the dashboard: header (status dot, title, sub-line, "updated N s
// ago", actions, the loader slot), body, optional footer. A refresh never
// replaces the body; `error` is a line under the header, the data stays.
export function Card({ title, sub, dot, updatedAt, busy, error, actions, children, footer, className = "" }) {
  return (
    <section className={`rounded-lg border border-slate-700 bg-slate-900 ${className}`}>
      <div className="flex items-center gap-3 border-b border-slate-800 px-4 py-3">
        {dot && <span className={`h-2.5 w-2.5 shrink-0 rounded-full transition-colors ${dot}`} />}
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold text-white">{typeof title === "string" ? <TextSwap text={title} /> : title}</h3>
          {sub && <p className="truncate text-[11px] text-slate-500">{typeof sub === "string" ? <TextSwap text={sub} /> : sub}</p>}
        </div>
        <UpdatedAgo at={updatedAt} />
        {actions}
        <span className="inline-flex h-4 w-[18px] shrink-0 items-end justify-center">{busy && <SignalBars />}</span>
      </div>
      {error && <div className="border-b border-slate-800 px-4 py-1.5 text-[11px] text-rose-300">Last refresh failed: {error}.{updatedAt ? " Showing what was read before." : ""}</div>}
      {children}
      {footer}
    </section>
  );
}

const KIND = {
  change: { label: "Changes nothing that runs", line: "border-indigo-500/40", text: "text-indigo-300",
    go: "bg-indigo-600/25 text-indigo-200 ring-1 ring-indigo-500/40 hover:bg-indigo-600/35" },
  disrupt: { label: "Interrupts traffic", line: "border-amber-500/40", text: "text-amber-300",
    go: "bg-amber-600/25 text-amber-200 ring-1 ring-amber-500/40 hover:bg-amber-600/35" },
};

// An action that asks inside the card before it runs (Foundations spec, 2-3):
// the button opens a drawer under itself with what runs, what changes, what
// stops and for how long; the confirm button repeats the effect. While it
// runs the button says so and a second click never starts it again. With
// `confirmWord` (an action that cuts devices off) the confirm button waits
// until the word is typed.
export function ConfirmAction({ label, tier = "disrupt", rows, confirmLabel, onRun, running, runningLabel = "Running…", disabledReason, variant = "primary", onRunningClick, confirmWord }) {
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const goRef = useRef(null);
  const wordRef = useRef(null);
  const k = KIND[tier];
  const ready = wordMatches(typed, confirmWord);
  useEffect(() => {
    if (!open) { setTyped(""); return undefined; }
    const id = setTimeout(() => (confirmWord ? wordRef : goRef).current?.focus({ preventScroll: true }), 60);
    return () => clearTimeout(id);
  }, [open, confirmWord]);
  useEffect(() => { if (running) setOpen(false); }, [running]);
  const go = () => { if (!ready) return; setOpen(false); onRun(confirmWord ? typed.trim().toLowerCase() : undefined); };
  const click = () => {
    if (running) { onRunningClick?.(); return; }
    setOpen((o) => !o);
  };
  return (
    <div className="flex flex-col">
      <div className="flex flex-wrap items-center gap-3">
        <button type="button" onClick={click} disabled={!!disabledReason && !running} title={disabledReason || undefined}
          aria-expanded={open} className={`${triggerClass(variant, tier)} disabled:opacity-40`}>
          {running ? runningLabel : label}
        </button>
        {disabledReason && !running && <span className="text-[11px] text-slate-500">{disabledReason}</span>}
      </div>
      {/* Closed, the panel is only drawn away: inert keeps its confirm button out of Tab and Enter. */}
      <div className="t-acc" data-open={open ? "true" : "false"} {...(open ? {} : { inert: "" })}>
        <div className="t-acc-panel">
          <div className="t-acc-panel-inner">
            <div className={`mt-3 flex flex-col gap-2.5 border-t pt-3 ${k.line}`}>
              <span className={`text-[10px] font-semibold uppercase tracking-wider ${k.text}`}>{k.label}</span>
              <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-xs">
                {rows.map(([dt, dd]) => (
                  <React.Fragment key={dt}>
                    <dt className="text-slate-500">{dt}</dt>
                    <dd className={dt === "Stops" ? "text-amber-200" : "text-slate-300"}>{dd}</dd>
                  </React.Fragment>
                ))}
              </dl>
              {confirmWord && (
                <label className="flex flex-col gap-1 text-xs text-slate-400">
                  <span>Type <span className="font-mono text-amber-200">{confirmWord}</span> to confirm</span>
                  <input ref={wordRef} value={typed} onChange={(e) => setTyped(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter") go(); }}
                    autoComplete="off" spellCheck={false} aria-label={`Type ${confirmWord} to confirm`}
                    className="w-40 rounded border border-slate-700 bg-slate-950 px-2 py-1 font-mono text-xs text-slate-200 outline-none focus:border-amber-500/60" />
                </label>
              )}
              <div className="flex flex-wrap gap-2 pb-1">
                <button ref={goRef} type="button" onClick={go} disabled={!ready}
                  className={`rounded px-3 py-1.5 text-xs font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${k.go}`}>{confirmLabel}</button>
                <button type="button" onClick={() => setOpen(false)}
                  className="rounded bg-slate-700/60 px-3 py-1.5 text-xs font-medium text-slate-300 transition-colors hover:bg-slate-700">Cancel</button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
