import React, { useEffect, useState } from "react";
import { Area, AreaChart, ResponsiveContainer } from "recharts";
import { useNavigate } from "react-router-dom";
import { getApps, getClusterSummary, getMetricsOverview, getNfStatus, getNodeMetrics, getNodeMetricsRange, getNorthboundServices } from "../api";
import { useIsolationSummary } from "../context/IsolationSummaryContext";
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

function relativeTime(ts) {
  if (!ts) return null;
  const diffSec = Math.floor((Date.now() - new Date(ts).getTime()) / 1000);
  if (diffSec < 0 || isNaN(diffSec)) return null;
  if (diffSec < 60) return `${diffSec}s ago`;
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
  return `${Math.floor(diffSec / 3600)}h ago`;
}

// Real signals only, each already fetched for another section of this page:
// a node down, a failed NF, a recent restart, a service that hasn't reached
// its replica count. Nothing here is polled separately.
function computeAttention(cluster, allNfs, exposure, apps) {
  const items = [];
  for (const node of cluster?.nodes || []) {
    if (node.status !== "Ready") {
      items.push({ key: `node-${node.name}`, dotClass: "bg-rose-400", text: `Node ${node.name} is not Ready` });
    }
  }
  for (const nf of allNfs) {
    const label = NF_LABELS[nf.nf_type] || nf.nf_type;
    if (nf.phase === "Failed") {
      items.push({ key: `nf-failed-${nf.name}`, dotClass: "bg-rose-400", text: `${label} failed` });
    } else if (nf.restarts > 0 && nf.start_time) {
      const age = Date.now() - new Date(nf.start_time).getTime();
      if (age < 3600_000) {
        items.push({ key: `nf-restart-${nf.name}`, dotClass: "bg-amber-400", text: `${label} restarted and rejoined the cluster`, time: relativeTime(nf.start_time) });
      }
    }
  }
  for (const svc of [...exposure, ...apps]) {
    if (svc.replicas > 0 && svc.ready_replicas !== svc.replicas) {
      items.push({ key: `svc-${svc.name}`, dotClass: "bg-amber-400", text: `${svc.name} is not fully ready (${svc.ready_replicas ?? 0}/${svc.replicas})` });
    }
  }
  return items;
}

