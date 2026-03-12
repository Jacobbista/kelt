import React, { useCallback, useMemo, useState } from "react";
import { Background, Controls, ReactFlow } from "@xyflow/react";

const STATUS_DOT = {
  Running: "bg-emerald-400",
  Pending: "bg-amber-400 animate-pulse",
  Terminating: "bg-slate-500 animate-pulse",
  Failed: "bg-rose-400",
};

const PALETTE = {
  cp:     { bg: "#1e1b4b", border: "#6366f1", color: "#a5b4fc" },
  amf:    { bg: "#1e1b4b", border: "#818cf8", color: "#c7d2fe" },
  smf:    { bg: "#1e1b4b", border: "#818cf8", color: "#c7d2fe" },
  upf:    { bg: "#022c22", border: "#10b981", color: "#6ee7b7" },
  ran:    { bg: "#0c1a2e", border: "#0ea5e9", color: "#7dd3fc" },
  dn:     { bg: "#1c1917", border: "#a16207", color: "#fbbf24" },
  mongo:  { bg: "#1c1917", border: "#a16207", color: "#fbbf24" },
  zone:   { bg: "#0f172a", border: "#334155", color: "#94a3b8" },
};

function nodeStyle(p) {
  return {
    background: p.bg,
    borderColor: p.border,
    color: p.color,
    borderWidth: 1.5,
    borderRadius: 8,
    fontSize: 12,
    padding: "8px 14px",
    minWidth: 70,
    textAlign: "center",
  };
}

function edgeStyle(label, pps) {
  const isData = label?.startsWith("N3") || label?.startsWith("N6");
  const hasTraffic = pps > 0;
  const baseWidth = isData ? 2 : 1.5;
  const width = hasTraffic ? Math.min(baseWidth + Math.log10(pps + 1), 5) : baseWidth;
  return {
    stroke: hasTraffic ? "#34d399" : (isData ? "#10b981" : "#6366f1"),
    strokeWidth: width,
    strokeDasharray: hasTraffic ? undefined : (isData ? undefined : "6 3"),
  };
}

function buildNodes(nfStatus, nads) {
  const nfMap = {};
  for (const cat of ["control_plane", "user_plane", "data", "other"]) {
    for (const nf of nfStatus[cat] || []) {
      nfMap[nf.nf_type] = nfMap[nf.nf_type] || [];
      nfMap[nf.nf_type].push(nf);
    }
  }

  const nf = (type) => nfMap[type]?.[0];
  const phase = (type) => nf(type)?.phase || "absent";
  const dot = (type) => STATUS_DOT[phase(type)] || "bg-slate-600";

  const cpNfs = ["nrf", "ausf", "udm", "udr", "pcf", "nssf", "bsf"];
  const cpX = 200;
  const cpY = 30;

  const nodes = [];

  cpNfs.forEach((t, i) => {
    const spacing = 110;
    const x = cpX + i * spacing;
    nodes.push({
      id: `nf-${t}`,
      position: { x, y: cpY },
      data: { label: t.toUpperCase(), nf: nf(t), phase: phase(t), dot: dot(t) },
      style: nodeStyle(PALETTE.cp),
      type: "default",
    });
  });

  nodes.push({
    id: "nf-amf",
    position: { x: 320, y: 160 },
    data: { label: "AMF", nf: nf("amf"), phase: phase("amf"), dot: dot("amf") },
    style: nodeStyle(PALETTE.amf),
  });
  nodes.push({
    id: "nf-smf",
    position: { x: 560, y: 160 },
    data: { label: "SMF", nf: nf("smf"), phase: phase("smf"), dot: dot("smf") },
    style: nodeStyle(PALETTE.smf),
  });

  const upfCloud = (nfMap["upf"] || []).find((u) => u.name.includes("cloud"));
  const upfEdge = (nfMap["upf"] || []).find((u) => u.name.includes("edge"));
  const upfAny = nf("upf");

  nodes.push({
    id: "nf-upf-cloud",
    position: { x: 560, y: 300 },
    data: {
      label: upfCloud ? "UPF-Cloud" : "UPF",
      nf: upfCloud || upfAny,
      phase: (upfCloud || upfAny)?.phase || "absent",
      dot: STATUS_DOT[(upfCloud || upfAny)?.phase] || "bg-slate-600",
    },
    style: nodeStyle(PALETTE.upf),
  });

  if (upfEdge) {
    nodes.push({
      id: "nf-upf-edge",
      position: { x: 560, y: 420 },
      data: { label: "UPF-Edge", nf: upfEdge, phase: upfEdge.phase, dot: STATUS_DOT[upfEdge.phase] || "bg-slate-600" },
      style: nodeStyle(PALETTE.upf),
    });
  }

  nodes.push({
    id: "zone-ran",
    position: { x: 80, y: 280 },
    data: { label: "RAN Zone" },
    style: { ...nodeStyle(PALETTE.zone), minWidth: 130, borderStyle: "dashed" },
  });

  nodes.push({
    id: "zone-dn",
    position: { x: 800, y: 300 },
    data: { label: "Data Network" },
    style: { ...nodeStyle(PALETTE.dn), minWidth: 120, borderStyle: "dashed" },
  });

  nodes.push({
    id: "nf-mongodb",
    position: { x: 800, y: 160 },
    data: { label: "MongoDB", nf: nf("mongodb"), phase: phase("mongodb"), dot: dot("mongodb") },
    style: nodeStyle(PALETTE.mongo),
  });

  return nodes;
}

