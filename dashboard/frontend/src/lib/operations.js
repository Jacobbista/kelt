// Reading the runner's operation records for the header and the Operations
// page. Pure (operations.test.js).
const ts = (s) => (s ? Date.parse(s) : NaN);

export function sortNewest(records) {
  return [...(records || [])].sort((a, b) => (ts(b.started) || 0) - (ts(a.started) || 0));
}

// Three running lines at most (the header never scrolls), the rest counted,
// and the newest run that is not running.
export function headerSummary(records) {
  const all = sortNewest(records);
  const running = all.filter((r) => r.state === "running");
  return { running: running.slice(0, 3), more: Math.max(0, running.length - 3), last: all.find((r) => r.state !== "running") || null };
}

export function took(rec, now = Date.now()) {
  const start = ts(rec?.started);
  if (Number.isNaN(start)) return "—";
  const end = rec.state === "running" ? now : ts(rec.ended);
  if (Number.isNaN(end)) return "—";
  const s = Math.max(0, Math.round((end - start) / 1000));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min`;
  return `${Math.floor(m / 60)} h ${m % 60} min`;
}

// "Done" means the state was read back: the check decides. A run of a piece
// that reads back, and whose read-back has not run yet (it runs when the run is
// opened), exited 0 and is not done yet.
export function resultOf(rec) {
  switch (rec?.state) {
    case "running": return { label: "running", tone: "run" };
    case "done":
      if (rec.check?.ok === false) return { label: "failed", tone: "bad" };
      if (rec.check?.pending) return { label: "exited 0, reading back", tone: "muted" };
      if (!rec.check && rec.reads_back) return { label: "exited 0, not read back yet", tone: "muted" };
      return rec.check?.ok === null ? { label: "done, not read back", tone: "ok" } : { label: "done", tone: "ok" };
    case "failed": return { label: "failed", tone: "bad" };
    case "interrupted": return { label: "interrupted", tone: "bad" };
    default: return { label: "unknown", tone: "muted" };
  }
}

// The record's limits as typed in Settings; the backend and the runner check
// the same bounds, this gives the message before a request is sent.
export const RETENTION_BOUNDS = { days: [1, 3650], mb: [1, 10240] };
const whole = (s, [lo, hi]) => /^\d+$/.test(String(s).trim()) && Number(s) >= lo && Number(s) <= hi;

export function retentionError(days, mb) {
  const { days: d, mb: m } = RETENTION_BOUNDS;
  if (!whole(days, d)) return `Days must be a whole number from ${d[0]} to ${d[1]}.`;
  if (!whole(mb, m)) return `Size must be a whole number from ${m[0]} to ${m[1]} MB.`;
  return null;
}

const dur = (s) => (s < 60 ? `${s} s` : `${Math.round(s / 60)} min`);

// The expected time of a run, from its piece's takes_s in the registry.
export function expectedOf(rec, pieces) {
  const s = pieces?.[rec?.piece]?.takes_s;
  return s ? `~${dur(s)}` : null;
}

// How long a run took, or, while it runs, how far it is against the expected time.
export function progressOf(rec, pieces, now = Date.now()) {
  const t = took(rec, now);
  const exp = expectedOf(rec, pieces);
  return rec?.state === "running" && exp ? `${t} of ${exp}` : t;
}

// The piece's own steps, without Ansible's fact gathering. Long lists show
// the last five; `first` is the number of the first shown.
export function stepsShown(steps, all, keep = 5) {
  const list = (steps || []).filter((s) => s !== "Gathering Facts");
  const total = list.length;
  if (all || total <= keep) return { shown: list, hidden: 0, first: 1, total };
  return { shown: list.slice(-keep), hidden: total - keep, first: total - keep + 1, total };
}

// The Ansible output is for finding out why a run failed: open by itself
// only then, on request otherwise.
export function logOpen(rec) {
  return rec?.state === "failed" || rec?.check?.ok === false;
}
