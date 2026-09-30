import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { getGnbConsole, getOperation, getOperations, getPieces, getRanModesStatus } from "../api";
import useResource from "../data/useResource";
import PathCard from "../components/ran/PathCard";
import GnbCard from "../components/ran/GnbCard";
import SimulatedRan from "../components/ran/SimulatedRan";
import { resourceCache } from "../lib/resourceCache";
import { followStart, followStep, lastRanRun, runningFrom, stepFor, verifyAfter } from "../lib/ranRun";
import { resultOf } from "../lib/operations";

const hhmm = () => new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });

// The RAN page (spec .local/specs/2026-09-26-ran-page.md): does the physical
// gNB reach the core, where does the path break, what fixes it.
export default function RanPage() {
  const navigate = useNavigate();
  const modes = useResource("ran-modes", getRanModesStatus, { every: 10000 });
  const con = useResource("gnb-console", getGnbConsole);
  const [localPiece, setLocalPiece] = useState(null);
  const [recheck, setRecheck] = useState(false);
  const [last, setLast] = useState(null);
  const [step, setStep] = useState(null);
  const [follow, setFollow] = useState(null); // {piece, targetId, id}: a piece run the page follows
  const pieces = useResource("pieces", getPieces);
  // The runner's records (shared with the header's Operations button).
  const ops = useResource("operations", () => getOperations(), { every: 5000 });
  const titleOf = (piece) => pieces.data?.[piece]?.title || piece;

  const phys = modes.data?.physical;
  const err = modes.error ? String(modes.error.message || modes.error) : null;
  // A RAN piece started here, or one still running from before (another tab,
  // the CLI); the runner's `ran` lock keeps it to one at a time (ranRun.js).
  const { piece: runningPiece, id: runningId } = runningFrom(localPiece, ops.data);
  const stepText = runningPiece ? (step || stepFor(runningPiece, ops.data) || `${titleOf(runningPiece)}…`) : null;
  // The last result: this page's own when it ran one, else the runner's
  // newest RAN record (after a reload, another page, or a CLI run).
  const lastRec = lastRanRun(ops.data);
  const lastRes = lastRec && resultOf(lastRec);
  const lastShown = last || (lastRec && (
    <button type="button" onClick={() => navigate(`/operations#${lastRec.id}`)} className="text-left hover:text-slate-300">
      <span className={lastRes.tone === "ok" ? "text-emerald-400" : lastRes.tone === "bad" ? "text-rose-400" : "text-slate-400"}>
        {lastRes.label} {lastRec.ended ? new Date(lastRec.ended).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false }) : ""}
      </span> · {lastRec.title || lastRec.piece} · from {lastRec.source || "cli"}
    </button>
  ));

  const begin = (piece) => {
    setLocalPiece(piece);
    setStep(null);
    setLast(<><span className="text-indigo-300">Running</span> · {titleOf(piece)}</>);
  };

  // After a fix, read the state back with a request made after it ended
  // (never a poll that started before), and call it done only if the link it
  // fixes no longer reads broken.
  // `checked`: the backend already read the state back and it held (the
  // piece's own check); then the chain is only shown, not judged again (it
  // also carries what the piece does not control, like the gNB's NG Setup).
  const finish = async (piece, targetId, failure, checked = false) => {
    setRecheck(true);
    await Promise.all([modes.refresh({ after: true }), ops.refresh({ after: true })]);
    const check = failure ? null : checked ? { ok: true } : verifyAfter(piece, targetId, resourceCache.snapshot("ran-modes").data?.physical?.chain);
    if (!failure && check.ok) {
      setLast(<><span className="text-emerald-400">✓ Done {hhmm()}</span> · {titleOf(piece)}, state read back</>);
    } else {
      setLast(<><span className="text-rose-400">✗ Failed {hhmm()}</span> · {titleOf(piece)}: {failure || check.message}</>);
    }
    setLocalPiece(null);
    setRecheck(false);
    setStep(null);
  };

  // Every RAN action is a piece (ansible/pieces.yml): PieceButton starts it,
  // the page follows the operation (it outlives the button) until it ends.
  const pieceHandlers = (piece, targetId) => ({
    running: runningPiece === piece,
    onStarted: (answer) => {
      begin(piece);
      const failed = followStart(answer);
      if (failed) finish(piece, targetId, failed.message);
      else setFollow({ piece, targetId, id: answer.id });
    },
    onFailed: (message) => { begin(piece); finish(piece, targetId, message); },
    // A running button opens its operation.
    onRunningClick: () => {
      const id = follow?.piece === piece ? follow.id : runningId;
      navigate(id ? `/operations#${id}` : "/operations");
    },
  });

  useEffect(() => {
    if (!follow) return undefined;
    let cancelled = false;
    (async () => {
      let errors = 0;
      for (;;) {
        await new Promise((r) => setTimeout(r, 2000));
        if (cancelled) return;
        let result;
        try { result = { rec: await getOperation(follow.id) }; } catch (error) { result = { error }; }
        if (cancelled) return;
        const next = followStep(result, errors);
        if (next.wait) {
          errors = next.errors;
          const steps = result.rec?.steps;
          if (steps?.length) setStep(`${steps[steps.length - 1]}…`);
          continue;
        }
        setFollow(null);
        finish(follow.piece, follow.targetId, next.done.ok ? null : next.done.message, next.done.checked);
        return;
      }
    })();
    return () => { cancelled = true; };
  }, [follow]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div>
      <h3 className="mb-3 text-sm font-medium uppercase tracking-wide text-slate-400">Physical RAN</h3>
      <div className="mb-6 grid items-start gap-3 lg:grid-cols-[minmax(0,1fr)_300px]">
        <PathCard phys={phys} updatedAt={modes.updatedAt} error={err}
          runningPiece={runningPiece} stepText={stepText} recheck={recheck}
          pieceHandlers={pieceHandlers} last={lastShown} />
        <GnbCard phys={phys} console={con.data} onConsoleChanged={() => con.refresh({ after: true })} />
      </div>
      <h3 className="mb-3 text-sm font-medium uppercase tracking-wide text-slate-400">Simulated RAN</h3>
      <SimulatedRan sim={modes.data?.ueransim} />
    </div>
  );
}
