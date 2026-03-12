import React, { useCallback, useMemo, useState } from "react";
import { Background, Controls, Handle, Position, ReactFlow } from "@xyflow/react";

const IconK3s = ({ size = 20 }) => <img src="/icons/k3s.svg" alt="K3s" width={size} height={size} />;
const IconKubeEdge = ({ size = 20 }) => <img src="/icons/kubeedge.svg" alt="KubeEdge" width={size} height={size} />;
const IconAnsible = ({ size = 20 }) => <img src="/icons/ansible.svg" alt="Ansible" width={size} height={size} />;

const NF_COLORS = {
  amf: { bg: "#1e1b4b", border: "#818cf8" },
  smf: { bg: "#1e1b4b", border: "#818cf8" },
  nrf: { bg: "#2e1065", border: "#a78bfa" },
  udm: { bg: "#2e1065", border: "#a78bfa" },
  udr: { bg: "#2e1065", border: "#a78bfa" },
  ausf: { bg: "#2e1065", border: "#a78bfa" },
  pcf: { bg: "#2e1065", border: "#a78bfa" },
  nssf: { bg: "#2e1065", border: "#a78bfa" },
  bsf: { bg: "#2e1065", border: "#a78bfa" },
  upf: { bg: "#022c22", border: "#34d399" },
  mongodb: { bg: "#1c1917", border: "#f59e0b" },
  gnb: { bg: "#0c1a2e", border: "#22d3ee" },
  ue: { bg: "#0c1a2e", border: "#2dd4bf" },
  mec: { bg: "#1c1917", border: "#f43f5e" },
};

const BRIDGE_COLORS = {
  N1: { border: "#38bdf8", bg: "#0c4a6e20" },
  N2: { border: "#818cf8", bg: "#312e8120" },
  N3: { border: "#34d399", bg: "#022c2220" },
  N4: { border: "#fbbf24", bg: "#78350f20" },
  N6: { border: "#fb923c", bg: "#7c2d1220" },
  RAN: { border: "#22d3ee", bg: "#0c1a2e20" },
};

function classifyBridge(name) {
  if (name.startsWith("br-n2")) return "N2";
  if (name.startsWith("br-n3")) return "N3";
  if (name.startsWith("br-n1")) return "N1";
  if (name.startsWith("br-n4")) return "N4";
  if (name.startsWith("br-n6")) return "N6";
  if (name === "br-ran") return "RAN";
  return "";
}

function inferNfType(label) {
  for (const k of Object.keys(NF_COLORS)) {
    if ((label || "").toLowerCase().includes(k)) return k;
  }
  return "pod";
}

