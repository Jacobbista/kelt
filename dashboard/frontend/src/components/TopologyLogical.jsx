import React, { useCallback, useMemo, useState } from "react";
import {
  Background,
  Controls,
  Handle,
  Position,
  ReactFlow,
} from "@xyflow/react";

const STATUS_DOT = {
  Running: "bg-emerald-400",
  Pending: "bg-amber-400 animate-pulse",
  Terminating: "bg-slate-500 animate-pulse",
  Failed: "bg-rose-400",
};

const PALETTE = {
  cp: { bg: "#1e1b4b", border: "#6366f1", color: "#a5b4fc" },
  amf: { bg: "#1e1b4b", border: "#818cf8", color: "#c7d2fe" },
  smf: { bg: "#1e1b4b", border: "#818cf8", color: "#c7d2fe" },
  upf: { bg: "#022c22", border: "#10b981", color: "#6ee7b7" },
  ran: { bg: "#0c1a2e", border: "#0ea5e9", color: "#7dd3fc" },
  dn: { bg: "#1c1917", border: "#a16207", color: "#fbbf24" },
  mongo: { bg: "#1c1917", border: "#a16207", color: "#fbbf24" },
  zone: { bg: "#0f172a", border: "#334155", color: "#94a3b8" },
};

/**
 * Single place to tune positions, equal NF box size, and handle dots.
 * - nfBox: same minWidth/minHeight for all NF blocks (CP, AMF, SMF, MongoDB, UPF).
 * - ran: left of the core path (same x as before); vertical band near UPF without sharing column.
 * - showHandleDots / perNodeShowDots: visible handle circles vs invisible (edges still attach).
 */
export const LOGICAL_LAYOUT = {
  nfBox: { minWidth: 96, minHeight: 48 },
  cp: { startX: 88, y: 30, spacing: 152, showHandleDots: false },
  amf: { x: 320, y: 160 },
  smf: { x: 560, y: 160 },
  mongodb: { x: 800, y: 160 },
  upfCloud: { x: 560, y: 300 },
  upfEdge: { x: 560, y: 430 },
  /** Left column: aligns with UPF only in the vertical band, not stacked on the UPF box. */
  ran: { x: 80, y: 300, width: 130 },
  zoneDn: { x: 800, y: 300 },
  zoneMec: { x: 800, y: 430 },
  showHandleDots: true,
  /** Optional overrides by node id (e.g. zone-ran: true, nf-nrf: false). */
  showHandleDotsByNode: {
    "zone-ran": true,
    "zone-dn": false,
    "zone-mec": false,
  },
};

const POS = {
  top: Position.Top,
  bottom: Position.Bottom,
  left: Position.Left,
  right: Position.Right,
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
    minWidth: LOGICAL_LAYOUT.nfBox.minWidth,
    minHeight: LOGICAL_LAYOUT.nfBox.minHeight,
    textAlign: "center",
  };
}

function nfStyle(palette) {
  return { ...nodeStyle(palette), ...LOGICAL_LAYOUT.nfBox };
}

function showDotForNode(nodeId) {
  if (
    Object.prototype.hasOwnProperty.call(
      LOGICAL_LAYOUT.showHandleDotsByNode,
      nodeId,
    )
  ) {
    return LOGICAL_LAYOUT.showHandleDotsByNode[nodeId];
  }
  if (
    nodeId.startsWith("nf-") &&
    ["nrf", "ausf", "udm", "udr", "pcf", "nssf", "bsf"].some(
      (t) => nodeId === `nf-${t}`,
    )
  ) {
    return LOGICAL_LAYOUT.cp.showHandleDots;
  }
  return LOGICAL_LAYOUT.showHandleDots;
}

/** Handle descriptors: position = top|bottom|left|right; showDot from layout unless overridden. */
function handleDef(nodeId, h) {
  const showDot = h.showDot !== undefined ? h.showDot : showDotForNode(nodeId);
  return { ...h, showDot };
}

function handlesAmf(nodeId) {
  return [
    handleDef(nodeId, { id: "amf-in-top", type: "target", position: "top" }),
    handleDef(nodeId, {
      id: "amf-in-bottom",
      type: "target",
      position: "bottom",
    }),
    handleDef(nodeId, {
      id: "amf-out-right",
      type: "source",
      position: "right",
    }),
  ];
}

