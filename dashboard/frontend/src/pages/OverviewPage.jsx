import React, { useEffect, useState } from "react";
import { Area, AreaChart, ResponsiveContainer } from "recharts";
import { useNavigate } from "react-router-dom";
import { getApps, getClusterSummary, getMetricsOverview, getNfStatus, getNodeMetrics, getNodeMetricsRange, getNorthboundServices } from "../api";
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
  // Optional layers: absent (not deployed, or the caller may not read them)
  // simply hides the section, the overview never errors on them.
  const [exposure, setExposure] = useState([]);
  const [apps, setApps] = useState([]);
  const navigate = useNavigate();

  async function refresh() {
    try {
      setError("");
      const [c, nf] = await Promise.all([getClusterSummary(), getNfStatus()]);
      setCluster(c);
      setNfStatus(nf);
      getNorthboundServices().then((r) => setExposure(r.services || [])).catch(() => setExposure([]));
      getApps().then((r) => setApps(r.apps || [])).catch(() => setApps([]));

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
              {nf.restarts > 0 && (() => {
                const ageMs = nf.start_time ? Date.now() - new Date(nf.start_time).getTime() : Infinity;
                const recent = ageMs < 3600_000;
                return (
                  <span
                    className={`ml-2 ${recent ? "text-rose-400" : "text-slate-500"}`}
                    title={`${nf.restarts} restarts — pod up since ${nf.start_time ? new Date(nf.start_time).toLocaleString() : "unknown"}`}
                  >
                    {nf.restarts} restarts
                  </span>
                );
              })()}
            </div>
            <div className="mt-1 truncate text-[10px] text-slate-600 font-mono">{nf.node || ""}</div>
          </button>
        ))}
      </div>

      {exposure.length > 0 && (
        <>
          <h3 className="mb-3 mt-6 text-sm font-medium text-slate-400 uppercase tracking-wide">Exposure stack</h3>
          <div className="grid grid-cols-4 gap-3 xl:grid-cols-6">
            {exposure.map((svc) => (
              <WorkloadCard
                key={svc.name}
                name={svc.name}
                caption={svc.subtitle || svc.role}
                pods={svc.pods}
                replicas={svc.replicas}
                readyReplicas={svc.ready_replicas}
                onClick={() => navigate("/services")}
              />
            ))}
          </div>
        </>
      )}

      {apps.length > 0 && (
        <>
          <h3 className="mb-3 mt-6 text-sm font-medium text-slate-400 uppercase tracking-wide">Edge apps</h3>
          <div className="grid grid-cols-4 gap-3 xl:grid-cols-6">
            {apps.map((app) => (
              <WorkloadCard
                key={app.name}
                name={app.name}
                caption={[app.mec_attached ? `n6m ${app.mec_ip || ""}`.trim() : null, app.exposed ? "exposed" : null].filter(Boolean).join(" · ") || "internal"}
                pods={app.pods}
                replicas={app.replicas}
                readyReplicas={app.ready_replicas}
                onClick={() => navigate("/services/apps")}
              />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

// One deployment as a card, in the same idiom as the network-function cards:
// status dot from the first pod's phase, restarts flagged when recent, the node
// line replaced by what the layer knows (role, n6m address, exposure).
function WorkloadCard({ name, caption, pods, replicas, readyReplicas, onClick }) {
  const pod = (pods || [])[0];
  const phase = pod?.phase || (replicas === 0 ? "Scaled to 0" : "Pending");
  const ready = replicas > 0 && readyReplicas === replicas;
  const restarts = (pods || []).reduce((n, p) => n + (p.restarts || 0), 0);
  return (
    <button
      type="button"
      onClick={onClick}
      className="group rounded-lg border border-slate-700 bg-slate-900 p-3 text-left transition-colors hover:border-indigo-600/50 hover:bg-slate-800"
    >
      <div className="flex items-center gap-2">
        <span className={`h-2 w-2 rounded-full ${replicas === 0 ? "bg-slate-500" : ready ? statusColor(phase) : "bg-amber-400 animate-pulse"}`} />
        <span className="truncate text-xs font-semibold text-white">{name}</span>
      </div>
      <div className="mt-2 text-[10px] text-slate-500">
        <span className={ready ? "text-emerald-400" : replicas === 0 ? "text-slate-500" : "text-amber-400"}>
          {replicas === 0 ? "Scaled to 0" : ready ? "Running" : `${readyReplicas ?? 0}/${replicas} ready`}
        </span>
        {restarts > 0 && <span className="ml-2 text-slate-500">{restarts} restarts</span>}
      </div>
      <div className="mt-1 truncate text-[10px] text-slate-600 font-mono">{caption || ""}</div>
    </button>
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