function buildGraph(topology, clusterNodes) {
  const nodes = [];
  const edges = [];

  const vmMap = {};
  for (const n of clusterNodes || []) {
    const role = n.roles?.[0] || (n.name === "master" ? "master" : "worker");
    vmMap[n.name] = { ...n, role };
  }

  const bridgeNodes = (topology?.nodes || []).filter((n) => n.type === "bridge");
  const podNodes = (topology?.nodes || []).filter((n) => n.type !== "bridge");

  const podsByVm = {};
  for (const p of podNodes) {
    const vm = p.data?.node || "worker";
    if (!podsByVm[vm]) podsByVm[vm] = [];
    podsByVm[vm].push(p);
  }

  const edgeVm = vmMap.edge || vmMap[Object.keys(vmMap).find((k) => vmMap[k]?.role === "edge" || vmMap[k]?.role === "agent")];
  const workerVm = vmMap.worker || vmMap[Object.keys(vmMap).find((k) => vmMap[k]?.role === "worker")];
  const masterVm = vmMap.master || vmMap[Object.keys(vmMap).find((k) => vmMap[k]?.role === "control-plane" || vmMap[k]?.role === "master")];

  const COL_EDGE = 0;
  const COL_BRIDGE = 320;
  const COL_WORKER = 580;
  const ROW_H = 70;
  const BOX_H = 220;

  // ─── EDGE box ────────────────────────────────────────────
  const edgePods = podsByVm.edge || [];
  const edgePodsData = edgePods.map((p) => ({
    label: p.label,
    type: p.type || inferNfType(p.label),
    phase: p.data?.phase || "Unknown",
  }));

  if (edgeVm) {
    nodes.push({
      id: "edge",
      position: { x: COL_EDGE, y: 0 },
      type: "vmNode",
      data: {
        label: "EDGE",
        sublabel: edgeVm.ip,
        status: edgeVm.status,
        role: "edge",
        pods: edgePodsData,
        podSection: "RAN Pods",
        icons: ["kubeedge"],
        iconTooltips: { kubeedge: "KubeEdge EdgeCore" },
      },
    });
  }

  // ─── Bridge nodes (on connections) ───────────────────────
  bridgeNodes.forEach((b, i) => {
    const bType = classifyBridge(b.label);
    const vxlanPorts = (b.data?.ports || []).filter((p) => p.startsWith("vxlan"));
    const patchPorts = (b.data?.ports || []).filter((p) => p.startsWith("patch"));
    nodes.push({
      id: b.id,
      position: { x: COL_BRIDGE, y: i * ROW_H },
      type: "bridgeNode",
      data: {
        name: b.label,
        ifaceType: bType,
        vxlanPorts,
        patchPorts,
        totalPorts: (b.data?.ports || []).length,
      },
    });
  });

  // ─── WORKER box ──────────────────────────────────────────
  const workerPods = podsByVm.worker || podsByVm[Object.keys(podsByVm).find((k) => k !== "edge")] || [];
  const nfPodsData = workerPods.map((p) => ({
    label: p.label,
    type: p.type || inferNfType(p.label),
    phase: p.data?.phase || "Unknown",
  }));

  if (workerVm) {
    nodes.push({
      id: "worker",
      position: { x: COL_WORKER, y: 0 },
      type: "vmNode",
      data: {
        label: "WORKER",
        sublabel: workerVm.ip,
        status: workerVm.status,
        role: "worker",
        pods: nfPodsData,
        podSection: "Core NFs",
        icons: ["k3s", "kubeedge"],
        iconTooltips: { k3s: "K3s worker", kubeedge: "KubeEdge CloudCore" },
      },
    });
  }

  // ─── Edges: edge box → bridges (VXLAN) ────────────────────
  const bridgeIdsWithVxlan = bridgeNodes
    .filter((b) => (b.data?.ports || []).some((p) => p.startsWith("vxlan")))
    .map((b) => b.id);

  if (edgeVm && bridgeIdsWithVxlan.length > 0) {
    const vxlanCount = bridgeNodes.reduce((s, b) => s + (b.data?.ports || []).filter((p) => p.startsWith("vxlan")).length, 0);
    edges.push({
      id: "e-edge-bridges",
      source: "edge",
      target: bridgeIdsWithVxlan[0],
      label: `VXLAN overlay (${vxlanCount} tunnels)`,
      animated: true,
      style: { stroke: "#818cf8", strokeWidth: 2, strokeDasharray: "6 4" },
      labelStyle: { fill: "#818cf8", fontSize: 9 },
      labelBgStyle: { fill: "#0f172a", fillOpacity: 0.9 },
    });
    for (let i = 1; i < bridgeIdsWithVxlan.length; i++) {
      edges.push({
        id: `e-edge-${bridgeIdsWithVxlan[i]}`,
        source: "edge",
        target: bridgeIdsWithVxlan[i],
        style: { stroke: "#818cf8", strokeWidth: 1.5, strokeDasharray: "6 4" },
      });
    }
  }

  // ─── Edges: bridges → worker box ─────────────────────────
  for (const b of bridgeNodes) {
    edges.push({
      id: `e-br-${b.id}-worker`,
      source: b.id,
      target: "worker",
      style: { stroke: "#6366f1", strokeWidth: 1.5 },
    });
  }

  // ─── Ansible + Master ────────────────────────────────────
  const bottomY = Math.max(bridgeNodes.length * ROW_H, BOX_H) + 50;

  nodes.push({
    id: "ansible",
    position: { x: COL_EDGE, y: bottomY },
    type: "ansibleNode",
    data: {},
  });

  if (masterVm) {
    nodes.push({
      id: "master",
      position: { x: COL_WORKER, y: bottomY },
      type: "masterNode",
      data: { ip: masterVm.ip, status: masterVm.status },
    });
    edges.push({
      id: "e-ansible-edge",
      source: "ansible",
      target: "edge",
      label: "provisions",
      style: { stroke: "#ea580c", strokeWidth: 1, strokeDasharray: "5 4" },
      labelStyle: { fill: "#ea580c", fontSize: 8 },
      labelBgStyle: { fill: "#0f172a", fillOpacity: 0.9 },
    });
    edges.push({
      id: "e-ansible-worker",
      source: "ansible",
      target: "worker",
      label: "provisions",
      style: { stroke: "#ea580c", strokeWidth: 1, strokeDasharray: "5 4" },
      labelStyle: { fill: "#ea580c", fontSize: 8 },
      labelBgStyle: { fill: "#0f172a", fillOpacity: 0.9 },
    });
    edges.push({
      id: "e-ansible-master",
      source: "ansible",
      target: "master",
      label: "provisions",
      style: { stroke: "#ea580c", strokeWidth: 1, strokeDasharray: "5 4" },
      labelStyle: { fill: "#ea580c", fontSize: 8 },
      labelBgStyle: { fill: "#0f172a", fillOpacity: 0.9 },
    });
    edges.push({
      id: "e-worker-master",
      source: "worker",
      target: "master",
      label: "K3s API",
      style: { stroke: "#64748b", strokeWidth: 1, strokeDasharray: "5 4" },
      labelStyle: { fill: "#64748b", fontSize: 8 },
      labelBgStyle: { fill: "#0f172a", fillOpacity: 0.9 },
    });
  }

  return { nodes, edges };
}

