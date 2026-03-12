import React, { useEffect, useState } from "react";
import { Area, AreaChart, ResponsiveContainer } from "recharts";
import { getClusterSummary, getMetricsOverview, getNfStatus, getNodeMetrics, getNodeMetricsRange } from "../api";
import Loader from "../components/Loader";
import NodeCard from "../components/NodeCard";

const NF_LABELS = {
  amf: "AMF", smf: "SMF", upf: "UPF", nrf: "NRF", udm: "UDM", udr: "UDR",
  ausf: "AUSF", pcf: "PCF", bsf: "BSF", nssf: "NSSF", mongodb: "MongoDB",
  gnb: "gNB", ue: "UE", unknown: "Other",
};

function statusColor(phase) {
  if (phase === "Running") return "bg-emerald-400";
  if (phase === "Pending" || phase === "ContainerCreating") return "bg-amber-400 animate-pulse";
  if (phase === "Terminating") return "bg-slate-500 animate-pulse";
  return "bg-rose-400";
}

function pctColor(v) {
  if (v > 80) return "text-rose-400";
  if (v > 60) return "text-amber-400";
  return "text-emerald-400";
}

function rangeToMini(rangeData) {
  if (!rangeData?.result?.[0]?.values) return [];
  const all = rangeData.result.flatMap((s) => s.values.map(([ts, v]) => ({ ts, v: parseFloat(v) })));
  const map = {};
  for (const { ts, v } of all) {
    map[ts] = (map[ts] || 0) + v;
  }
  const entries = Object.entries(map).sort(([a], [b]) => a - b);
  const count = rangeData.result.length || 1;
  return entries.map(([ts, total]) => ({ value: total / count }));
}

function MiniSparkline({ data, color = "#6366f1" }) {
  if (!data || data.length < 2) return null;
  return (
    <div style={{ width: 80, height: 28 }}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data}>
          <Area type="monotone" dataKey="value" stroke={color} fill={color} fillOpacity={0.2} strokeWidth={1.5} dot={false} isAnimationActive={false} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

export default function OverviewPage({ onNavigateToNf }) {
  const [cluster, setCluster] = useState(null);
  const [nfStatus, setNfStatus] = useState(null);
  const [metricsOv, setMetricsOv] = useState(null);
  const [nodeMetrics, setNodeMetrics] = useState(null);
  const [cpuMini, setCpuMini] = useState([]);
  const [memMini, setMemMini] = useState([]);
  const [error, setError] = useState("");

  async function refresh() {
    try {
      setError("");
      const [c, nf] = await Promise.all([getClusterSummary(), getNfStatus()]);
      setCluster(c);
      setNfStatus(nf);

      const [mo, nm, range] = await Promise.all([
        getMetricsOverview().catch(() => null),
        getNodeMetrics().catch(() => null),
        getNodeMetricsRange(15).catch(() => null),
      ]);
      setMetricsOv(mo);
      setNodeMetrics(nm);
      if (range) {
        setCpuMini(rangeToMini(range.cpu));
        setMemMini(rangeToMini(range.memory));
      }
    } catch (err) {
      setError(String(err.message || err));
    }
  }

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 10000);
    return () => clearInterval(id);
  }, []);

  if (error) {
    return <div className="rounded border border-rose-700 bg-rose-950/50 p-3 text-sm text-rose-300">{error}</div>;
  }
  if (!cluster || !nfStatus) {
    return (
      <div className="flex min-h-[200px] items-center justify-center">
        <Loader size="lg" label="Loading cluster data…" />
      </div>
    );
  }

  const allNfs = [...nfStatus.control_plane, ...nfStatus.user_plane, ...nfStatus.data, ...nfStatus.other];
  const { stats } = cluster;

  return (
    <div>
      <h2 className="mb-4 text-lg font-semibold">Cluster Overview</h2>

      <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-4 xl:grid-cols-6">
        <StatCard label="Total Pods" value={stats.total_pods} />
        <StatCard label="Running" value={stats.running} accent="text-emerald-400" />
        <StatCard label="Pending" value={stats.pending} accent="text-amber-400" />
        <StatCard label="Failed" value={stats.failed} accent="text-rose-400" />
        {metricsOv && (
          <>
            <div className="rounded-lg border border-slate-700 bg-slate-900 p-4">
              <div className="flex items-center justify-between">
                <div>
                  <div className={`text-2xl font-bold ${pctColor(metricsOv.avg_cpu_pct)}`}>{metricsOv.avg_cpu_pct}%</div>
                  <div className="mt-1 text-xs text-slate-400">Avg CPU</div>
                </div>
                <MiniSparkline data={cpuMini} color="#6366f1" />
              </div>
            </div>
            <div className="rounded-lg border border-slate-700 bg-slate-900 p-4">
              <div className="flex items-center justify-between">
                <div>
                  <div className={`text-2xl font-bold ${pctColor(metricsOv.avg_mem_pct)}`}>{metricsOv.avg_mem_pct}%</div>
                  <div className="mt-1 text-xs text-slate-400">Avg Memory</div>
                </div>
                <MiniSparkline data={memMini} color="#10b981" />
              </div>
            </div>
          </>
        )}
      </div>

      <h3 className="mb-3 text-sm font-medium text-slate-400 uppercase tracking-wide">Nodes</h3>
      <div className="mb-6 grid grid-cols-3 gap-3">
        {cluster.nodes.map((node) => (
          <NodeCard key={node.name} node={node} metrics={nodeMetrics} />
        ))}
      </div>

      <h3 className="mb-3 text-sm font-medium text-slate-400 uppercase tracking-wide">Network Functions</h3>
      <div className="grid grid-cols-4 gap-3 xl:grid-cols-6">
        {allNfs.map((nf) => (
          <button
            key={nf.name}
            type="button"
            onClick={() => onNavigateToNf?.(nf.nf_type)}
            className="group rounded-lg border border-slate-700 bg-slate-900 p-3 text-left transition-colors hover:border-indigo-600/50 hover:bg-slate-800"
          >
            <div className="flex items-center gap-2">
              <span className={`h-2 w-2 rounded-full ${statusColor(nf.phase)}`} />
              <span className="text-xs font-semibold text-white uppercase">
                {NF_LABELS[nf.nf_type] || nf.nf_type}
              </span>
            </div>
            <div className="mt-2 text-[10px] text-slate-500">
              <span className={
                nf.phase === "Running" ? "text-emerald-400"
                : nf.phase === "Terminating" ? "text-slate-500"
                : "text-amber-400"
              }>
                {nf.phase}
              </span>
              {nf.restarts > 0 && (
                <span className="ml-2 text-rose-400">{nf.restarts} restarts</span>
              )}
            </div>
            <div className="mt-1 truncate text-[10px] text-slate-600 font-mono">{nf.node || ""}</div>
          </button>
        ))}
      </div>
    </div>
  );
}

function StatCard({ label, value, accent = "text-white" }) {
  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900 p-4">
      <div className={`text-2xl font-bold ${accent}`}>{value}</div>
      <div className="mt-1 text-xs text-slate-400">{label}</div>
    </div>
  );
}
