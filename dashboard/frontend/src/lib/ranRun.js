// What the RAN page shows about its pieces and its headline, as pure
// functions (tested in ranRun.test.js).
// The pieces this page runs; they share the runner's `ran` lock, so at most
// one of them runs at a time.
export const RAN_PIECES = ["ran_link", "ran_attach", "ran_detach"];

const newestRunning = (records) =>
  [...(records || [])].filter((r) => r.state === "running" && RAN_PIECES.includes(r.piece))
    .sort((a, b) => Date.parse(b.started || 0) - Date.parse(a.started || 0))[0] || null;

// The RAN piece running now: one started on this page, or one still running
// from before (the dashboard, another tab, the CLI), with its operation id.
export function runningFrom(localPiece, records) {
  if (localPiece) return { piece: localPiece, id: null };
  const r = newestRunning(records);
  return r ? { piece: r.piece, id: r.id } : { piece: null, id: null };
}

// Progress text for a piece: the last step its running record reached.
export function stepFor(piece, records) {
  const r = newestRunning(records);
  const steps = (r && r.piece === piece ? r.steps || [] : []).filter((s) => s !== "Gathering Facts");
  return steps.length ? `${steps[steps.length - 1]}…` : null;
}

// "Done" means the state read back after the action shows the effect.
export function verifyAfter(piece, targetId, chain) {
  if (!chain) return { ok: false, message: "the RAN status could not be read back" };
  if (piece === "ran_detach") {
    return chain.state === "detached" ? { ok: true, message: null } : { ok: false, message: "the RAN still reads as attached" };
  }
  if (chain.state === "detached") return { ok: false, message: "the RAN still reads as detached" };
  const link = chain.links.find((l) => l.id === targetId);
  if (link?.state === "bad") return { ok: false, message: `still broken at ${link.name}: ${link.verdict}` };
  return { ok: true, message: null };
}

const DOING = { ran_attach: "Attaching…", ran_detach: "Detaching…", ran_link: "Bringing the link up…" };

// The newest RAN piece run that has ended (the card's last result), from the
// runner's records: the same after a reload or a visit to another page.
export function lastRanRun(records) {
  return [...(records || [])].filter((r) => r.state !== "running" && RAN_PIECES.includes(r.piece))
    .sort((a, b) => Date.parse(b.ended || b.started || 0) - Date.parse(a.ended || a.started || 0))[0] || null;
}

// The path card's headline: checking, could not read, a RAN piece running
// (what is being done, not "broken": the chain is expected to be half way),
// detached, serving, or broken at.
export function pathHeadline(chain, counts, error, running = null) {
  if (running?.piece && chain) {
    return { title: DOING[running.piece] || "Working…", sub: running.step || "Started; reading its steps.", dot: "bg-indigo-400", checking: false };
  }
  if (!chain) {
    return error
      ? { title: "Could not read the RAN status", sub: error, dot: "bg-rose-400", checking: false }
      : { title: "Checking the path…", sub: "From the cable to the UEs, in order.", dot: "bg-slate-500", checking: true };
  }
  if (chain.state === "detached") {
    return { title: "Detached", sub: "The core is not connected to the RAN. Attach brings it back.", dot: "bg-slate-500", checking: false };
  }
  if (chain.state === "serving") {
    const ues = counts?.ues ?? 0;
    const sub = ues > 0
      ? `1 gNB, ${ues} UE${ues === 1 ? "" : "s"}. Every link from the cable to the UEs checked.`
      : "1 gNB, no UE yet. Every link up to the UEs checked.";
    return { title: "Serving", sub, dot: "bg-emerald-400", checking: false };
  }
  const first = chain.links.find((l) => l.id === chain.first_broken);
  return { title: `Broken at ${first.name.toLowerCase()}`, sub: `${first.verdict}.`, dot: "bg-rose-400", checking: false };
}

// Following a piece's operation (PieceButton starts it, the page follows it:
// the page outlives the button, which unmounts when its link recovers).
// null = follow it; otherwise the start already failed, with why.
export function followStart(answer) {
  if (answer?.state === "failed") return { ok: false, message: answer.error || "the run did not start" };
  if (!answer?.id) return { ok: false, message: "the runner did not say which run holds the piece" };
  return null;
}

const MAX_READ_ERRORS = 3;

// One poll's result → keep waiting, or done with {ok, message}.
// `result` is {rec} or {error}; `errors` counts failed reads in a row.
export function followStep(result, errors) {
  if (result.error) {
    if (result.error.status === 404) return { done: { ok: false, message: "the operation's record is gone" } };
    const n = errors + 1;
    return n >= MAX_READ_ERRORS
      ? { done: { ok: false, message: `lost track of the run (${n} failed reads)` } }
      : { wait: true, errors: n };
  }
  const rec = result.rec;
  if (rec.state === "running") return { wait: true, errors: 0 };
  if (rec.state === "interrupted") return { done: { ok: false, message: "the run was interrupted" } };
  // Exited 0 and the read-back is still settling (an AMF being replaced).
  if (rec.state === "done" && rec.check?.pending) return { wait: true, errors: 0 };
  // `checked`: the backend read the state back and it held; that is final.
  if (rec.state === "done") return { done: { ok: rec.check?.ok !== false, message: rec.check?.message || "", checked: rec.check?.ok === true } };
  const why = rec.check?.ok === false ? rec.check.message : rec.error || (rec.exit != null ? `the playbook exited ${rec.exit}` : "the run failed");
  return { done: { ok: false, message: why } };
}