// ─── Node Components ─────────────────────────────────────────────

const STATUS_DOT = { Running: "bg-emerald-400", Pending: "bg-amber-400 animate-pulse", Failed: "bg-rose-400" };
const ICON_MAP = { k3s: IconK3s, kubeedge: IconKubeEdge };

const ROLE_STYLE = {
  edge: { border: "#0ea5e9", bg: "#0c1a2e", badge: "bg-cyan-600" },
  worker: { border: "#6366f1", bg: "#1e1b4b", badge: "bg-indigo-600" },
  master: { border: "#8b5cf6", bg: "#1e1b4b", badge: "bg-violet-600" },
};

function getPodColor(label) {
  for (const [key, val] of Object.entries(NF_COLORS)) {
    if ((label || "").toLowerCase().includes(key)) return { border: val.border };
  }
  return { border: "#475569" };
}

function VmNode({ data }) {
  const [expandedIcon, setExpandedIcon] = useState(null);
  const s = ROLE_STYLE[data.role] || ROLE_STYLE.worker;
  const ready = data.status === "Ready";

  return (
    <div className="rounded-xl border-2 shadow-xl" style={{ background: s.bg, borderColor: s.border, minWidth: data.role === "worker" ? 280 : 200, padding: 14 }}>
      <Handle type="target" position={Position.Left} className="!w-2.5 !h-2.5 !bg-slate-400" />
      <Handle type="source" position={Position.Right} className="!w-2.5 !h-2.5 !bg-slate-400" />
      <Handle type="source" position={Position.Bottom} className="!w-2.5 !h-2.5 !bg-slate-400" id="bottom" />

      <div className="flex items-center gap-2 mb-1.5">
        <span className={`rounded px-2 py-0.5 text-[10px] font-bold uppercase text-white ${s.badge}`}>{data.label}</span>
        <div className="flex items-center gap-1 ml-auto">
          {(data.icons || []).map((key) => {
            const Icon = ICON_MAP[key];
            if (!Icon) return null;
            return (
              <div key={key} className="relative">
                <button
                  type="button"
                  onClick={(e) => { e.stopPropagation(); setExpandedIcon(expandedIcon === key ? null : key); }}
                  className="flex items-center justify-center w-7 h-7 rounded-lg hover:bg-white/10 transition-colors"
                  title={data.iconTooltips?.[key]}
                >
                  <Icon size={20} />
                </button>
                {expandedIcon === key && (
                  <div className="absolute left-full top-0 ml-2 z-[100] whitespace-nowrap rounded-lg border border-slate-600 bg-slate-900 px-2.5 py-1.5 text-[11px] text-slate-300 shadow-xl">
                    {data.iconTooltips?.[key]}
                  </div>
                )}
              </div>
            );
          })}
          <span className={`ml-1 text-[9px] font-medium px-1.5 py-0.5 rounded ${ready ? "bg-emerald-600/30 text-emerald-400" : "bg-rose-600/30 text-rose-400"}`}>
            {data.status || "Unknown"}
          </span>
        </div>
      </div>

      {data.sublabel && <div className="text-[10px] font-mono text-slate-500 mb-2">{data.sublabel}</div>}

      {data.pods?.length > 0 && (
        <div>
          {data.podSection && <div className="text-[9px] uppercase tracking-wider text-slate-500 mb-1.5">{data.podSection}</div>}
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
            {data.pods.map((p, i) => {
              const alive = p.phase === "Running";
              const pc = getPodColor(p.label);
              return (
                <span
                  key={i}
                  className={`relative rounded-md border px-2.5 py-1 text-[10px] font-mono leading-none ${alive ? "bg-white/5" : "border-slate-700 bg-slate-800/50 text-slate-500"}`}
                  style={alive ? { borderColor: pc.border } : {}}
                >
                  <span className={`absolute top-0.5 right-0.5 h-1.5 w-1.5 rounded-full ${alive ? "bg-emerald-400" : "bg-slate-600"}`} />
                  {p.label}
                </span>
              );
            })}
          </div>
        </div>
      )}
      {data.pods?.length === 0 && data.role !== "master" && (
        <div className="text-[10px] text-slate-600 italic">No pods</div>
      )}
      {data.role === "master" && (
        <div className="text-[10px] text-slate-500">K3s control plane</div>
      )}
    </div>
  );
}