function ppsLabel(pps) {
  if (!pps || pps < 1) return "";
  if (pps > 1000) return ` ${(pps / 1000).toFixed(1)}k pps`;
  return ` ${Math.round(pps)} pps`;
}

function buildEdges(nads, trafficData) {
  const nadMap = {};
  for (const nad of nads) {
    nadMap[nad.name] = nad;
  }

  const subnet = (name) => {
    const ipam = nadMap[name]?.ipam || {};
    return ipam.subnet || ipam.range || "";
  };

  const t = trafficData || {};
  const pps = (iface) => t[iface]?.pps || 0;

  const baseEdges = [
    { id: "e-amf-smf", source: "nf-amf", target: "nf-smf", label: "N11 (SBI)", style: edgeStyle("N11", 0) },
    { id: "e-smf-upf", source: "nf-smf", target: "nf-upf-cloud", label: `N4 ${subnet("n4-net")}${ppsLabel(pps("N4"))}`, animated: pps("N4") > 0, style: edgeStyle("N4", pps("N4")), labelStyle: { fill: pps("N4") > 0 ? "#a5b4fc" : "#94a3b8", fontSize: 9 }, labelBgStyle: { fill: "#0f172a", fillOpacity: 0.85 } },
    { id: "e-ran-amf", source: "zone-ran", target: "nf-amf", label: `N1/N2 ${subnet("n2-net")}${ppsLabel(pps("N2"))}`, animated: pps("N2") > 0, style: edgeStyle("N2", pps("N2")), labelStyle: { fill: pps("N2") > 0 ? "#a5b4fc" : "#94a3b8", fontSize: 9 }, labelBgStyle: { fill: "#0f172a", fillOpacity: 0.85 } },
    { id: "e-ran-upf", source: "zone-ran", target: "nf-upf-cloud", label: `N3 ${subnet("n3-net")}${ppsLabel(pps("N3"))}`, animated: pps("N3") > 0, style: edgeStyle("N3", pps("N3")), labelStyle: { fill: pps("N3") > 0 ? "#a5b4fc" : "#94a3b8", fontSize: 9 }, labelBgStyle: { fill: "#0f172a", fillOpacity: 0.85 } },
    { id: "e-upf-dn", source: "nf-upf-cloud", target: "zone-dn", label: `N6 ${subnet("n6c-net")}${ppsLabel(pps("N6"))}`, animated: pps("N6") > 0, style: edgeStyle("N6", pps("N6")), labelStyle: { fill: pps("N6") > 0 ? "#a5b4fc" : "#94a3b8", fontSize: 9 }, labelBgStyle: { fill: "#0f172a", fillOpacity: 0.85 } },
  ];

  const sbiEdges = ["nrf", "ausf", "udm", "udr", "pcf", "nssf", "bsf"].map((t) => ({
    id: `e-sbi-${t}`,
    source: `nf-${t}`,
    target: t === "nrf" || t === "ausf" || t === "udm" ? "nf-amf" : "nf-smf",
    style: { stroke: "#4f46e5", strokeWidth: 1, strokeDasharray: "4 4" },
    label: "SBI",
    labelStyle: { fill: "#6366f1", fontSize: 8 },
    labelBgStyle: { fill: "#0f172a", fillOpacity: 0.85 },
  }));

  return [...baseEdges, ...sbiEdges];
}