function handlesSmf(nodeId, hasUpfEdge) {
  const inTop = handleDef(nodeId, {
    id: "smf-in-top",
    type: "target",
    position: "top",
  });
  const inLeft = handleDef(nodeId, {
    id: "smf-in-left",
    type: "target",
    position: "left",
  });
  if (hasUpfEdge) {
    return [
      inTop,
      inLeft,
      handleDef(nodeId, {
        id: "smf-out-bottom",
        type: "source",
        position: "bottom",
        style: { left: "35%" },
      }),
      handleDef(nodeId, {
        id: "smf-out-bottom-upfe",
        type: "source",
        position: "bottom",
        style: { left: "65%" },
      }),
    ];
  }
  return [
    inTop,
    inLeft,
    handleDef(nodeId, {
      id: "smf-out-bottom",
      type: "source",
      position: "bottom",
      style: { left: "50%" },
    }),
  ];
}

function handlesUpf(nodeId) {
  return [
    handleDef(nodeId, { id: "upf-in-top", type: "target", position: "top" }),
    handleDef(nodeId, { id: "upf-in-left", type: "target", position: "left" }),
    handleDef(nodeId, {
      id: "upf-out-right",
      type: "source",
      position: "right",
    }),
    handleDef(nodeId, {
      id: "upf-out-bottom",
      type: "source",
      position: "bottom",
    }),
  ];
}

function handlesRan(nodeId, hasUpfEdge) {
  const top = handleDef(nodeId, {
    id: "ran-out-top",
    type: "source",
    position: "top",
  });
  if (hasUpfEdge) {
    return [
      top,
      handleDef(nodeId, {
        id: "ran-out-right-upf",
        type: "source",
        position: "right",
        style: { top: "35%" },
      }),
      handleDef(nodeId, {
        id: "ran-out-right-edge",
        type: "source",
        position: "right",
        style: { top: "65%" },
      }),
    ];
  }
  return [
    top,
    handleDef(nodeId, {
      id: "ran-out-right-upf",
      type: "source",
      position: "right",
      style: { top: "50%" },
    }),
  ];
}

function handlesCpNf(nodeId, t) {
  return [
    handleDef(nodeId, {
      id: `nf-${t}-sbi`,
      type: "source",
      position: "bottom",
    }),
  ];
}

function handlesZoneIn(nodeId, side) {
  return [
    handleDef(nodeId, {
      id: `zone-in-${side}`,
      type: "target",
      position: side,
    }),
  ];
}

