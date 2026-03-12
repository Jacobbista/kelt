import React, { useEffect, useState } from "react";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { getNfMetrics, getNodeMetrics, getNodeMetricsRange } from "../api";
import Loader from "../components/Loader";

function pctColor(v) {
  if (v > 80) return "text-rose-400";
  if (v > 60) return "text-amber-400";
  return "text-emerald-400";
}

function GaugeCard({ label, value, unit = "%" }) {
  const pct = Math.min(Math.max(value, 0), 100);
  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900 p-3">
      <div className="text-xs text-slate-400 mb-2">{label}</div>
      <div className={`text-2xl font-bold tabular-nums ${pctColor(pct)}`}>
        {pct.toFixed(1)}{unit}
      </div>
      <div className="mt-2 h-1.5 rounded-full bg-slate-800">
        <div
          className={`h-full rounded-full transition-all ${pct > 80 ? "bg-rose-500" : pct > 60 ? "bg-amber-500" : "bg-emerald-500"}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

function formatTs(ts) {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function rangeToSeries(rangeData) {
  if (!rangeData?.result) return [];
  const map = {};
  for (const series of rangeData.result) {
    const label = series.metric?.instance || series.metric?.pod || "unknown";
    for (const [ts, val] of series.values || []) {
      if (!map[ts]) map[ts] = { ts, time: formatTs(ts) };
      map[ts][label] = parseFloat(parseFloat(val).toFixed(1));
    }
  }
  return Object.values(map).sort((a, b) => a.ts - b.ts);
}

function nfMetricToBar(items) {
  return items
    .map((m) => {
      const name = m.label.replace(/-[a-z0-9]{8,10}-[a-z0-9]{5}$/, "");
      return { name, value: m.value };
    })
    .sort((a, b) => b.value - a.value);
}

const COLORS = ["#6366f1", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#06b6d4"];

export default function MetricsPage() {
  const [nodeMetrics, setNodeMetrics] = useState(null);
  const [nfMetrics, setNfMetrics] = useState(null);
  const [cpuHistory, setCpuHistory] = useState([]);
  const [memHistory, setMemHistory] = useState([]);
  const [error, setError] = useState("");

  useEffect(() => {
    async function load() {
      try {
        setError("");
        const [nm, nfm, range] = await Promise.all([
          getNodeMetrics(),
          getNfMetrics(),
          getNodeMetricsRange(30),
        ]);
        setNodeMetrics(nm);
        setNfMetrics(nfm);
        setCpuHistory(rangeToSeries(range.cpu));
        setMemHistory(rangeToSeries(range.memory));
      } catch (err) {
        setError(String(err.message || err));
      }
    }
    load();
    const id = setInterval(load, 15000);
    return () => clearInterval(id);
  }, []);

  if (error) {
    return <div className="rounded border border-rose-700 bg-rose-950/50 p-3 text-sm text-rose-300">{error}</div>;
  }
  if (!nodeMetrics) {
    return (
      <div className="flex min-h-[200px] items-center justify-center">
        <Loader size="lg" label="Loading metrics…" />
      </div>
    );
  }

  const cpuSeries = Object.keys(cpuHistory[0] || {}).filter((k) => k !== "ts" && k !== "time");
  const memSeries = Object.keys(memHistory[0] || {}).filter((k) => k !== "ts" && k !== "time");

  return (
    <div className="space-y-6">
      <h2 className="text-lg font-semibold">Metrics</h2>

      {/* Node gauges */}
      <div>
        <h3 className="mb-3 text-sm font-medium text-slate-400 uppercase tracking-wide">Node Resources</h3>
        <div className="grid grid-cols-3 gap-3 lg:grid-cols-6">
          {nodeMetrics.cpu.map((c) => (
            <GaugeCard key={`cpu-${c.label}`} label={`CPU ${c.label.split(":")[0]}`} value={c.value} />
          ))}
          {nodeMetrics.memory.map((m) => (
            <GaugeCard key={`mem-${m.label}`} label={`Mem ${m.label.split(":")[0]}`} value={m.value} />
          ))}
        </div>
      </div>

      {/* CPU history chart */}
      {cpuHistory.length > 0 && (
        <div>
          <h3 className="mb-3 text-sm font-medium text-slate-400 uppercase tracking-wide">CPU % (30 min)</h3>
          <div className="rounded-lg border border-slate-700 bg-slate-900 p-3" style={{ height: 220 }}>
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={cpuHistory}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                <XAxis dataKey="time" tick={{ fill: "#64748b", fontSize: 10 }} />
                <YAxis domain={[0, 100]} tick={{ fill: "#64748b", fontSize: 10 }} width={35} />
                <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #334155", fontSize: 11 }} />
                {cpuSeries.map((key, i) => (
                  <Area key={key} type="monotone" dataKey={key} stroke={COLORS[i % COLORS.length]} fill={COLORS[i % COLORS.length]} fillOpacity={0.15} strokeWidth={1.5} />
                ))}
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {/* Memory history chart */}
      {memHistory.length > 0 && (
        <div>
          <h3 className="mb-3 text-sm font-medium text-slate-400 uppercase tracking-wide">Memory % (30 min)</h3>
          <div className="rounded-lg border border-slate-700 bg-slate-900 p-3" style={{ height: 220 }}>
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={memHistory}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                <XAxis dataKey="time" tick={{ fill: "#64748b", fontSize: 10 }} />
                <YAxis domain={[0, 100]} tick={{ fill: "#64748b", fontSize: 10 }} width={35} />
                <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #334155", fontSize: 11 }} />
                {memSeries.map((key, i) => (
                  <Area key={key} type="monotone" dataKey={key} stroke={COLORS[i % COLORS.length]} fill={COLORS[i % COLORS.length]} fillOpacity={0.15} strokeWidth={1.5} />
                ))}
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {/* NF resource bars */}
      {nfMetrics && (
        <div className="grid grid-cols-2 gap-4">
          <div>
            <h3 className="mb-3 text-sm font-medium text-slate-400 uppercase tracking-wide">NF CPU (millicores)</h3>
            <div className="rounded-lg border border-slate-700 bg-slate-900 p-3" style={{ height: 300 }}>
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={nfMetricToBar(nfMetrics.cpu)} layout="vertical">
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                  <XAxis type="number" tick={{ fill: "#64748b", fontSize: 10 }} />
                  <YAxis dataKey="name" type="category" tick={{ fill: "#94a3b8", fontSize: 10 }} width={80} />
                  <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #334155", fontSize: 11 }} />
                  <Bar dataKey="value" fill="#6366f1" radius={[0, 4, 4, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
          <div>
            <h3 className="mb-3 text-sm font-medium text-slate-400 uppercase tracking-wide">NF Memory (MB)</h3>
            <div className="rounded-lg border border-slate-700 bg-slate-900 p-3" style={{ height: 300 }}>
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={nfMetricToBar(nfMetrics.memory)} layout="vertical">
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                  <XAxis type="number" tick={{ fill: "#64748b", fontSize: 10 }} />
                  <YAxis dataKey="name" type="category" tick={{ fill: "#94a3b8", fontSize: 10 }} width={80} />
                  <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #334155", fontSize: 11 }} />
                  <Bar dataKey="value" fill="#10b981" radius={[0, 4, 4, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
