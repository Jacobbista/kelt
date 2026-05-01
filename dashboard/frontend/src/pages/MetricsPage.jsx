import React, { useEffect, useMemo, useState } from "react";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, LabelList,
  Legend, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { getNfMetrics, getNfMetricsRange, getNodeMetrics, getNodeMetricsRange } from "../api";
import Loader from "../components/Loader";

const COLORS = ["#6366f1", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#06b6d4"];

const TABS = [
  { id: "nodes", label: "Nodes" },
  { id: "nfs",   label: "NFs"   },
];

const RANGES = [
  { label: "15m", mins: 15 },
  { label: "30m", mins: 30 },
  { label: "1h",  mins: 60 },
  { label: "6h",  mins: 360 },
  { label: "24h", mins: 1440 },
];

// ─── Helpers ─────────────────────────────────────────────────────────────────

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

function formatTs(ts) {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function cleanPodName(name) {
  return name.replace(/-[a-z0-9]{8,10}-[a-z0-9]{5}$/, "");
}

function rangeToSeries(rangeData, cleanLabel = false) {
  if (!rangeData?.result) return [];
  const map = {};
  for (const series of rangeData.result) {
    const raw = series.metric?.instance || series.metric?.pod || "unknown";
    const label = cleanLabel ? cleanPodName(raw) : raw;
    for (const [ts, val] of series.values || []) {
      if (!map[ts]) map[ts] = { ts, time: formatTs(ts) };
      map[ts][label] = parseFloat(parseFloat(val).toFixed(2));
    }
  }
  return Object.values(map).sort((a, b) => a.ts - b.ts);
}

function nfMetricToBar(items) {
  return items
    .map((m) => ({ name: cleanPodName(m.label), value: m.value }))
    .sort((a, b) => b.value - a.value);
}

function buildNodeCards(nodeMetrics) {
  const byNode = {};
  const add = (arr, key) => {
    for (const m of arr || []) {
      const name = m.label.split(":")[0];
      if (!byNode[name]) byNode[name] = { name };
      byNode[name][key] = m.value;
    }
  };
  add(nodeMetrics.cpu, "cpu");
  add(nodeMetrics.memory, "memory");
  add(nodeMetrics.disk, "disk");
  return Object.values(byNode);
}

function seriesKeys(data) {
  return Object.keys(data[0] || {}).filter((k) => k !== "ts" && k !== "time");
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function TabBar({ tab, onTab }) {
  return (
    <div className="flex rounded-lg border border-slate-700 bg-slate-900 p-0.5">
      {TABS.map((t) => (
        <button
          key={t.id}
          type="button"
          onClick={() => onTab(t.id)}
          className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
            tab === t.id
              ? "bg-indigo-600/30 text-indigo-300"
              : "text-slate-400 hover:text-white hover:bg-slate-800"
          }`}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

function RangeSelector({ value, onChange }) {
  return (
    <div className="flex rounded-lg border border-slate-700 bg-slate-900 p-0.5">
      {RANGES.map((r) => (
        <button
          key={r.mins}
          type="button"
          onClick={() => onChange(r.mins)}
          className={`rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${
            value === r.mins
              ? "bg-indigo-600/30 text-indigo-300"
              : "text-slate-400 hover:text-white hover:bg-slate-800"
          }`}
        >
          {r.label}
        </button>
      ))}
    </div>
  );
}

function NodeCard({ node }) {
  const rows = [
    { label: "CPU",  value: node.cpu },
    { label: "Mem",  value: node.memory },
    { label: "Disk", value: node.disk },
  ].filter((r) => r.value != null);

  return (
    <div className="flex-1 min-w-[160px] max-w-xs rounded-lg border border-slate-700 bg-slate-900 p-3">
      <div className="text-[10px] font-mono text-slate-500 mb-3 truncate">{node.name}</div>
      {rows.map(({ label, value }) => (
        <div key={label} className="mb-2 last:mb-0">
          <div className="flex justify-between items-baseline mb-1">
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
  );
}

const CHART_STYLE = { background: "#0f172a", border: "1px solid #334155", fontSize: 11 };

function HistoryChart({ title, data, series, yUnit = "%" }) {
  return (
    <div>
      <h3 className="mb-2 text-sm font-medium text-slate-400 uppercase tracking-wide">{title}</h3>
      <div className="rounded-lg border border-slate-700 bg-slate-900 p-3" style={{ height: 230 }}>
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
            <XAxis dataKey="time" tick={{ fill: "#64748b", fontSize: 10 }} />
            <YAxis
              domain={yUnit === "%" ? [0, 100] : ["auto", "auto"]}
              tick={{ fill: "#64748b", fontSize: 10 }}
              width={40}
              unit={yUnit}
            />
            <Tooltip contentStyle={CHART_STYLE} />
            <Legend wrapperStyle={{ fontSize: 10, paddingTop: 4 }} />
            {series.map((key, i) => (
              <Area
                key={key}
                type="monotone"
                dataKey={key}
                stroke={COLORS[i % COLORS.length]}
                fill={COLORS[i % COLORS.length]}
                fillOpacity={0.08}
                strokeWidth={1.5}
                dot={false}
              />
            ))}
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function HBarChart({ title, data, color, unit = "" }) {
  const barHeight = 22;
  const chartHeight = Math.max(180, data.length * (barHeight + 6) + 40);
  return (
    <div>
      <h3 className="mb-2 text-sm font-medium text-slate-400 uppercase tracking-wide">{title}</h3>
      <div className="rounded-lg border border-slate-700 bg-slate-900 p-3">
        {data.length === 0 ? (
          <div className="flex items-center justify-center text-xs text-slate-600" style={{ height: 180 }}>
            No data — Kubernetes Metrics API unavailable
          </div>
        ) : (
          <ResponsiveContainer width="100%" height={chartHeight}>
            <BarChart data={data} layout="vertical" margin={{ top: 0, right: 60, bottom: 0, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" horizontal={false} />
              <XAxis type="number" tick={{ fill: "#64748b", fontSize: 10 }} axisLine={false} tickLine={false} />
              <YAxis dataKey="name" type="category" tick={{ fill: "#94a3b8", fontSize: 10 }} width={90} />
              <Tooltip contentStyle={CHART_STYLE} formatter={(v) => [`${v}${unit}`, "value"]} />
              <Bar dataKey="value" radius={[0, 3, 3, 0]} barSize={barHeight}>
                {data.map((_, i) => <Cell key={i} fill={color} fillOpacity={0.75 + 0.25 * (1 - i / data.length)} />)}
                <LabelList
                  dataKey="value"
                  position="right"
                  style={{ fill: "#94a3b8", fontSize: 10 }}
                  formatter={(v) => `${v}${unit}`}
                />
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function MetricsPage() {
  const [tab, setTab]           = useState("nodes");
  const [rangeMins, setRangeMins] = useState(30);
  const [nodeMetrics, setNodeMetrics] = useState(null);
  const [nfMetrics, setNfMetrics]     = useState(null);
  const [nodeRange, setNodeRange]     = useState(null);
  const [nfRange, setNfRange]         = useState(null);
  const [updatedAt, setUpdatedAt]     = useState(null);
  const [error, setError]             = useState("");

  // ~120 data points regardless of window size
  const step = useMemo(() => {
    const s = Math.max(60, Math.round((rangeMins * 60) / 120));
    return `${s}s`;
  }, [rangeMins]);

  useEffect(() => {
    async function load() {
      try {
        setError("");
        const [nm, nfm, nr, nfr] = await Promise.all([
          getNodeMetrics(),
          getNfMetrics(),
          getNodeMetricsRange(rangeMins, step),
          getNfMetricsRange(rangeMins, step),
        ]);
        setNodeMetrics(nm);
        setNfMetrics(nfm);
        setNodeRange(nr);
        setNfRange(nfr);
        setUpdatedAt(new Date());
      } catch (err) {
        setError(String(err.message || err));
      }
    }
    load();
    const id = setInterval(load, 15_000);
    return () => clearInterval(id);
  }, [rangeMins, step]);

  if (!nodeMetrics && !error) {
    return (
      <div className="flex min-h-[200px] items-center justify-center">
        <Loader size="lg" label="Loading metrics…" />
      </div>
    );
  }

  const rangeLabel = RANGES.find((r) => r.mins === rangeMins)?.label ?? `${rangeMins}m`;

  const cpuHistory    = nodeRange ? rangeToSeries(nodeRange.cpu) : [];
  const memHistory    = nodeRange ? rangeToSeries(nodeRange.memory) : [];
  const nfCpuHistory  = nfRange   ? rangeToSeries(nfRange.cpu, true) : [];

  const cpuSeries   = seriesKeys(cpuHistory);
  const memSeries   = seriesKeys(memHistory);
  const nfCpuSeries = seriesKeys(nfCpuHistory);

  const nodeCards = nodeMetrics ? buildNodeCards(nodeMetrics) : [];

  return (
    <div className="flex h-full flex-col">

      {/* ── Header ── */}
      <div className="mb-4 flex flex-wrap items-center gap-3 flex-shrink-0">
        <h2 className="text-lg font-semibold">Metrics</h2>
        <TabBar tab={tab} onTab={setTab} />
        <RangeSelector value={rangeMins} onChange={setRangeMins} />
        {updatedAt && (
          <span className="ml-auto text-[10px] tabular-nums text-slate-600">
            updated {updatedAt.toLocaleTimeString()}
          </span>
        )}
      </div>

      {error && (
        <div className="mb-4 rounded border border-rose-700 bg-rose-950/50 p-3 text-sm text-rose-300">
          {error}
        </div>
      )}

      {/* ── Content ── */}
      <div className="flex-1 min-h-0 overflow-y-auto space-y-6 pr-1">

        {/* ══ Nodes tab ══ */}
        {tab === "nodes" && (
          <>
            {nodeCards.length > 0 && (
              <div>
                <h3 className="mb-3 text-sm font-medium text-slate-400 uppercase tracking-wide">
                  Node Hardware
                </h3>
                <div className="flex flex-wrap gap-3">
                  {nodeCards.map((node) => (
                    <NodeCard key={node.name} node={node} />
                  ))}
                </div>
              </div>
            )}

            {cpuHistory.length > 0 && (
              <HistoryChart
                title={`CPU % — ${rangeLabel}`}
                data={cpuHistory}
                series={cpuSeries}
              />
            )}

            {memHistory.length > 0 && (
              <HistoryChart
                title={`Memory % — ${rangeLabel}`}
                data={memHistory}
                series={memSeries}
              />
            )}
          </>
        )}

        {/* ══ NFs tab ══ */}
        {tab === "nfs" && nfMetrics && (
          <>
            <div className="grid grid-cols-2 gap-4">
              <HBarChart
                title="NF CPU (millicores)"
                data={nfMetricToBar(nfMetrics.cpu)}
                color="#6366f1"
              />
              <HBarChart
                title="NF Memory (MB)"
                data={nfMetricToBar(nfMetrics.memory)}
                color="#10b981"
              />
            </div>

            {nfCpuHistory.length > 0 && (
              <HistoryChart
                title={`NF CPU trend — ${rangeLabel}`}
                data={nfCpuHistory}
                series={nfCpuSeries}
                yUnit="m"
              />
            )}
          </>
        )}
      </div>
    </div>
  );
}