function edgeStyle(label, pps) {
  const isData = label?.startsWith("N3") || label?.startsWith("N6");
  const hasTraffic = pps > 0;
  const baseWidth = isData ? 2 : 1.5;
  const width = hasTraffic
    ? Math.min(baseWidth + Math.log10(pps + 1), 5)
    : baseWidth;
  return {
    stroke: hasTraffic ? "#34d399" : isData ? "#10b981" : "#6366f1",
    strokeWidth: width,
    strokeDasharray: hasTraffic ? undefined : isData ? undefined : "6 3",
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
  const { startX, y: cpY, spacing } = LOGICAL_LAYOUT.cp;

  const nodes = [];

  cpNfs.forEach((t, i) => {
    const x = startX + i * spacing;
    const id = `nf-${t}`;
    nodes.push({
      id,
      position: { x, y: cpY },
      data: {
        label: t.toUpperCase(),
        nf: nf(t),
        phase: phase(t),
        dot: dot(t),
        handles: handlesCpNf(id, t),
        nodeStyle: nfStyle(PALETTE.cp),
      },
      type: "logicalNode",
    });
  });

  const upfCloud = (nfMap["upf"] || []).find((u) => u.name.includes("cloud"));
  const upfEdge = (nfMap["upf"] || []).find((u) => u.name.includes("edge"));
  const upfAny = nf("upf");
  const hasN6m = (nads || []).some((n) => n.name === "n6m-net");

  nodes.push({
    id: "nf-amf",
    position: { x: LOGICAL_LAYOUT.amf.x, y: LOGICAL_LAYOUT.amf.y },
    data: {
      label: "AMF",
      nf: nf("amf"),
      phase: phase("amf"),
      dot: dot("amf"),
      handles: handlesAmf("nf-amf"),
      nodeStyle: nfStyle(PALETTE.amf),
    },
    type: "logicalNode",
  });

  nodes.push({
    id: "nf-smf",
    position: { x: LOGICAL_LAYOUT.smf.x, y: LOGICAL_LAYOUT.smf.y },
    data: {
      label: "SMF",
      nf: nf("smf"),
      phase: phase("smf"),
      dot: dot("smf"),
      handles: handlesSmf("nf-smf", !!upfEdge),
      nodeStyle: nfStyle(PALETTE.smf),
    },
    type: "logicalNode",
  });

  nodes.push({
    id: "nf-upf-cloud",
    position: { x: LOGICAL_LAYOUT.upfCloud.x, y: LOGICAL_LAYOUT.upfCloud.y },
    data: {
      label: upfCloud ? "UPF-Cloud" : "UPF",
      nf: upfCloud || upfAny,
      phase: (upfCloud || upfAny)?.phase || "absent",
      dot: STATUS_DOT[(upfCloud || upfAny)?.phase] || "bg-slate-600",
      handles: handlesUpf("nf-upf-cloud"),
      nodeStyle: nfStyle(PALETTE.upf),
    },
    type: "logicalNode",
  });

  if (upfEdge) {
    nodes.push({
      id: "nf-upf-edge",
      position: { x: LOGICAL_LAYOUT.upfEdge.x, y: LOGICAL_LAYOUT.upfEdge.y },
      data: {
        label: "UPF-Edge",
        nf: upfEdge,
        phase: upfEdge.phase,
        dot: STATUS_DOT[upfEdge.phase] || "bg-slate-600",
        handles: handlesUpf("nf-upf-edge"),
        nodeStyle: nfStyle(PALETTE.upf),
      },
      type: "logicalNode",
    });
  }

  nodes.push({
    id: "zone-ran",
    position: { x: LOGICAL_LAYOUT.ran.x, y: LOGICAL_LAYOUT.ran.y },
    data: {
      label: "RAN Zone",
      handles: handlesRan("zone-ran", !!upfEdge),
      nodeStyle: {
        ...nfStyle(PALETTE.zone),
        minWidth: LOGICAL_LAYOUT.ran.width,
        borderStyle: "dashed",
      },
    },
    type: "logicalNode",
  });

  nodes.push({
    id: "zone-dn",
    position: { x: LOGICAL_LAYOUT.zoneDn.x, y: LOGICAL_LAYOUT.zoneDn.y },
    data: {
      label: "Internet (N6c)",
      handles: handlesZoneIn("zone-dn", "left"),
      nodeStyle: {
        ...nfStyle(PALETTE.dn),
        borderStyle: "dashed",
      },
    },
    type: "logicalNode",
  });

  if (hasN6m) {
    nodes.push({
      id: "zone-mec",
      position: { x: LOGICAL_LAYOUT.zoneMec.x, y: LOGICAL_LAYOUT.zoneMec.y },
      data: {
        label: "MEC Network (N6m)",
        handles: handlesZoneIn("zone-mec", "top"),
        nodeStyle: {
          ...nfStyle(PALETTE.dn),
          borderStyle: "dashed",
        },
      },
      type: "logicalNode",
    });
  }

  nodes.push({
    id: "nf-mongodb",
    position: { x: LOGICAL_LAYOUT.mongodb.x, y: LOGICAL_LAYOUT.mongodb.y },
    data: {
      label: "MongoDB",
      nf: nf("mongodb"),
      phase: phase("mongodb"),
      dot: dot("mongodb"),
      handles: [],
      nodeStyle: nfStyle(PALETTE.mongo),
    },
    type: "logicalNode",
  });

  return nodes;
}

function ppsLabel(pps) {
  if (!pps || pps < 1) return "";
  if (pps > 1000) return ` ${(pps / 1000).toFixed(1)}k pps`;
  return ` ${Math.round(pps)} pps`;
}

function buildEdges(nfStatus, nads, trafficData) {
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

  const nfMap = {};
  for (const cat of ["control_plane", "user_plane", "data", "other"]) {
    for (const nf of nfStatus[cat] || []) {
      nfMap[nf.nf_type] = nfMap[nf.nf_type] || [];
      nfMap[nf.nf_type].push(nf);
    }
  }
  const upfEdge = (nfMap["upf"] || []).find((u) => u.name.includes("edge"));
  const hasN6m = !!nadMap["n6m-net"];

  const labelStyle = (active) => ({
    fill: active ? "#a5b4fc" : "#94a3b8",
    fontSize: 9,
  });
  const bgStyle = { fill: "#0f172a", fillOpacity: 0.85 };

  const baseEdges = [
    {
      id: "e-amf-smf",
      type: "simplebezier",
      source: "nf-amf",
      sourceHandle: "amf-out-right",
      target: "nf-smf",
      targetHandle: "smf-in-left",
      label: "N11 (SBI)",
      style: edgeStyle("N11", 0),
    },
    {
      id: "e-smf-upf",
      type: "simplebezier",
      source: "nf-smf",
      sourceHandle: "smf-out-bottom",
      target: "nf-upf-cloud",
      targetHandle: "upf-in-top",
      label: `N4 ${subnet("n4-net")}${ppsLabel(pps("N4"))}`,
      animated: pps("N4") > 0,
      style: edgeStyle("N4", pps("N4")),
      labelStyle: labelStyle(pps("N4") > 0),
      labelBgStyle: bgStyle,
    },
    {
      id: "e-ran-amf",
      type: "simplebezier",
      source: "zone-ran",
      sourceHandle: "ran-out-top",
      target: "nf-amf",
      targetHandle: "amf-in-bottom",
      label: `N1/N2 ${subnet("n2-net")}${ppsLabel(pps("N2"))}`,
      animated: pps("N2") > 0,
      style: edgeStyle("N2", pps("N2")),
      labelStyle: labelStyle(pps("N2") > 0),
      labelBgStyle: bgStyle,
    },
    {
      id: "e-ran-upf",
      type: "simplebezier",
      source: "zone-ran",
      sourceHandle: "ran-out-right-upf",
      target: "nf-upf-cloud",
      targetHandle: "upf-in-left",
      label: `N3 ${subnet("n3-net")}${ppsLabel(pps("N3"))}`,
      animated: pps("N3") > 0,
      style: edgeStyle("N3", pps("N3")),
      labelStyle: labelStyle(pps("N3") > 0),
      labelBgStyle: bgStyle,
    },
    {
      id: "e-upf-dn",
      type: "simplebezier",
      source: "nf-upf-cloud",
      sourceHandle: "upf-out-right",
      target: "zone-dn",
      targetHandle: "zone-in-left",
      label: `N6c ${subnet("n6c-net")}${ppsLabel(pps("N6"))}`,
      animated: pps("N6") > 0,
      style: edgeStyle("N6", pps("N6")),
      labelStyle: labelStyle(pps("N6") > 0),
      labelBgStyle: bgStyle,
    },
  ];

  if (hasN6m) {
    baseEdges.push({
      id: "e-upf-mec",
      type: "simplebezier",
      source: "nf-upf-cloud",
      sourceHandle: "upf-out-bottom",
      target: "zone-mec",
      targetHandle: "zone-in-top",
      label: `N6m ${subnet("n6m-net")}`,
      animated: false,
      style: edgeStyle("N6", 0),
      labelStyle: labelStyle(false),
      labelBgStyle: bgStyle,
    });
  }

  if (upfEdge) {
    baseEdges.push({
      id: "e-smf-upfe",
      type: "simplebezier",
      source: "nf-smf",
      sourceHandle: "smf-out-bottom-upfe",
      target: "nf-upf-edge",
      targetHandle: "upf-in-top",
      label: `N4 ${subnet("n4-net")}`,
      animated: false,
      style: edgeStyle("N4", 0),
      labelStyle: labelStyle(false),
      labelBgStyle: bgStyle,
    });
    baseEdges.push({
      id: "e-ran-upfe",
      type: "simplebezier",
      source: "zone-ran",
      sourceHandle: "ran-out-right-edge",
      target: "nf-upf-edge",
      targetHandle: "upf-in-left",
      label: `N3 ${subnet("n3-net")}`,
      animated: false,
      style: edgeStyle("N3", 0),
      labelStyle: labelStyle(false),
      labelBgStyle: bgStyle,
    });
  }

  const sbiEdges = ["nrf", "ausf", "udm", "udr", "pcf", "nssf", "bsf"].map(
    (t) => ({
      id: `e-sbi-${t}`,
      type: "simplebezier",
      source: `nf-${t}`,
      sourceHandle: `nf-${t}-sbi`,
      target: t === "nrf" || t === "ausf" || t === "udm" ? "nf-amf" : "nf-smf",
      targetHandle:
        t === "nrf" || t === "ausf" || t === "udm"
          ? "amf-in-top"
          : "smf-in-top",
      style: { stroke: "#4f46e5", strokeWidth: 1, strokeDasharray: "4 4" },
      label: t === "nrf" || t === "udr" ? "SBI" : "",
      labelStyle: { fill: "#6366f1", fontSize: 8 },
      labelBgStyle: bgStyle,
    }),
  );

  return [...baseEdges, ...sbiEdges];
}

function LogicalNode({ id, data }) {
  const s = data.nodeStyle || {};
  const handles = data.handles || [];
  return (
    <div
      className="relative rounded-xl border-2 shadow-sm text-center flex items-center justify-center"
      style={{ boxSizing: "border-box", ...s }}
    >
      {handles.map((h) => (
        <Handle
          key={h.id}
          id={h.id}
          type={h.type}
          position={POS[h.position] || Position.Top}
          className={
            h.showDot
              ? "!w-2 !h-2 !border border-slate-500 !bg-slate-300"
              : "!opacity-0 !w-1 !h-1 !min-w-0 !min-h-0 !border-0 !bg-transparent"
          }
          style={h.style}
        />
      ))}
      {data.label}
    </div>
  );
}

const nodeTypes = {
  logicalNode: LogicalNode,
};

export default function TopologyLogical({
  nfStatus,
  nads,
  interfaces,
  onSelectNf,
  trafficData,
}) {
  const [selected, setSelected] = useState(null);

  const nodes = useMemo(() => buildNodes(nfStatus, nads), [nfStatus, nads]);
  const edges = useMemo(
    () => buildEdges(nfStatus, nads, trafficData),
    [nfStatus, nads, trafficData],
  );

  const onNodeClick = useCallback(
    (_, node) => {
      setSelected(node.data);
      if (node.data.nf) onSelectNf?.(node.data);
    },
    [onSelectNf],
  );

  return (
    <div className="flex h-full gap-3">
      <div
        className="flex-1 rounded border border-slate-700"
        style={{ minHeight: 400 }}
      >
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          fitView
          fitViewOptions={{ padding: 0.3 }}
          onNodeClick={onNodeClick}
          proOptions={{ hideAttribution: true }}
          style={{ background: "#0f172a" }}
          nodesDraggable={false}
          nodesConnectable={false}
        >
          <Controls className="[&>button]:bg-slate-800 [&>button]:border-slate-700 [&>button]:text-slate-300" />
          <Background color="#1e293b" gap={24} />
        </ReactFlow>
      </div>

      <div className="w-72 flex-shrink-0 overflow-y-auto rounded border border-slate-700 bg-slate-900 p-3 text-sm">
        <div className="mb-3 font-semibold">Details</div>

        {!selected && (
          <div className="text-slate-400 text-xs">
            Click a network function to inspect.
          </div>
        )}

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

            {interfaces?.length > 0 &&
              (() => {
                const podIfaces = interfaces.find(
                  (i) => i.pod === selected.nf.name,
                );
                if (!podIfaces) return null;
                return (
                  <div className="mt-3">
                    <div className="text-xs text-slate-500 uppercase tracking-wide mb-1">
                      Interfaces
                    </div>
                    {podIfaces.interfaces
                      .filter((i) => !i.default)
                      .map((iface) => (
                        <div
                          key={iface.name}
                          className="mb-2 rounded bg-slate-950 p-2 text-xs"
                        >
                          <div className="font-medium text-indigo-300">
                            {iface.label}
                          </div>
                          <div className="text-slate-400 mt-0.5">
                            NAD: {iface.name}
                          </div>
                          {iface.ips?.length > 0 && (
                            <div className="font-mono text-slate-300">
                              {iface.ips.join(", ")}
                            </div>
                          )}
                          {iface.mac && (
                            <div className="text-slate-500">
                              MAC: {iface.mac}
                            </div>
                          )}
                        </div>
                      ))}
                  </div>
                );
              })()}
          </div>
        )}

        {selected && !selected.nf && (
          <div className="text-slate-400 text-xs">{selected.label}</div>
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
      <span className={mono ? "font-mono text-slate-300" : "text-slate-300"}>
        {String(value)}
      </span>
    </div>
  );
}
