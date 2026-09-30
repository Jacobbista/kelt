import React from "react";
import { getPieces, runPiece } from "../api";
import useResource from "../data/useResource";
import { ConfirmAction } from "./Card";

// Starts a piece (ansible/pieces.yml) with the in-card confirm filled from the
// registry. It only starts: the caller follows the operation (`onStarted`
// gets the runner's answer, `{id, state}`), because the caller outlives the
// button — a fix button disappears as soon as its link recovers.
export default function PieceButton({ piece, label, running, onStarted, onFailed, disabledReason, variant = "primary", onRunningClick }) {
  const pieces = useResource("pieces", getPieces);
  const p = pieces.data?.[piece];

  const run = async (typed) => {
    try {
      onStarted?.(await runPiece(piece, typed));
    } catch (e) {
      onFailed?.(String(e?.message || e));
    }
  };

  if (!p) return null;
  return (
    <ConfirmAction
      label={label || p.title}
      tier={p.tier}
      variant={variant}
      rows={[
        ["Runs", <span className="font-mono text-indigo-200">kelt run-piece {piece}</span>],
        ["Changes", p.changes],
        ...(p.stops ? [["Stops", p.stops]] : []),
        ...(p.takes_s ? [["Takes", `about ${p.takes_s} s`]] : []),
      ]}
      confirmLabel={p.title}
      running={running}
      runningLabel="Running…"
      disabledReason={disabledReason}
      onRun={run}
      onRunningClick={onRunningClick}
      confirmWord={p.confirm_word || null}
    />
  );
}
