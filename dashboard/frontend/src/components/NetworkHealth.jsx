import React, { useEffect, useState, useCallback } from "react";
import { getN6NatDiagnostics, getNetworkHealth, runNetworkHealthCheck } from "../api";
import Loader from "./Loader";

const INTERFACES = [
  { key: "N2", label: "N2 (NGAP)", bridge: "br-n2", protocol: "SCTP/38412", color: "indigo" },
  { key: "N3", label: "N3 (GTP-U)", bridge: "br-n3", protocol: "UDP/2152", color: "emerald" },
  { key: "N4", label: "N4 (PFCP)", bridge: "br-n4", protocol: "UDP/8805", color: "cyan" },
  { key: "N6", label: "N6 (DN)", bridge: "br-n6c", protocol: "IP", color: "amber" },
];

const STATUS_STYLES = {
  ok:      { bg: "bg-emerald-900/30", border: "border-emerald-700/50", dot: "bg-emerald-400", text: "text-emerald-400" },
  fail:    { bg: "bg-rose-900/30", border: "border-rose-700/50", dot: "bg-rose-400", text: "text-rose-400" },
  warn:    { bg: "bg-amber-900/30", border: "border-amber-700/50", dot: "bg-amber-400", text: "text-amber-400" },
  error:   { bg: "bg-rose-900/30", border: "border-rose-700/50", dot: "bg-rose-400", text: "text-rose-400" },
  unknown: { bg: "bg-slate-900", border: "border-slate-700", dot: "bg-slate-500", text: "text-slate-500" },
};

function formatRate(bps) {
  if (!bps || bps < 1) return "0 B/s";
  if (bps > 1_000_000) return `${(bps / 1_000_000).toFixed(1)} MB/s`;
  if (bps > 1_000) return `${(bps / 1_000).toFixed(1)} KB/s`;
  return `${Math.round(bps)} B/s`;
}

function InterfaceCard({ iface, health, traffic }) {
  const st = STATUS_STYLES[health?.status] || STATUS_STYLES.unknown;
  const pps = traffic?.pps ?? 0;
  const bps = traffic?.bps ?? 0;

  return (
    <div className={`rounded-lg border ${st.border} ${st.bg} p-4`}>
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className={`h-2.5 w-2.5 rounded-full ${st.dot} ${health?.status === "unknown" ? "" : "animate-pulse"}`} />
          <span className="text-sm font-semibold text-white">{iface.label}</span>
        </div>
        <span className="text-[10px] font-mono text-slate-500">{iface.bridge}</span>
      </div>

      <div className="text-xs text-slate-400 mb-2">{iface.protocol}</div>

      {health?.detail && (
        <div className={`text-xs ${st.text} mb-2 break-words`}>
          {health.detail}
        </div>
      )}

      {health?.latency_ms != null && (
        <div className="text-xs text-slate-500">
          Latency: <span className="font-mono text-slate-300">{health.latency_ms} ms</span>
        </div>
      )}

      <div className="mt-3 grid grid-cols-2 gap-2 border-t border-slate-700/50 pt-2">
        <div>
          <div className="text-[10px] text-slate-500 uppercase">PPS</div>
          <div className={`text-lg font-bold tabular-nums ${pps > 0 ? "text-cyan-400" : "text-slate-600"}`}>
            {Math.round(pps)}
          </div>
        </div>
        <div>
          <div className="text-[10px] text-slate-500 uppercase">Throughput</div>
          <div className={`text-sm font-bold tabular-nums ${bps > 0 ? "text-indigo-300" : "text-slate-600"}`}>
            {formatRate(bps)}
          </div>
        </div>
      </div>
    </div>
  );
}

function CheckChip({ label, ok }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-1 text-[10px] ${
        ok
          ? "border-emerald-700/50 bg-emerald-900/20 text-emerald-300"
          : "border-rose-700/50 bg-rose-900/20 text-rose-300"
      }`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${ok ? "bg-emerald-400" : "bg-rose-400"}`} />
      {label}
    </span>
  );
}

