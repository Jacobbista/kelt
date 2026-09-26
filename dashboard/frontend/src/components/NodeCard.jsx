import React, { useLayoutEffect, useRef, useState } from "react";

// Same thresholds and bar idiom as MetricsPage's node cards (pctColor/pctBar
// there), so a reader sees the same emerald/amber/rose cutoffs everywhere.
function pctColor(v) {
  if (v > 80) return "text-rose-400";
  if (v > 60) return "text-amber-400";
  return "text-emerald-400";
}

function pctBar(v) {
  if (v > 80) return "bg-rose-500";
  if (v > 60) return "bg-amber-500";
  return "bg-emerald-500";
}

// The node-exporter scrape job relabels `instance` to the k3s node name
// (prometheus-config.yaml.j2), not the usual "<ip>:9100" target address, so
// join on the node's name.
function findByName(list, name) {
  if (!name) return null;
  const hit = (list || []).find((m) => m.label === name);
  return hit ? hit.value : null;
}

export default function NodeCard({ node, metrics }) {
  const isReady = node.status === "Ready";
  const rows = [
    { label: "CPU", value: findByName(metrics?.cpu, node.name) },
    { label: "Mem", value: findByName(metrics?.memory, node.name) },
  ].filter((r) => r.value != null);
  const hasMetrics = rows.length > 0;

  // Measure the meter block once it has real rows, so the card can tween its
  // height to that value instead of the block popping in under a fixed layout
  // (transitions.dev card-resize: a plain CSS height transition, JS only
  // supplies the two end states).
  const meterRef = useRef(null);
  const [meterHeight, setMeterHeight] = useState(0);
  useLayoutEffect(() => {
    if (hasMetrics && meterRef.current) setMeterHeight(meterRef.current.scrollHeight);
  }, [hasMetrics, rows]);

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
      </div>
      {/* Per-node, not a duplicate of the cluster-wide average above it: this is
          the only place that answers "which node is under pressure". Grows
          into place the first time per-node metrics arrive. */}
      <div className="t-resize overflow-hidden" style={{ height: hasMetrics ? meterHeight : 0 }}>
        {/* The gap above the divider has to be padding, not margin: only
            padding on the measured element is counted by scrollHeight. */}
        <div ref={meterRef} className="pt-3">
          <div className="flex flex-col gap-2 border-t border-slate-800 pt-3">
            {rows.map(({ label, value }) => (
              <div key={label}>
                <div className="mb-1 flex items-baseline justify-between">
                  <span className="text-[10px] text-slate-500">{label}</span>
                  <span className={`text-xs font-semibold tabular-nums ${pctColor(value)}`}>
                    {value.toFixed(1)}%
                  </span>
                </div>
                <div className="h-1.5 rounded-full bg-slate-800">
                  <div
                    className={`h-full rounded-full transition-all ${pctBar(value)}`}
                    style={{ width: `${Math.min(value, 100)}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
