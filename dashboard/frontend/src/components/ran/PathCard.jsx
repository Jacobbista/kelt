import React from "react";
import { Card } from "../Card";
import PieceButton from "../PieceButton";
import LinkRow from "./LinkRow";
import { pathHeadline } from "../../lib/ranRun";

// The page's main card: the verdict, then the five links in order.
export default function PathCard({ phys, updatedAt, error, runningPiece, stepText, recheck, pieceHandlers, last }) {
  const chain = phys?.chain;
  const head = pathHeadline(chain, phys?.counts, chain ? null : error, runningPiece ? { piece: runningPiece, step: stepText } : null);
  // Detach while anything is attached (br-ran or the AMF on it), whatever the
  // intent says; not when a link already offers it (a detach left half way).
  const linkOffersDetach = (chain?.links || []).some((l) => l.fix?.piece === "ran_detach");
  const attached = !!(phys?.bridge_exists || phys?.amf_attached_to_bridge) && !linkOffersDetach;

  return (
    <Card
      title={head.title}
      sub={head.sub}
      dot={head.dot}
      updatedAt={updatedAt}
      busy={(!chain && head.checking) || !!runningPiece || recheck}
      error={error}
      footer={
        <div className="border-t border-slate-800 px-4 py-2.5 text-[11px] text-slate-500">
          <div className="flex flex-wrap items-start gap-x-3 gap-y-2">
            <span className="min-w-0 flex-1 py-1.5">{last || "No operation run from this page yet."}</span>
          </div>
          {/* DISRUPT: amber, at the card's foot, apart from anything routine; typed confirmation (pieces.yml). */}
          {(attached || (runningPiece === "ran_detach" && !linkOffersDetach)) && (
            <div className="mt-2 border-t border-slate-800 pt-2">
              <PieceButton piece="ran_detach" label="Detach…"
                disabledReason={runningPiece && runningPiece !== "ran_detach" ? "Another RAN action is running" : null}
                {...pieceHandlers("ran_detach", "bridge")} />
            </div>
          )}
        </div>
      }
    >
      <ol className="relative flex flex-col gap-1 p-2">
        {(chain?.links || PLACEHOLDER).map((link, i, all) => (
          <LinkRow key={link.id} link={link} isLast={i === all.length - 1}
            checking={(!chain && head.checking) || recheck} delay={recheck ? 0 : i * 60}
            stepText={runningPiece && chain && (link.id === (chain.first_broken || "bridge")) ? stepText : null}
            runningPiece={runningPiece} pieceHandlers={pieceHandlers} />
        ))}
      </ol>
    </Card>
  );
}

const PLACEHOLDER = [
  ["cable", "Cable and link"], ["bridge", "RAN bridge"], ["core", "Core connection"], ["user_plane", "User plane"], ["ues", "UEs"],
].map(([id, name]) => ({ id, name, state: "blocked", verdict: "Not read yet", value: "", facts: [], fix: null }));