export default function TopologyLogical({ nfStatus, nads, interfaces, onSelectNf, trafficData }) {
  const [selected, setSelected] = useState(null);

  const nodes = useMemo(() => buildNodes(nfStatus, nads), [nfStatus, nads]);
  const edges = useMemo(() => buildEdges(nads, trafficData), [nads, trafficData]);

  const onNodeClick = useCallback((_, node) => {
    setSelected(node.data);
    if (node.data.nf) onSelectNf?.(node.data);
  }, [onSelectNf]);

  return (
    <div className="flex h-full gap-3">
      <div className="flex-1 rounded border border-slate-700" style={{ minHeight: 400 }}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          fitView
          fitViewOptions={{ padding: 0.3 }}
          onNodeClick={onNodeClick}
          proOptions={{ hideAttribution: true }}
          style={{ background: "#0f172a" }}
          nodesDraggable={false}
        >
          <Controls className="[&>button]:bg-slate-800 [&>button]:border-slate-700 [&>button]:text-slate-300" />
          <Background color="#1e293b" gap={24} />
        </ReactFlow>
      </div>

      <div className="w-72 flex-shrink-0 overflow-y-auto rounded border border-slate-700 bg-slate-900 p-3 text-sm">
        <div className="mb-3 font-semibold">Details</div>

        {!selected && <div className="text-slate-400 text-xs">Click a network function to inspect.</div>}

        {selected?.nf && (
          <div className="space-y-2">
            <div className="flex items-center gap-2">
              <span className={`h-2.5 w-2.5 rounded-full ${selected.dot}`} />
              <span className="font-semibold text-white">{selected.label}</span>
              <span className="text-xs text-slate-400">{selected.phase}</span>
            </div>
            <DetailRow label="Pod" value={selected.nf.name} mono />
            <DetailRow label="IP" value={selected.nf.pod_ip} mono />
            <DetailRow label="Node" value={selected.nf.node} />
            <DetailRow label="Restarts" value={selected.nf.restarts} />
            <DetailRow label="Deployment" value={selected.nf.deployment} mono />

            {interfaces?.length > 0 && (() => {
              const podIfaces = interfaces.find((i) => i.pod === selected.nf.name);
              if (!podIfaces) return null;
              return (
                <div className="mt-3">
                  <div className="text-xs text-slate-500 uppercase tracking-wide mb-1">Interfaces</div>
                  {podIfaces.interfaces.filter((i) => !i.default).map((iface) => (
                    <div key={iface.name} className="mb-2 rounded bg-slate-950 p-2 text-xs">
                      <div className="font-medium text-indigo-300">{iface.label}</div>
                      <div className="text-slate-400 mt-0.5">NAD: {iface.name}</div>
                      {iface.ips?.length > 0 && <div className="font-mono text-slate-300">{iface.ips.join(", ")}</div>}
                      {iface.mac && <div className="text-slate-500">MAC: {iface.mac}</div>}
                    </div>
                  ))}
                </div>
              );
            })()}
          </div>
        )}

        {selected && !selected.nf && (
          <div className="text-slate-400 text-xs">{selected.label} — zone placeholder</div>
        )}

        {nads?.length > 0 && (
          <div className="mt-4 border-t border-slate-800 pt-3">
            <div className="text-xs text-slate-500 uppercase tracking-wide mb-2">Network Attachments</div>
            {nads.map((nad) => (
              <div key={nad.name} className="mb-2 rounded bg-slate-950 p-2 text-xs">
                <div className="font-medium text-slate-200">{nad.name}</div>
                <div className="text-slate-500">Type: {nad.type} {nad.bridge && `| Bridge: ${nad.bridge}`}</div>
                {nad.ipam?.subnet && <div className="font-mono text-slate-400">{nad.ipam.subnet}</div>}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function DetailRow({ label, value, mono }) {
  if (value == null || value === "") return null;
  return (
    <div className="text-xs">
      <span className="text-slate-500">{label}: </span>
      <span className={mono ? "font-mono text-slate-300" : "text-slate-300"}>{String(value)}</span>
    </div>
  );
}
