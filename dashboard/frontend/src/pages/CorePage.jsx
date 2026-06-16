import React, { useEffect, useState, useCallback, useRef } from "react";
import { getNfStatus, getAmfCniAlert, restartDeployment, scaleAmfController, getNfVersions, getNfUpdateStreamUrl } from "../api";
import Loader from "../components/Loader";
import NfCard from "../components/NfCard";
import AmfCniRepairModal from "../components/AmfCniRepairModal";

const SECTIONS = [
  { key: "control_plane", title: "Control Plane" },
  { key: "user_plane", title: "User Plane" },
  { key: "data", title: "Data" },
  { key: "other", title: "Other" },
];

export default function CorePage({ onOpenLogs, onOpenTerminal, onOpenIperf3Logs, expandNfType }) {
  const [nfStatus, setNfStatus] = useState(null);
  const [error, setError] = useState("");
  const [expandedPod, setExpandedPod] = useState(null);
  const [restartingDeps, setRestartingDeps] = useState(new Set());
  const [amfCniAlert, setAmfCniAlert] = useState(null);
  const [controllerModalOpen, setControllerModalOpen] = useState(false);
  const [nfVersions, setNfVersions] = useState([]);
  const [versionsLoading, setVersionsLoading] = useState(false);
  const [versionsCheckedAt, setVersionsCheckedAt] = useState(null);
  const [updateModal, setUpdateModal] = useState(null); // {nf, tag}
  const [updateLog, setUpdateLog] = useState([]);
  const [updating, setUpdating] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const data = await getNfStatus();
      setNfStatus(data);

      const allRunning = [
        ...(data.control_plane || []),
        ...(data.user_plane || []),
        ...(data.data || []),
        ...(data.other || []),
      ];
      setRestartingDeps((prev) => {
        const next = new Set(prev);
        for (const dep of prev) {
          const pod = allRunning.find((p) => p.deployment === dep);
          if (pod && pod.phase === "Running") {
            next.delete(dep);
          }
        }
        return next;
      });
    } catch (err) {
      setError(String(err.message || err));
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 3000);
    return () => clearInterval(id);
  }, [refresh]);

  const refreshAmfAlert = useCallback(async () => {
    try {
      const data = await getAmfCniAlert();
      setAmfCniAlert(data ?? { active: false });
    } catch (_) {
      setAmfCniAlert({ active: false });
    }
  }, []);

  useEffect(() => {
    refreshAmfAlert();
    const id = setInterval(refreshAmfAlert, 15000);
    return () => clearInterval(id);
  }, [refreshAmfAlert]);

  const refreshVersions = useCallback(async () => {
    setVersionsLoading(true);
    try {
      const data = await getNfVersions();
      setNfVersions(Array.isArray(data) ? data : []);
      setVersionsCheckedAt(new Date());
    } catch (_) {
      setVersionsCheckedAt(new Date());
    } finally {
      setVersionsLoading(false);
    }
  }, []);

  // Version check runs once on mount — versions.json changes rarely.
  // Manual refresh available via the button in the header.
  useEffect(() => { refreshVersions(); }, [refreshVersions]);

  // Build lookup: deployment name → version info
  const versionMap = Object.fromEntries(
    nfVersions.map((v) => [v.nf, v])
  );

  useEffect(() => {
    if (expandNfType && nfStatus) {
      const all = [
        ...nfStatus.control_plane,
        ...nfStatus.user_plane,
        ...nfStatus.data,
        ...nfStatus.other,
      ];
      const match = all.find((nf) => nf.nf_type === expandNfType);
      if (match) setExpandedPod(match.name);
    }
  }, [expandNfType, nfStatus]);

  async function handleScaleController(controller, replicas) {
    try {
      await scaleAmfController(controller.kind, controller.name, replicas, "5g");
      setTimeout(refresh, 1000);
      setTimeout(refreshAmfAlert, 1200);
    } catch (err) {
      setError(String(err?.message || err));
    }
  }

  async function handleRestart(nf) {
    if (!nf.deployment) return;
    try {
      setError("");
      setRestartingDeps((prev) => new Set(prev).add(nf.deployment));
      await restartDeployment("5g", nf.deployment);
      setTimeout(refresh, 1500);
    } catch (err) {
      setRestartingDeps((prev) => {
        const next = new Set(prev);
        next.delete(nf.deployment);
        return next;
      });
      setError(String(err.message || err));
    }
  }

  async function handleUpdate(nf, tag) {
    setUpdateLog([]);
    setUpdating(true);
    try {
      const resp = await fetch(getNfUpdateStreamUrl(), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ nf, tag }),
      });
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop();
        for (const line of lines) {
          if (!line.trim()) continue;
          try {
            const ev = JSON.parse(line);
            if (ev.type === "log") setUpdateLog((l) => [...l, ev.line]);
            if (ev.type === "result" || ev.type === "error") {
              setUpdateLog((l) => [...l, ev.detail || ev.type]);
            }
          } catch (_) {}
        }
      }
    } catch (err) {
      setUpdateLog((l) => [...l, `Error: ${err.message}`]);
    } finally {
      setUpdating(false);
      refreshVersions();
      refresh();
    }
  }

  if (!nfStatus) {
    return (
      <div className="flex min-h-[200px] items-center justify-center">
        <Loader size="lg" label="Loading NF status…" />
      </div>
    );
  }

  const allNfs = [
    ...(nfStatus.control_plane || []),
    ...(nfStatus.user_plane || []),
    ...(nfStatus.data || []),
    ...(nfStatus.other || []),
  ];
  const runningCount = allNfs.filter((p) => p.phase === "Running").length;

  return (
    <div className="svc-fade">
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold text-slate-100">5G Core Network Functions</h2>
          <p className="text-xs text-slate-500">{runningCount}/{allNfs.length} running · Open5GS service-based architecture</p>
        </div>
        <button
          type="button"
          onClick={refreshVersions}
          disabled={versionsLoading}
          className="rounded bg-slate-800 px-2 py-1 text-[10px] text-slate-400 hover:bg-slate-700 hover:text-slate-200 disabled:opacity-50 disabled:cursor-not-allowed"
          title="Check for NF image updates from 5g-nf-platform"
        >
          {versionsLoading ? (
            <span className="flex items-center gap-1">
              <span className="h-2 w-2 rounded-full bg-slate-400 animate-pulse" />
              checking…
            </span>
          ) : versionsCheckedAt ? (
            <span className="flex items-center gap-1">
              {nfVersions.some((v) => v.update_available) ? (
                <span className="text-amber-400">↑ updates available</span>
              ) : (
                <span className="text-emerald-400">✓ up to date</span>
              )}
              <span className="text-slate-600 ml-1">
                {versionsCheckedAt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
              </span>
            </span>
          ) : (
            "check updates"
          )}
        </button>
      </div>

      {amfCniAlert?.active && (
        <div className="mb-4 flex items-center justify-between rounded-lg border border-amber-600/50 bg-amber-950/40 px-4 py-3 text-sm text-amber-200">
          <span className="flex items-center gap-2">
            <span className="h-2 w-2 rounded-full bg-amber-500 animate-pulse" />
            AMF controller/network issue detected (file-exists, duplicate ReplicaSets, or stuck pods)
          </span>
          <button
            type="button"
            onClick={() => setControllerModalOpen(true)}
            className="rounded bg-amber-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-amber-500 transition-colors"
          >
            Manage
          </button>
        </div>
      )}

      {error && (
        <div className="mb-4 flex items-center justify-between rounded border border-rose-700 bg-rose-950/50 p-3 text-sm text-rose-300">
          <span>{error}</span>
          <button
            type="button"
            onClick={() => setError("")}
            className="ml-3 text-rose-400 hover:text-white"
          >
            &#x2715;
          </button>
        </div>
      )}

      {SECTIONS.map(({ key, title }) => {
        const items = nfStatus[key] || [];
        if (items.length === 0) return null;
        return (
          <div key={key} className="mb-6">
            <h3 className="mb-2 flex items-center gap-2 text-sm font-medium uppercase tracking-wide text-slate-400">
              {title}
              <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] tabular-nums text-slate-500">{items.length}</span>
            </h3>
            <div className="flex flex-col gap-2">
              {items.map((nf) => (
                <NfCard
                  key={nf.name}
                  nf={nf}
                  expanded={expandedPod === nf.name}
                  onToggle={() => setExpandedPod(expandedPod === nf.name ? null : nf.name)}
                  onRestart={handleRestart}
                  onOpenLogs={onOpenLogs}
                  onOpenTerminal={onOpenTerminal}
                  onOpenIperf3Logs={onOpenIperf3Logs}
                  isRestarting={restartingDeps.has(nf.deployment)}
                  versionInfo={versionMap[nf.deployment] || null}
                  onUpdate={(tag) => { setUpdateModal({ nf: nf.deployment, tag }); setUpdateLog([]); }}
                />
              ))}
            </div>
          </div>
        );
      })}

      <AmfCniRepairModal
        open={controllerModalOpen}
        onClose={() => setControllerModalOpen(false)}
        alert={amfCniAlert}
        onScale={handleScaleController}
      />

      {updateModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="w-full max-w-lg rounded-lg border border-slate-700 bg-slate-900 p-5 shadow-xl">
            <h3 className="mb-3 text-sm font-semibold text-white">
              Update {updateModal.nf} → <span className="font-mono text-indigo-300">{updateModal.tag}</span>
            </h3>
            {updateLog.length > 0 && (
              <pre className="mb-3 max-h-48 overflow-auto rounded bg-slate-950 border border-slate-700 p-3 text-[10px] font-mono text-slate-300 whitespace-pre-wrap">
                {updateLog.join("\n")}
              </pre>
            )}
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => { setUpdateModal(null); setUpdateLog([]); }}
                disabled={updating}
                className="rounded bg-slate-800 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-700 disabled:opacity-50"
              >
                {updating ? "Running…" : "Close"}
              </button>
              {!updating && updateLog.length === 0 && (
                <button
                  type="button"
                  onClick={() => handleUpdate(updateModal.nf, updateModal.tag)}
                  className="rounded bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-500"
                >
                  Confirm update
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
