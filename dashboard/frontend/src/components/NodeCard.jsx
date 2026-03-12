import React from "react";

export default function NodeCard({ node }) {
  const isReady = node.status === "Ready";
  return (
    <div className={`rounded-lg border p-4 ${
      isReady
        ? "border-slate-700 bg-slate-900"
        : "border-rose-700/50 bg-rose-950/30"
    }`}>
      <div className="flex items-center gap-2.5">
        <span className={`h-2.5 w-2.5 rounded-full ${isReady ? "bg-emerald-400" : "bg-rose-400"}`} />
        <span className="font-medium text-sm text-white">{node.name}</span>
      </div>
      <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-xs text-slate-400">
        <span>Status</span>
        <span className={isReady ? "text-emerald-400" : "text-rose-400"}>{node.status}</span>
        <span>IP</span>
        <span className="text-slate-300 font-mono">{node.ip || "—"}</span>
        <span>Roles</span>
        <span className="text-slate-300">{node.roles?.join(", ") || "—"}</span>
        {node.kubelet_version && (
          <>
            <span>Kubelet</span>
            <span className="text-slate-300">{node.kubelet_version}</span>
          </>
        )}
      </div>
    </div>
  );
}