export default function OverviewPage() {
  const [cluster, setCluster] = useState(null);
  const [nfStatus, setNfStatus] = useState(null);
  const [metricsOv, setMetricsOv] = useState(null);
  const [nodeMetrics, setNodeMetrics] = useState(null);
  const [cpuMini, setCpuMini] = useState([]);
  const [memMini, setMemMini] = useState([]);
  const [error, setError] = useState("");
  // Optional layers: absent (not deployed, or the caller may not read them)
  // simply hides the section, the overview never errors on them. Loaded is
  // tracked separately from the array itself, so a still-empty first fetch
  // reads as "waiting" (skeleton) rather than "confirmed not deployed" (gone).
  const [exposure, setExposure] = useState([]);
  const [exposureLoaded, setExposureLoaded] = useState(false);
  const [apps, setApps] = useState([]);
  const isolation = useIsolationSummary();
  const [appsLoaded, setAppsLoaded] = useState(false);
  const navigate = useNavigate();

  async function refresh() {
    try {
      setError("");
      const [c, nf] = await Promise.all([getClusterSummary(), getNfStatus()]);
      setCluster(c);
      setNfStatus(nf);
      getNorthboundServices()
        .then((r) => setExposure(r.services || []))
        .catch(() => setExposure([]))
        .finally(() => setExposureLoaded(true));
      getApps()
        .then((r) => setApps(r.apps || []))
        .catch(() => setApps([]))
        .finally(() => setAppsLoaded(true));

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
  const attention = computeAttention(cluster, allNfs, exposure, apps);

  const coreRunning = allNfs.filter((nf) => nf.phase === "Running").length;
  const exposureRunning = exposure.filter((s) => s.replicas > 0 && s.ready_replicas === s.replicas).length;
  const appsRunning = apps.filter((a) => a.replicas > 0 && a.ready_replicas === a.replicas).length;

  return (
    <div>

      <h3 className="mb-3 text-sm font-medium text-slate-400 uppercase tracking-wide">Cluster</h3>
      <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-4 xl:grid-cols-6">
        <StatCard label="Total Pods" value={stats.total_pods} />
        <StatCard label="Running" value={stats.running} accent="text-emerald-400" />
        <StatCard label="Pending" value={stats.pending} accent="text-amber-400" />
        <StatCard label="Failed" value={stats.failed} accent="text-rose-400" />
        <SkeletonStat revealed={!!metricsOv}>
          {metricsOv && (
            <div className="flex items-center justify-between">
              <div>
                <div className={`text-2xl font-bold ${pctColor(metricsOv.avg_cpu_pct)}`}>{metricsOv.avg_cpu_pct}%</div>
                <div className="mt-1 text-xs text-slate-400">Avg CPU</div>
              </div>
              <MiniSparkline data={cpuMini} color="#6366f1" />
            </div>
          )}
        </SkeletonStat>
        <SkeletonStat revealed={!!metricsOv}>
          {metricsOv && (
            <div className="flex items-center justify-between">
              <div>
                <div className={`text-2xl font-bold ${pctColor(metricsOv.avg_mem_pct)}`}>{metricsOv.avg_mem_pct}%</div>
                <div className="mt-1 text-xs text-slate-400">Avg Memory</div>
              </div>
              <MiniSparkline data={memMini} color="#10b981" />
            </div>
          )}
        </SkeletonStat>
      </div>

      <h3 className="mb-3 text-sm font-medium text-slate-400 uppercase tracking-wide">Needs attention</h3>
      <div className="mb-6 rounded-lg border border-slate-700 bg-slate-900 p-3">
        {attention.length === 0 ? (
          <div className="flex items-center gap-2 py-1 text-xs text-slate-500">
            <svg viewBox="0 0 16 16" className="h-3.5 w-3.5 text-emerald-500" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M3 8.5L6.5 12L13 4" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            Nothing needs attention right now
          </div>
        ) : (
          <div className="flex flex-col divide-y divide-slate-800/60">
            {attention.map((item) => (
              <div key={item.key} className="flex items-center gap-2.5 py-1.5 text-xs">
                <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${item.dotClass}`} />
                <span className="text-slate-300">{item.text}</span>
                {item.time && <span className="ml-auto shrink-0 text-slate-600">{item.time}</span>}
              </div>
            ))}
          </div>
        )}
      </div>

      <h3 className="mb-3 text-sm font-medium text-slate-400 uppercase tracking-wide">Nodes</h3>
      <div className="mb-6 grid grid-cols-3 gap-3">
        {cluster.nodes.map((node) => (
          <NodeCard key={node.name} node={node} metrics={nodeMetrics} />
        ))}
      </div>

      <h3 className="mb-3 text-sm font-medium text-slate-400 uppercase tracking-wide">Systems</h3>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
        <SystemCard name="5G Core" running={coreRunning} total={allNfs.length} onClick={() => navigate("/core")} />
        <IsolationCard summary={isolation} onClick={() => navigate("/network/isolation")} />
        {(!exposureLoaded || exposure.length > 0) && (
          <SkeletonSystemCard revealed={exposureLoaded}>
            {exposureLoaded && (
              <SystemCard name="Exposure Stack" running={exposureRunning} total={exposure.length} onClick={() => navigate("/services")} />
            )}
          </SkeletonSystemCard>
        )}
        {(!appsLoaded || apps.length > 0) && (
          <SkeletonSystemCard revealed={appsLoaded}>
            {appsLoaded && (
              <SystemCard name="Edge Apps" running={appsRunning} total={apps.length} onClick={() => navigate("/services/apps")} />
            )}
          </SkeletonSystemCard>
        )}
      </div>
    </div>
  );
}

// A whole layer rolled up to one number: how many of its pieces are running.
// The dedicated page for each layer (5G Core, Services) is where the
// individual items live; this card is only the door to it.
function SystemCard({ name, running, total, onClick }) {
  const allUp = total > 0 && running === total;
  const dot = total === 0 ? "bg-slate-500" : allUp ? "bg-emerald-400" : "bg-amber-400 animate-pulse";
  return (
    <button
      type="button"
      onClick={onClick}
      className="group h-full w-full rounded-lg border border-slate-700 bg-slate-900 p-4 text-left transition-colors hover:border-indigo-600/50 hover:bg-slate-800"
    >
      <div className="flex items-center justify-between">
        <span className="text-sm font-semibold text-white">{name}</span>
        <span className={`h-2 w-2 rounded-full ${dot}`} />
      </div>
      <div className={`mt-2 text-xs ${allUp ? "text-emerald-400" : "text-amber-400"}`}>
        {running}/{total} running
      </div>
      <div className="mt-3 flex items-center gap-1 text-[10px] font-medium text-indigo-400 group-hover:text-indigo-300">
        Open <span aria-hidden="true">&#x2192;</span>
      </div>
    </button>
  );
}

// Blocked crossings between the 5G planes over the last 24 h, same door idiom as
// SystemCard. A placeholder while loading; unknown (counters unavailable, reason
// in the tooltip) reads as such, not as "all clear".
function IsolationCard({ summary, onClick }) {
  const known = summary.available;
  const clear = known && summary.blocked === 0;
  const dot = summary.loading || !known ? "bg-slate-500" : clear ? "bg-emerald-400" : "bg-amber-400";
  return (
    <button
      type="button"
      onClick={onClick}
      className="group h-full w-full rounded-lg border border-slate-700 bg-slate-900 p-4 text-left transition-colors hover:border-indigo-600/50 hover:bg-slate-800"
    >
      <div className="flex items-center justify-between">
        <span className="text-sm font-semibold text-white">Isolation</span>
        <span className={`h-2 w-2 rounded-full ${dot}`} />
      </div>
      {summary.loading ? (
        <div className="mt-2.5 h-3 w-32 animate-pulse rounded bg-slate-800" />
      ) : (
        <div className={`mt-2 text-xs ${!known ? "text-slate-500" : clear ? "text-emerald-400" : "text-amber-400"}`}
             title={!known ? summary.reason : undefined}>
          {!known ? "Plane counters unavailable"
            : clear ? `No ${summary.mode === "observe" ? "disallowed" : "blocked"} crossings in 24 h`
            : `${summary.blocked} ${summary.mode === "observe" ? "not allowed (observe mode)" : "blocked"} in 24 h`}
        </div>
      )}
      <div className="mt-3 flex items-center gap-1 text-[10px] font-medium text-indigo-400 group-hover:text-indigo-300">
        Open <span aria-hidden="true">&#x2192;</span>
      </div>
    </button>
  );
}

// Skeleton and content are full card layers stacked on the same box (see
// .t-skel in index.css), so either reads as a complete tile on its own. The
// skeleton keeps breathing until `revealed` flips, whenever the fetch settles.
function SkeletonStat({ revealed, children }) {
  return (
    <div className={`t-skel min-h-[84px] ${revealed ? "is-revealed" : ""}`}>
      <div className="t-skel-skeleton is-pulsing flex items-center justify-between rounded-lg border border-slate-700 bg-slate-900 p-4">
        <div className="flex flex-col gap-2">
          <span className="h-6 w-14 rounded bg-slate-700" />
          <span className="h-3 w-16 rounded bg-slate-700" />
        </div>
        <span className="h-7 w-20 rounded bg-slate-700" />
      </div>
      <div className="t-skel-content h-full rounded-lg border border-slate-700 bg-slate-900 p-4">{children}</div>
    </div>
  );
}

function SkeletonSystemCard({ revealed, children }) {
  return (
    <div className={`t-skel min-h-[104px] ${revealed ? "is-revealed" : ""}`}>
      <div className="t-skel-skeleton is-pulsing flex flex-col gap-3 rounded-lg border border-slate-700 bg-slate-900 p-4">
        <div className="flex items-center justify-between">
          <span className="h-4 w-28 rounded bg-slate-700" />
          <span className="h-2 w-2 rounded-full bg-slate-700" />
        </div>
        <span className="h-3 w-20 rounded bg-slate-700" />
        <span className="h-3 w-14 rounded bg-slate-700" />
      </div>
      <div className="t-skel-content">{children}</div>
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
