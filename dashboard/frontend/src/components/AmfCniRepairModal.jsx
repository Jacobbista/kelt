import React from "react";

function controllerKey(controller) {
  return `${controller.kind}:${controller.name}`;
}

export default function AmfCniRepairModal({ open, onClose, alert, onScale }) {
  const [desiredByController, setDesiredByController] = React.useState({});
  const [busyController, setBusyController] = React.useState("");
  const [opError, setOpError] = React.useState("");

  React.useEffect(() => {
    if (!open) return;
    const next = {};
    const deployments = alert?.controllers?.deployments || [];
    const replicasets = alert?.controllers?.replicasets || [];
    [...deployments, ...replicasets].forEach((controller) => {
      next[controllerKey(controller)] = String(controller.desired ?? 0);
    });
    setDesiredByController(next);
    setBusyController("");
    setOpError("");
  }, [open, alert]);

  async function handleScale(controller) {
    const key = controllerKey(controller);
    const raw = desiredByController[key] ?? "0";
    const target = Number.parseInt(raw, 10);
    if (!Number.isFinite(target) || target < 0) {
      setOpError(`Invalid replicas for ${controller.kind}/${controller.name}`);
      return;
    }
    setBusyController(key);
    setOpError("");
    try {
      await onScale(controller, target);
    } catch (err) {
      setOpError(String(err?.message || err));
    } finally {
      setBusyController("");
    }
  }

  if (!open) return null;
  const deployments = alert?.controllers?.deployments || [];
  const replicasets = alert?.controllers?.replicasets || [];
  const reasons = alert?.reasons || [];
  const events = alert?.events || [];
  const stuckPods = alert?.stuck_pods || [];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80">
      <div className="w-full max-w-3xl rounded-lg border border-slate-700 bg-slate-900 p-4">
        <div className="mb-3 flex items-center justify-between">
          <h3 className="font-semibold text-slate-200">AMF Controller Manager</h3>
          <button
            type="button"
            onClick={onClose}
            className="rounded bg-slate-700 px-3 py-1 text-sm font-medium text-slate-200 hover:bg-slate-600"
          >
            Close
          </button>
        </div>

        <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
          {reasons.map((reason) => (
            <span key={reason} className="rounded bg-amber-900/50 px-2 py-1 text-amber-300">
              {reason}
            </span>
          ))}
          {reasons.length === 0 && (
            <span className="rounded bg-emerald-900/40 px-2 py-1 text-emerald-300">No active AMF controller issues</span>
          )}
        </div>

        {events.length > 0 && (
          <div className="mb-3 rounded border border-amber-800/40 bg-amber-950/20 p-2 text-xs text-amber-200">
            <div className="mb-1 font-medium">Recent FailedCreatePodSandBox events</div>
            <div className="space-y-1">
              {events.slice(0, 3).map((event) => (
                <div key={`${event.pod_name}-${event.last_seen}`}>
                  <span className="font-mono">{event.pod_name}</span> — {event.message}
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="grid gap-3 md:grid-cols-2">
          <div className="rounded border border-slate-800 bg-slate-950 p-2">
            <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">Deployments</div>
            <div className="space-y-2">
              {deployments.map((controller) => {
                const key = controllerKey(controller);
                return (
                  <div key={key} className="rounded border border-slate-800 p-2 text-xs">
                    <div className="mb-1 text-slate-300">
                      <span className="font-mono">{controller.name}</span> desired={controller.desired} ready={controller.ready}
                    </div>
                    <div className="flex items-center gap-2">
                      <input
                        type="number"
                        min="0"
                        className="w-20 rounded border border-slate-700 bg-slate-900 px-2 py-1 text-slate-200"
                        value={desiredByController[key] ?? "0"}
                        onChange={(e) => setDesiredByController((prev) => ({ ...prev, [key]: e.target.value }))}
                      />
                      <button
                        type="button"
                        onClick={() => handleScale(controller)}
                        disabled={busyController === key}
                        className="rounded bg-sky-700 px-2 py-1 text-white hover:bg-sky-600 disabled:opacity-50"
                      >
                        {busyController === key ? "Scaling..." : "Scale"}
                      </button>
                    </div>
                  </div>
                );
              })}
              {deployments.length === 0 && <div className="text-xs text-slate-500">No AMF deployments found.</div>}
            </div>
          </div>

          <div className="rounded border border-slate-800 bg-slate-950 p-2">
            <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">ReplicaSets</div>
            <div className="space-y-2">
              {replicasets.map((controller) => {
                const key = controllerKey(controller);
                return (
                  <div key={key} className="rounded border border-slate-800 p-2 text-xs">
                    <div className="mb-1 text-slate-300">
                      <span className="font-mono">{controller.name}</span> desired={controller.desired} ready={controller.ready}
                    </div>
                    <div className="flex items-center gap-2">
                      <input
                        type="number"
                        min="0"
                        className="w-20 rounded border border-slate-700 bg-slate-900 px-2 py-1 text-slate-200"
                        value={desiredByController[key] ?? "0"}
                        onChange={(e) => setDesiredByController((prev) => ({ ...prev, [key]: e.target.value }))}
                      />
                      <button
                        type="button"
                        onClick={() => handleScale(controller)}
                        disabled={busyController === key}
                        className="rounded bg-amber-700 px-2 py-1 text-white hover:bg-amber-600 disabled:opacity-50"
                      >
                        {busyController === key ? "Scaling..." : "Scale"}
                      </button>
                    </div>
                  </div>
                );
              })}
              {replicasets.length === 0 && <div className="text-xs text-slate-500">No AMF replicasets found.</div>}
            </div>
          </div>
        </div>

        {stuckPods.length > 0 && (
          <div className="mt-3 rounded border border-rose-900/40 bg-rose-950/20 p-2 text-xs text-rose-300">
            <div className="mb-1 font-medium">Stuck AMF pods</div>
            <div className="space-y-1">
              {stuckPods.map((pod) => (
                <div key={pod.name}>
                  <span className="font-mono">{pod.name}</span> phase={pod.phase} node={pod.node || "-"}
                </div>
              ))}
            </div>
          </div>
        )}

        {opError && (
          <div className="mt-3 rounded border border-rose-700/50 bg-rose-950/30 px-3 py-2 text-sm text-rose-300">
            {opError}
          </div>
        )}

        <p className="mt-3 text-xs text-slate-500">
          Tip: keep AMF deployment at 1 replica, scale old AMF ReplicaSets to 0 if duplicated.
        </p>
      </div>
    </div>
  );
}
