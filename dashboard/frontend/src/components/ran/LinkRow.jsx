import React, { useEffect, useRef, useState } from "react";
import { TextSwap } from "../Card";
import PieceButton from "../PieceButton";

const GLYPH = {
  ok: <span className="grid h-4 w-4 place-items-center rounded-full bg-emerald-500/15 ring-1 ring-emerald-500/40"><svg width="9" height="9" viewBox="0 0 10 10" fill="none" stroke="#34d399" strokeWidth="1.8"><path d="M2 5.3l2 2L8 3" strokeLinecap="round" strokeLinejoin="round" /></svg></span>,
  bad: <span className="grid h-4 w-4 place-items-center rounded-full bg-rose-500/15 ring-1 ring-rose-500/50"><svg width="8" height="8" viewBox="0 0 10 10" fill="none" stroke="#fb7185" strokeWidth="1.8"><path d="M2.5 2.5l5 5M7.5 2.5l-5 5" strokeLinecap="round" /></svg></span>,
  idle: <span className="grid h-4 w-4 place-items-center rounded-full ring-1 ring-slate-600"><span className="h-1.5 w-1.5 rounded-full bg-slate-500" /></span>,
  blocked: <span className="block h-4 w-4 rounded-full border border-dashed border-slate-600" />,
  checking: <span className="grid h-4 w-4 place-items-center rounded-full ring-1 ring-indigo-500/50"><span className="h-1.5 w-1.5 animate-pulse rounded-full bg-indigo-400 motion-reduce:animate-none" /></span>,
};
const VERDICT = { ok: "text-slate-300", bad: "text-rose-300", idle: "text-slate-400", blocked: "text-slate-500" };
const CHEVRON = <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M3 4.5l3 3 3-3" strokeLinecap="round" strokeLinejoin="round" /></svg>;

function Fix({ fix, linkId, runningPiece, pieceHandlers }) {
  if (!fix) return null;
  if (fix.piece) {
    const blocked = runningPiece && runningPiece !== fix.piece ? "Another RAN action is running" : null;
    return <PieceButton piece={fix.piece} disabledReason={blocked} {...pieceHandlers(fix.piece, linkId)} />;
  }
  if (fix.cli) {
    return (
      <div className="flex max-w-xl flex-col gap-2">
        <p className="text-xs text-slate-400">Nothing to bring up from here. On the machine that hosts the VMs, this saves the adapter; then <span className="font-mono">kelt provision</span> restarts the worker with it, so every pod on the worker restarts (about 2 minutes).</p>
        <pre className="rounded border border-slate-800 bg-slate-950 p-2 font-mono text-[11px] text-slate-300">{fix.cli}</pre>
      </div>
    );
  }
  if (fix.checklist) {
    return (
      <div className="flex flex-col gap-2">
        <p className="text-xs text-slate-400">The break is outside the testbed: nothing on this page can fix it.</p>
        <ul className="flex flex-col gap-1 text-xs text-slate-300">
          {fix.checklist.map((t) => <li key={t} className="flex gap-2"><span className="text-slate-600">·</span>{t}</li>)}
        </ul>
        <p className="text-xs text-slate-400">The page reads the path again every 10 s.</p>
      </div>
    );
  }
  if (fix.see === "setup") {
    return <p className="text-xs text-slate-400">Check the AMF address in the gNB's configuration ("Settings for the gNB", in the gNB card) and the gNB's console.</p>;
  }
  return null;
}

// One link of the chain: status mark on the rail, name, verdict, value, and a
// drawer with what was read and the fix. A broken link opens by itself and
// closes when it recovers.
// A link opens by itself when it has something to do: broken, or idle with a
// fix (detached: Attach). It closes when that goes away.
const wantsOpen = (link) => link.state === "bad" || (link.state === "idle" && !!link.fix);

export default function LinkRow({ link, isLast, checking, delay = 0, runningPiece, pieceHandlers, stepText }) {
  const [open, setOpen] = useState(wantsOpen(link));
  const prev = useRef(wantsOpen(link));
  const want = wantsOpen(link);
  useEffect(() => {
    if (want && !prev.current) setOpen(true);
    if (!want && prev.current) setOpen(false);
    prev.current = want;
  }, [want]);

  const busy = checking || (stepText && runningPiece);
  const hasDetail = link.state !== "blocked" && (link.facts.length > 0 || link.fix);
  const verdictText = busy ? (stepText || "checking…") : link.verdict;
  const bad = link.state === "bad" && !busy;
  return (
    <li className={`t-acc relative rounded-md transition-colors ${bad ? "bg-rose-950/20 ring-1 ring-rose-900/40" : ""}`}
        data-open={open && hasDetail ? "true" : "false"} style={{ transitionDelay: `${delay}ms` }}>
      {!isLast && <span className={`absolute left-[17.5px] top-[28px] -bottom-4 w-px transition-colors duration-300 ${link.state === "ok" && !busy ? "bg-emerald-500/40" : "bg-slate-700/70"}`} />}
      <button type="button" disabled={!hasDetail} onClick={() => setOpen((o) => !o)} aria-expanded={open && hasDetail}
        className={`relative grid w-full grid-cols-[16px_minmax(0,1fr)_14px] items-center gap-3 rounded-md px-2.5 py-2.5 text-left transition-colors sm:grid-cols-[16px_9.5rem_minmax(0,1fr)_14px] xl:grid-cols-[16px_9.5rem_minmax(0,1fr)_auto_14px] ${hasDetail ? "hover:bg-slate-800/40" : "cursor-default"}`}>
        <span>{busy ? GLYPH.checking : GLYPH[link.state]}</span>
        <span className="text-sm font-medium text-slate-200">
          {link.name}
          <span className={`mt-0.5 block text-xs font-normal sm:hidden ${VERDICT[link.state]}`}>{verdictText}</span>
        </span>
        <span className="hidden min-w-0 truncate text-xs sm:block">
          {busy
            ? <span className="t-shimmer" data-text={verdictText}>{verdictText}</span>
            : <TextSwap text={link.verdict} className={VERDICT[link.state]} />}
        </span>
        <span className="hidden font-mono text-[11px] text-slate-500 xl:block">{link.value}</span>
        <span className="t-acc-chevron text-slate-500" style={{ visibility: hasDetail ? "visible" : "hidden" }}>{CHEVRON}</span>
      </button>
      <div className="t-acc-panel">
        <div className="t-acc-panel-inner">
          <div className="flex flex-col gap-3 pb-3 pl-[42px] pr-3">
            {link.facts.length > 0 && (
              <div className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs text-slate-400">
                {link.facts.map(([k, v]) => (
                  <React.Fragment key={k}><span>{k}</span><span className="font-mono text-slate-300">{v}</span></React.Fragment>
                ))}
              </div>
            )}
            <Fix fix={link.fix} linkId={link.id} runningPiece={runningPiece} pieceHandlers={pieceHandlers} />
          </div>
        </div>
      </div>
    </li>
  );
}