function N6NatPolicyCard({ data }) {
  if (!data) return null;

  const summary = data.summary || {};
  const checks = data.checks || {};
  const warnings = data.warnings || [];
  const rules = data.rules || [];
  const legacyRules = data.legacy_rules || [];
  const statusStyle = STATUS_STYLES[summary.status] || STATUS_STYLES.unknown;

  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900 p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-medium text-slate-300">N6 Egress NAT Policy</h3>
        <span className={`rounded px-2 py-0.5 text-[10px] uppercase ${statusStyle.bg} ${statusStyle.text} border ${statusStyle.border}`}>
          {summary.status || "unknown"}
        </span>
      </div>

      <div className="mb-3 grid grid-cols-1 gap-2 md:grid-cols-3">
        <div className="rounded border border-slate-700 bg-slate-950/60 p-2">
          <div className="text-[10px] uppercase text-slate-500">IP Forwarding</div>
          <div className={`text-xs font-semibold ${summary.ip_forward_enabled ? "text-emerald-300" : "text-rose-300"}`}>
            {summary.ip_forward_enabled ? "Enabled" : "Disabled"}
          </div>
        </div>
        <div className="rounded border border-slate-700 bg-slate-950/60 p-2">
          <div className="text-[10px] uppercase text-slate-500">Backend</div>
          <div className="text-xs font-semibold text-slate-200">{summary.backend || "unknown"}</div>
        </div>
        <div className="rounded border border-slate-700 bg-slate-950/60 p-2">
          <div className="text-[10px] uppercase text-slate-500">Outbound Interface</div>
          <div className="text-xs font-semibold text-slate-200">{summary.outbound_interface || "-"}</div>
        </div>
      </div>

      <div className="mb-3 flex flex-wrap gap-2">
        <CheckChip label="Forwarding" ok={Boolean(checks.ip_forward_enabled)} />
        <CheckChip label="Private Bypass" ok={Boolean(checks.private_bypass_complete)} />
        <CheckChip label="Masquerade" ok={Boolean(checks.masquerade_present)} />
        <CheckChip label="No Duplicates" ok={!checks.duplicates_present} />
        <CheckChip label="No Legacy Leftovers" ok={!checks.legacy_leftovers_present} />
      </div>

      {warnings.length > 0 && (
        <div className="mb-3 rounded border border-amber-700/40 bg-amber-950/20 p-2 text-xs text-amber-300">
          {warnings.map((w) => (
            <div key={w}>- {w}</div>
          ))}
        </div>
      )}

      <div className="space-y-1.5">
        <div className="text-[11px] font-medium text-slate-400">Active backend rules (10.207.0.0/24)</div>
        {rules.length === 0 ? (
          <div className="text-xs text-slate-500">No N6 rules detected in active backend.</div>
        ) : (
          rules.map((rule, idx) => {
            const label =
              rule.type === "private_bypass"
                ? "Private bypass"
                : rule.type === "public_masquerade"
                  ? "Public masquerade"
                  : "Other";
            return (
              <div key={`${rule.raw}-${idx}`} className="rounded border border-slate-800 bg-slate-950/50 p-2">
                <div className="mb-1 flex items-center justify-between gap-2">
                  <span className="text-[11px] font-medium text-slate-300">{idx + 1}. {label}</span>
                  {rule.duplicate && (
                    <span className="rounded border border-rose-700/50 bg-rose-900/20 px-1.5 py-0.5 text-[10px] text-rose-300">
                      duplicate
                    </span>
                  )}
                </div>
                <div className="font-mono text-[11px] text-slate-400 break-all">{rule.raw}</div>
              </div>
            );
          })
        )}
      </div>

      {legacyRules.length > 0 && (
        <div className="mt-3 rounded border border-amber-700/40 bg-amber-950/10 p-2">
          <div className="mb-1 text-[11px] font-medium text-amber-300">Legacy backend leftovers</div>
          {legacyRules.map((rule, idx) => (
            <div key={`${rule}-${idx}`} className="font-mono text-[11px] text-amber-200/80 break-all">
              {rule}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

const PATH_NODES = [
  { id: "ue", label: "UE", x: 0 },
  { id: "gnb", label: "gNB", x: 1 },
  { id: "amf", label: "AMF", x: 2 },
  { id: "smf", label: "SMF", x: 3 },
  { id: "upf", label: "UPF", x: 4 },
  { id: "dn", label: "DN", x: 5 },
];

const PATH_LINKS = [
  { from: "ue", to: "gnb", label: "Uu", iface: null },
  { from: "gnb", to: "amf", label: "N2", iface: "N2" },
  { from: "gnb", to: "upf", label: "N3", iface: "N3", curved: true },
  { from: "amf", to: "smf", label: "N11", iface: null },
  { from: "smf", to: "upf", label: "N4", iface: "N4" },
  { from: "upf", to: "dn", label: "N6", iface: "N6" },
];

function DataPathDiagram({ healthMap, trafficData }) {
  const nodeW = 64;
  const nodeH = 36;
  const gap = 100;
  const svgW = PATH_NODES.length * (nodeW + gap) - gap + 40;
  const svgH = 120;

  function nodeX(idx) { return 20 + idx * (nodeW + gap); }
  const nodeY = svgH / 2 - nodeH / 2;

  return (
    <svg viewBox={`0 0 ${svgW} ${svgH}`} className="w-full" style={{ maxHeight: 120 }}>
      {PATH_LINKS.map((link) => {
        const fromNode = PATH_NODES.find((n) => n.id === link.from);
        const toNode = PATH_NODES.find((n) => n.id === link.to);
        const x1 = nodeX(fromNode.x) + nodeW;
        const x2 = nodeX(toNode.x);
        const y = svgH / 2;

        const health = link.iface ? healthMap[link.iface] : null;
        const traffic = link.iface ? trafficData[link.iface] : null;
        const isActive = traffic?.pps > 0;
        const isOk = health?.status === "ok";
        const isFail = health?.status === "fail" || health?.status === "error";

        let stroke = "#334155";
        if (isFail) stroke = "#f87171";
        else if (isOk && isActive) stroke = "#34d399";
        else if (isOk) stroke = "#6366f1";

        const curved = link.curved;
        const cy = curved ? y + 30 : y;
        const d = curved
          ? `M ${x1} ${y} Q ${(x1 + x2) / 2} ${cy + 15} ${x2} ${y}`
          : `M ${x1} ${y} L ${x2} ${y}`;

        return (
          <g key={`${link.from}-${link.to}`}>
            <path
              d={d}
              fill="none"
              stroke={stroke}
              strokeWidth={isActive ? 2.5 : 1.5}
              strokeDasharray={isActive ? undefined : "6 4"}
            >
              {isActive && (
                <animate
                  attributeName="stroke-dashoffset"
                  from="20"
                  to="0"
                  dur="1s"
                  repeatCount="indefinite"
                />
              )}
            </path>
            <text
              x={(x1 + x2) / 2}
              y={curved ? cy + 8 : y - 8}
              textAnchor="middle"
              fill={isActive ? "#a5b4fc" : "#64748b"}
              fontSize="10"
              fontWeight={isActive ? "600" : "400"}
            >
              {link.label}
              {isActive && ` (${Math.round(traffic.pps)} pps)`}
            </text>
          </g>
        );
      })}

      {PATH_NODES.map((node) => {
        const x = nodeX(node.x);
        return (
          <g key={node.id}>
            <rect
              x={x}
              y={nodeY}
              width={nodeW}
              height={nodeH}
              rx={6}
              fill="#1e293b"
              stroke="#475569"
              strokeWidth={1}
            />
            <text
              x={x + nodeW / 2}
              y={nodeY + nodeH / 2 + 4}
              textAnchor="middle"
              fill="#e2e8f0"
              fontSize="11"
              fontWeight="600"
            >
              {node.label}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

export default function NetworkHealth({ trafficData }) {
  const [health, setHealth] = useState([]);
  const [n6Nat, setN6Nat] = useState(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState(null);
  const [lastRun, setLastRun] = useState(null);

  const fetchHealth = useCallback(async () => {
    try {
      const [data, natData] = await Promise.all([getNetworkHealth(), getN6NatDiagnostics()]);
      setHealth(data || []);
      setN6Nat(natData || null);
      setError(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchHealth();
    const id = setInterval(fetchHealth, 30000);
    return () => clearInterval(id);
  }, [fetchHealth]);

  async function handleRun() {
    setRunning(true);
    setError(null);
    try {
      const [data, natData] = await Promise.all([runNetworkHealthCheck(), getN6NatDiagnostics()]);
      setHealth(data || []);
      setN6Nat(natData || null);
      setLastRun(new Date().toLocaleTimeString());
    } catch (err) {
      setError(err.message);
    } finally {
      setRunning(false);
    }
  }

  const healthMap = {};
  for (const h of health) {
    healthMap[h.interface] = h;
  }

  const traffic = trafficData || {};

  if (loading) {
    return (
      <div className="flex min-h-[200px] items-center justify-center p-6">
        <Loader size="lg" label="Loading network health data…" />
      </div>
    );
  }

  return (
    <div className="space-y-5 overflow-y-auto h-full p-1">
      {error && (
        <div className="rounded border border-rose-700/40 bg-rose-950/30 p-3 text-sm text-rose-300">
          {error}
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {INTERFACES.map((iface) => (
          <InterfaceCard
            key={iface.key}
            iface={iface}
            health={healthMap[iface.key]}
            traffic={traffic[iface.key]}
          />
        ))}
      </div>

      <N6NatPolicyCard data={n6Nat} />

      <div className="rounded-lg border border-slate-700 bg-slate-900 p-4">
        <h3 className="text-sm font-medium text-slate-300 mb-3">PDU Session Data Path</h3>
        <DataPathDiagram healthMap={healthMap} trafficData={traffic} />
      </div>

      <div className="rounded-lg border border-slate-700 bg-slate-900 p-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-medium text-slate-300">Connectivity Tests</h3>
          <div className="flex items-center gap-3">
            {lastRun && (
              <span className="text-[10px] text-slate-500">Last run: {lastRun}</span>
            )}
            <button
              onClick={handleRun}
              disabled={running}
              className="rounded bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
            >
              {running ? "Testing..." : "Run Health Check"}
            </button>
          </div>
        </div>

        {health.length > 0 && (
          <div className="space-y-1.5">
            {health.map((h) => {
              const st = STATUS_STYLES[h.status] || STATUS_STYLES.unknown;
              return (
                <div
                  key={h.interface}
                  className="flex items-center gap-3 rounded px-3 py-2 bg-slate-950/50 text-xs"
                >
                  <span className={`h-2 w-2 rounded-full ${st.dot}`} />
                  <span className="w-10 font-semibold text-slate-200">{h.interface}</span>
                  <span className="text-slate-500 w-16 font-mono">{h.bridge}</span>
                  <span className={`flex-1 ${st.text}`}>{h.detail}</span>
                  {h.latency_ms != null && (
                    <span className="text-slate-400 font-mono">{h.latency_ms} ms</span>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {health.length === 0 && !running && (
          <p className="text-xs text-slate-500">
            No health data yet. Click "Run Health Check" to test N-interface connectivity.
          </p>
        )}
      </div>
    </div>
  );
}
