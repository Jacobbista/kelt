import React from "react";

function statusColor(phase) {
  if (phase === "Running") return "bg-emerald-500";
  if (phase === "Pending") return "bg-amber-500";
  return "bg-rose-500";
}

export default function PodTable({ pods, onRestart, onOpenLogs }) {
  return (
    <div className="overflow-auto rounded border border-slate-700">
      <table className="w-full text-left text-sm">
        <thead className="bg-slate-900 text-slate-300">
          <tr>
            <th className="p-2">Pod</th>
            <th className="p-2">Deployment</th>
            <th className="p-2">Status</th>
            <th className="p-2">Restarts</th>
            <th className="p-2">Node</th>
            <th className="p-2">IP</th>
            <th className="p-2">Actions</th>
          </tr>
        </thead>
        <tbody>
          {pods.map((pod) => (
            <tr key={pod.name} className="border-t border-slate-800">
              <td className="p-2 font-mono">{pod.name}</td>
              <td className="p-2">{pod.deployment || "-"}</td>
              <td className="p-2">
                <span className="inline-flex items-center gap-2">
                  <span className={`h-2.5 w-2.5 rounded-full ${statusColor(pod.phase)}`} />
                  {pod.phase}
                </span>
              </td>
              <td className="p-2">{pod.restarts}</td>
              <td className="p-2">{pod.node || "-"}</td>
              <td className="p-2 font-mono">{pod.pod_ip || "-"}</td>
              <td className="p-2">
                <div className="flex gap-2">
                  <button
                    type="button"
                    disabled={!pod.deployment}
                    onClick={() => onRestart(pod)}
                    className="rounded bg-indigo-600 px-2 py-1 text-xs hover:bg-indigo-500 disabled:cursor-not-allowed disabled:bg-slate-700"
                  >
                    Restart
                  </button>
                  <button
                    type="button"
                    onClick={() => onOpenLogs(pod)}
                    className="rounded bg-slate-700 px-2 py-1 text-xs hover:bg-slate-600"
                  >
                    Log Stream
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