function BridgeNode({ data }) {
  const bc = BRIDGE_COLORS[data.ifaceType] || { border: "#475569", bg: "#0f172a20" };
  return (
    <div className="rounded-lg border px-3 py-2 shadow-md" style={{ borderColor: bc.border, background: bc.bg, minWidth: 160 }}>
      <Handle type="target" position={Position.Left} className="!w-2 !h-2" style={{ background: bc.border }} />
      <Handle type="source" position={Position.Right} className="!w-2 !h-2" style={{ background: bc.border }} />
      <div className="flex items-center gap-2 mb-1">
        <span className="font-mono text-[11px] font-semibold text-slate-200">{data.name}</span>
        {data.ifaceType && (
          <span className="rounded px-1.5 py-0.5 text-[8px] font-bold uppercase" style={{ color: bc.border, background: bc.bg, border: `1px solid ${bc.border}40` }}>
            {data.ifaceType}
          </span>
        )}
      </div>
      <div className="flex flex-wrap gap-1">
        {data.vxlanPorts.map((p) => (
          <span key={p} className="rounded bg-indigo-600/25 px-1.5 py-0.5 text-[8px] font-mono text-indigo-300">{p}</span>
        ))}
        {data.patchPorts.map((p) => (
          <span key={p} className="rounded bg-amber-600/20 px-1.5 py-0.5 text-[8px] font-mono text-amber-300">{p}</span>
        ))}
        {data.totalPorts > data.vxlanPorts.length + data.patchPorts.length && (
          <span className="rounded bg-slate-700/40 px-1.5 py-0.5 text-[8px] font-mono text-slate-500">
            +{data.totalPorts - data.vxlanPorts.length - data.patchPorts.length} veth
          </span>
        )}
      </div>
    </div>
  );
}

function AnsibleNodeComp() {
  return (
    <div className="rounded-xl border-2 shadow-lg px-4 py-3" style={{ background: "#1c1917", borderColor: "#ea580c", minWidth: 130 }}>
      <Handle type="source" position={Position.Right} className="!w-2.5 !h-2.5 !bg-orange-500" />
      <div className="flex items-center gap-2">
        <span className="rounded px-2 py-0.5 text-[9px] font-bold uppercase text-white bg-orange-600">Ansible</span>
        <IconAnsible size={20} />
      </div>
      <div className="text-[9px] text-orange-300/60 mt-1">Provisions all VMs</div>
    </div>
  );
}

function MasterNodeComp({ data }) {
  const ready = data.status === "Ready";
  return (
    <div className="rounded-xl border-2 shadow-lg px-4 py-3" style={{ background: "#1e1b4b", borderColor: "#8b5cf6", minWidth: 130 }}>
      <Handle type="target" position={Position.Left} className="!w-2.5 !h-2.5 !bg-violet-400" />
      <div className="flex items-center gap-2 mb-1">
        <span className="rounded px-2 py-0.5 text-[9px] font-bold uppercase text-white bg-violet-600">Master</span>
        <IconK3s size={20} />
        <span className={`ml-auto text-[8px] px-1.5 py-0.5 rounded ${ready ? "bg-emerald-600/20 text-emerald-400" : "bg-rose-600/20 text-rose-400"}`}>
          {data.status || "Unknown"}
        </span>
      </div>
      <div className="text-[9px] font-mono text-slate-500">{data.ip}</div>
      <div className="text-[9px] text-violet-300/60 mt-0.5">K3s control plane</div>
    </div>
  );
}

const nodeTypes = {
  vmNode: VmNode,
  bridgeNode: BridgeNode,
  ansibleNode: AnsibleNodeComp,
  masterNode: MasterNodeComp,
};

