import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Panel, btn } from "../components/ui";
import { IconArrowLeft } from "../components/icons";
import { useConfirm } from "../context/ConfirmContext";
import { useAuth } from "../auth/AuthContext";
import {
  getStorageUsage,
  getStoragePreview,
  pruneImages,
  vacuumJournal,
  registryGarbageCollect,
} from "../api";

// Disk state for the worker node. The point of this page is the breakdown, not the
// percentage: almost all of a KELT node's disk is extracted container images, so an
// operator who prunes the registry expecting GB back gets a few hundred MB and no
// explanation. Each consumer is shown as its own number, largest first.

function fmt(bytes) {
  if (bytes === null || bytes === undefined) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let n = bytes;
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i += 1; }
  return `${n < 10 && i > 0 ? n.toFixed(1) : Math.round(n)} ${units[i]}`;
}

function Bar({ segments, total }) {
  return (
    <>
      <div className="flex h-2 w-full overflow-hidden rounded bg-slate-800">
        {segments.map((s) => (
          <div
            key={s.label}
            className={s.color}
            style={{ width: `${total ? (s.bytes / total) * 100 : 0}%` }}
            title={`${s.label}: ${fmt(s.bytes)}`}
          />
        ))}
      </div>
    </>
  );
}

function Row({ label, bytes, total, hint, accent = "text-slate-200", color }) {
  const pct = total ? (bytes / total) * 100 : 0;
  return (
    <div className="flex items-baseline gap-3 py-1.5 text-xs">
      <span className={`flex min-w-[190px] items-baseline gap-2 font-medium ${accent}`}>
        {/* Ties the row to its slice of the bar above. Rows with no slice (the
            registry, which is part of the volumes) deliberately have no swatch. */}
        <span className={`h-2 w-2 shrink-0 rounded-sm ${color || "bg-transparent"}`} />
        {label}
      </span>
      <span className="w-20 text-right font-mono text-slate-300">{fmt(bytes)}</span>
      <span className="w-12 text-right font-mono text-[11px] text-slate-500">
        {pct >= 0.1 ? `${pct.toFixed(1)}%` : "—"}
      </span>
      {hint && <span className="flex-1 text-[11px] text-slate-500">{hint}</span>}
    </div>
  );
}

