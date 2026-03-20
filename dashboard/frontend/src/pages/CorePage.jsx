import React, { useEffect, useState, useCallback } from "react";
import { getNfStatus, getAmfCniAlert, restartDeployment, scaleAmfController } from "../api";
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

  if (!nfStatus) {
    return (
      <div className="flex min-h-[200px] items-center justify-center">
        <Loader size="lg" label="Loading NF status…" />
      </div>
    );
  }

  return (
    <div>
      <h2 className="mb-4 text-lg font-semibold">5G Core Network Functions</h2>

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
            <h3 className="mb-2 text-sm font-medium text-slate-400 uppercase tracking-wide">{title}</h3>
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
    </div>
  );
}