export default function TopologyInfra({ topology, clusterNodes }) {
  const [selected, setSelected] = useState(null);

  const { nodes, edges } = useMemo(() => {
    try {
      return buildGraph(topology, clusterNodes);
    } catch (err) {
      console.error("TopologyInfra build error:", err);
      return { nodes: [], edges: [] };
    }
  }, [topology, clusterNodes]);

  const onNodeClick = useCallback((_, node) => {
    const d = node.data;
    if (d?.label || d?.name || d?.pods?.length) setSelected({ ...d, nodeId: node.id });
  }, []);

  return (
    <div className="h-full flex gap-3 min-h-0">
      <div className="flex-1 flex flex-col min-h-[460px] rounded-xl border-2 border-slate-600/60 bg-slate-950/50 overflow-hidden">
        <div className="flex items-center gap-2 px-4 py-2 border-b border-slate-700/50 bg-slate-900/30 flex-shrink-0">
          <span className="text-[10px] uppercase tracking-wider text-slate-500 font-medium">Host PC</span>
          <span className="text-slate-700">·</span>
          <span className="text-[10px] text-slate-600">Local infrastructure</span>
          <span className="ml-auto text-[10px] text-slate-600">Dashboard on host (out-of-band)</span>
        </div>
        <div className="flex-1 min-h-0" style={{ background: "#0f172a" }}>
          {nodes.length > 0 ? (
            <ReactFlow
              nodes={nodes}
              edges={edges}
              fitView
              fitViewOptions={{ padding: 0.15 }}
              proOptions={{ hideAttribution: true }}
              nodeTypes={nodeTypes}
              nodesDraggable
              onNodeClick={onNodeClick}
              style={{ width: "100%", height: "100%" }}
              minZoom={0.3}
              maxZoom={2}
            >
              <Controls className="[&>button]:bg-slate-800 [&>button]:border-slate-700 [&>button]:text-slate-300" />
              <Background color="#1e293b" gap={24} />
            </ReactFlow>
          ) : (
            <div className="flex h-full items-center justify-center text-slate-500 text-sm">
              <div className="text-center">
                <p>No cluster data available.</p>
                <p className="text-xs mt-1">Ensure the backend can reach the cluster and OVS on the worker.</p>
              </div>
            </div>
          )}
        </div>
      </div>

      {selected && (
        <div className="w-64 flex-shrink-0 overflow-y-auto rounded-xl border border-slate-700 bg-slate-900 p-3 text-sm">
          <div className="flex items-center justify-between mb-3">
            <span className="font-semibold text-white text-xs">Details</span>
            <button type="button" onClick={() => setSelected(null)} className="text-slate-500 hover:text-white text-xs">×</button>
          </div>
          <div className="space-y-2 text-xs">
            {selected.label && !selected.pods && <div><span className="text-slate-500">Name</span> <span className="font-mono text-white">{selected.label}</span></div>}
            {selected.name && <div><span className="text-slate-500">Bridge</span> <span className="font-mono text-white">{selected.name}</span></div>}
            {selected.sublabel && <div><span className="text-slate-500">IP</span> <span className="font-mono text-slate-300">{selected.sublabel}</span></div>}
            {selected.role && <div><span className="text-slate-500">Role</span> <span className="text-slate-300">{selected.role}</span></div>}
            {selected.nfType && <div><span className="text-slate-500">Type</span> <span className="text-slate-300">{selected.nfType}</span></div>}
            {selected.phase && !selected.pods && <div><span className="text-slate-500">Phase</span> <span className="text-slate-300">{selected.phase}</span></div>}
            {selected.ifaceType && <div><span className="text-slate-500">Interface</span> <span className="text-slate-300">{selected.ifaceType}</span></div>}
            {selected.pods?.length > 0 && (
              <div>
                <span className="text-slate-500 block mb-1">Pods ({selected.pods.length})</span>
                {selected.pods.map((p, i) => (
                  <div key={i} className="flex items-center gap-1.5 py-0.5">
                    <span className={`h-1.5 w-1.5 rounded-full ${p.phase === "Running" ? "bg-emerald-400" : "bg-slate-600"}`} />
                    <span className="font-mono text-slate-300">{p.label}</span>
                    <span className="text-slate-600 text-[10px]">{p.phase}</span>
                  </div>
                ))}
              </div>
            )}
            {selected.vxlanPorts?.length > 0 && (
              <div>
                <span className="text-slate-500 block mb-1">VXLAN ports</span>
                {selected.vxlanPorts.map((p) => <div key={p} className="font-mono text-indigo-300 text-[10px]">{p}</div>)}
              </div>
            )}
            {selected.patchPorts?.length > 0 && (
              <div>
                <span className="text-slate-500 block mb-1">Patch ports</span>
                {selected.patchPorts.map((p) => <div key={p} className="font-mono text-amber-300 text-[10px]">{p}</div>)}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