export default function StoragePage() {
  const auth = useAuth();
  const isAdmin = auth.roles.includes("dashboard-admin");
  const confirm = useConfirm();

  const [data, setData] = useState(null);
  const [preview, setPreview] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async (refresh = false) => {
    setLoading(true);
    setError("");
    try {
      setData(await getStorageUsage(refresh));
      // Estimates are secondary: a failure here must not blank the whole page.
      getStoragePreview().then(setPreview).catch(() => setPreview(null));
    } catch (e) {
      setError(e.message || "Could not read disk usage");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(false); }, [load]);

  async function run(name, label, fn, confirmBody, confirmLabel) {
    if (confirmBody && !(await confirm({
      title: label, body: confirmBody, confirmLabel, danger: true,
    }))) return;
    setBusy(name);
    setNote("");
    setError("");
    try {
      const res = await fn();
      // A blob count alone does not say whether the run is worth doing, so the
      // dry run reports the bytes those blobs occupy.
      const size = res.bytes === null || res.bytes === undefined ? "" : ` (${fmt(res.bytes)})`;
      setNote(
        res.freed !== undefined
          ? `${label}: freed ${fmt(res.freed)}, ${fmt(res.free_after)} free.`
          : res.blobs_eligible === 0
            ? `${label}: nothing to collect.`
            : `${label}: ${res.blobs_eligible} blob(s)${size} ${res.dry_run ? "would be deleted; nothing was touched." : "deleted."}`,
      );
      await load(true);
    } catch (e) {
      setError(e.message || `${label} failed`);
    } finally {
      setBusy("");
    }
  }

  const fs = data?.filesystem;
  const pruneEst = preview?.prune_images;
  const journalEst = preview?.vacuum_journal;
  const other = fs
    ? Math.max(0, fs.used - (data.containerd.snapshots + data.containerd.content
        + data.pvcs_total + data.journal))
    : 0;

  return (
    <div className="svc-fade flex flex-col gap-4 pb-8">
        <Link to="/settings" className="inline-flex w-fit items-center gap-1 text-xs text-slate-400 hover:text-slate-200">
          <IconArrowLeft size={14} /> Settings
        </Link>
      <header>
        <h2 className="text-lg font-semibold text-slate-100">Storage</h2>
        <p className="text-xs text-slate-500">
          Disk usage on the worker node, broken down by what actually occupies it, and the
          actions that reclaim space.
        </p>
      </header>

      <Panel
        title={`Disk — ${data?.node || "worker"}`}
        hint="What actually occupies the node filesystem. Sizes are measured by walking the tree, so they are refreshed on request rather than polled."
        right={
          <button type="button" onClick={() => load(true)} disabled={loading} className={btn.ghost}>
            {loading ? "measuring…" : "re-measure"}
          </button>
        }
      >
        {loading && !data ? (
          <div className="flex flex-col gap-2">
            <div className="h-2 w-full animate-pulse rounded bg-slate-800" />
            <div className="h-3 w-2/3 animate-pulse rounded bg-slate-800/70" />
            <div className="h-3 w-1/2 animate-pulse rounded bg-slate-800/70" />
          </div>
        ) : !fs ? (
          <p className="text-xs text-slate-500">No disk information available.</p>
        ) : (
          <>
            <div className="mb-3 flex items-baseline gap-3">
              <span className="font-mono text-lg text-slate-100">{fmt(fs.used)}</span>
              <span className="text-xs text-slate-500">used of {fmt(fs.total)}</span>
              <span className={`text-xs font-medium ${fs.used_pct >= 85 ? "text-amber-300" : "text-slate-400"}`}>
                {fs.used_pct}%
              </span>
              <span className="flex-1" />
              <span className="text-xs text-slate-400">{fmt(fs.free)} free</span>
            </div>

            <Bar
              total={fs.total}
              segments={[
                { label: "Container image layers", bytes: data.containerd.snapshots, color: "bg-indigo-500/70" },
                { label: "Container image blobs", bytes: data.containerd.content, color: "bg-indigo-400/40" },
                { label: "Persistent volumes", bytes: data.pvcs_total, color: "bg-sky-500/60" },
                { label: "System journals", bytes: data.journal, color: "bg-slate-500/60" },
                { label: "Everything else", bytes: other, color: "bg-slate-700" },
              ]}
            />

            <div className="mt-3 divide-y divide-slate-800/60">
              <Row
                label="Container image layers" color="bg-indigo-500/70" bytes={data.containerd.snapshots} total={fs.total}
                hint="Extracted images on the node. Normally the largest consumer."
              />
              <Row
                label="Container image blobs" color="bg-indigo-400/40" bytes={data.containerd.content} total={fs.total}
                hint="Compressed layers kept alongside the extracted copy."
              />
              <Row label="Persistent volumes" color="bg-sky-500/60" bytes={data.pvcs_total} total={fs.total} />
              <Row
                label="In-cluster registry" bytes={data.registry} total={fs.total}
                accent="text-slate-400"
                hint="Part of the volumes above. Pruning it frees roughly this much, not the image layers."
              />
              <Row label="System journals" color="bg-slate-500/60" bytes={data.journal} total={fs.total} />
              <Row label="Everything else" color="bg-slate-700" bytes={other} total={fs.total} accent="text-slate-400"
                   hint="OS, packages, logs outside journald." />
            </div>

            {data.pvcs?.length > 0 && (
              <div className="mt-4">
                <p className="mb-1 text-[10px] font-medium uppercase tracking-wide text-slate-500">
                  Volumes by claim
                </p>
                <div className="divide-y divide-slate-800/60">
                  {data.pvcs.map((p) => (
                    <div key={`${p.namespace}/${p.claim}`} className="flex items-baseline gap-3 py-1 text-xs">
                      <span className="min-w-[190px] text-slate-300">
                        <span className="text-slate-500">{p.namespace}/</span>{p.claim}
                      </span>
                      <span className="w-20 text-right font-mono text-slate-400">{fmt(p.bytes)}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </Panel>

      {isAdmin && (
        <Panel
          title="Reclaim space"
          hint="Ordered by how much they typically free. None of these touch running workloads or volume data. Figures are estimates; the amount actually freed is measured afterwards."
        >
          {note && <p className="mb-2 text-xs text-emerald-300">{note}</p>}
          {error && <p className="mb-2 text-xs text-rose-300">{error}</p>}
          <div className="flex flex-col divide-y divide-slate-800/60">
            <div className="flex items-center gap-3 py-2 text-xs">
              <span className="min-w-[190px] font-medium text-slate-200">Prune unused images</span>
              <span className="flex-1 text-[11px] text-slate-500">
                Removes image layers no container references. This is the one that frees GB.
                {pruneEst?.available && (
                  <span className="block text-slate-400">
                    {pruneEst.count === 0
                      ? "Nothing unused right now."
                      : `About ${fmt(pruneEst.bytes)} across ${pruneEst.count} of ${pruneEst.total} images.`}
                  </span>
                )}
              </span>
              <button
                type="button" className={btn.sky}
                disabled={busy !== "" || pruneEst?.count === 0}
                onClick={() => run("prune", "Prune unused images", pruneImages,
                  "Image layers not referenced by any container will be deleted from the node. Running workloads are unaffected; the next deploy of a removed image re-pulls it.", "Prune")}
              >
                {busy === "prune" ? "pruning…" : "prune"}
              </button>
            </div>

            <div className="flex items-center gap-3 py-2 text-xs">
              <span className="min-w-[190px] font-medium text-slate-200">Vacuum journals</span>
              <span className="flex-1 text-[11px] text-slate-500">
                Trims systemd logs to the 500M cap the deployment configures.
                {journalEst?.available && (
                  <span className="block text-slate-400">
                    {journalEst.bytes === 0
                      ? `Nothing to trim: ${fmt(journalEst.current)} is already under the ${fmt(journalEst.cap)} cap.`
                      : `About ${fmt(journalEst.bytes)} over the cap.`}
                  </span>
                )}
              </span>
              <button
                type="button" className={btn.ghost}
                disabled={busy !== "" || journalEst?.bytes === 0}
                onClick={() => run("journal", "Vacuum journals", vacuumJournal,
                  "Journal entries beyond the most recent 500M will be discarded. Past logs are lost; current logging is unaffected.", "Vacuum")}
              >
                {busy === "journal" ? "vacuuming…" : "vacuum"}
              </button>
            </div>

            <div className="flex items-center gap-3 py-2 text-xs">
              <span className="min-w-[190px] font-medium text-slate-200">Registry garbage collect</span>
              <span className="flex-1 text-[11px] text-slate-500">
                Drops unreferenced blobs from the in-cluster registry. Expect hundreds of MB at most.
                <span className="block text-slate-400">
                  Run the dry run to see exactly what would go.
                </span>
              </span>
              <button
                type="button" className={btn.ghost} disabled={busy !== ""}
                onClick={() => run("gc-dry", "Registry garbage collect (dry run)",
                  () => registryGarbageCollect(true))}
              >
                {busy === "gc-dry" ? "checking…" : "dry run"}
              </button>
              <button
                type="button" className={btn.amber} disabled={busy !== ""}
                onClick={() => run("gc", "Registry garbage collect", () => registryGarbageCollect(false),
                  "Unreferenced blobs will be deleted from the registry store. Images that are still tagged are kept.", "Collect")}
              >
                {busy === "gc" ? "collecting…" : "collect"}
              </button>
            </div>
          </div>
        </Panel>
      )}
    </div>
  );
}
