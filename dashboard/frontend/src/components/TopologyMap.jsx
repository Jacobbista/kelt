import React, { useCallback, useMemo, useState } from "react";
import { Background, Controls, MiniMap, ReactFlow } from "@xyflow/react";

const COL_MAP = {
  bridge: 0,
  amf: 1, smf: 1, nrf: 1, nssf: 1,
  udm: 2, udr: 2, ausf: 2, pcf: 2, bsf: 2,
  upf: 3, mongodb: 3,
  gnb: 4, ue: 4,
  pod: 2,
};

const NODE_COLORS = {
  bridge:  { bg: "#1e293b", border: "#475569", color: "#94a3b8" },
  amf:     { bg: "#1e1b4b", border: "#6366f1", color: "#a5b4fc" },
  smf:     { bg: "#1e1b4b", border: "#6366f1", color: "#a5b4fc" },
  nrf:     { bg: "#1e1b4b", border: "#6366f1", color: "#a5b4fc" },
  nssf:    { bg: "#1e1b4b", border: "#6366f1", color: "#a5b4fc" },
  udm:     { bg: "#1e1b4b", border: "#818cf8", color: "#c7d2fe" },
  udr:     { bg: "#1e1b4b", border: "#818cf8", color: "#c7d2fe" },
  ausf:    { bg: "#1e1b4b", border: "#818cf8", color: "#c7d2fe" },
  pcf:     { bg: "#1e1b4b", border: "#818cf8", color: "#c7d2fe" },
  bsf:     { bg: "#1e1b4b", border: "#818cf8", color: "#c7d2fe" },
  upf:     { bg: "#022c22", border: "#10b981", color: "#6ee7b7" },
  mongodb: { bg: "#1c1917", border: "#a16207", color: "#fbbf24" },
  gnb:     { bg: "#0c1a2e", border: "#0ea5e9", color: "#7dd3fc" },
  ue:      { bg: "#0c1a2e", border: "#0ea5e9", color: "#7dd3fc" },
};

const DEFAULT_COLOR = { bg: "#1e293b", border: "#475569", color: "#cbd5e1" };

function computeLayout(rawNodes) {
  const rowByCol = {};
  return rawNodes.map((node) => {
    const col = COL_MAP[node.type] ?? 2;
    const row = rowByCol[col] ?? 0;
    rowByCol[col] = row + 1;

    const palette = NODE_COLORS[node.type] || DEFAULT_COLOR;
    return {
      ...node,
      position: { x: 60 + col * 240, y: 60 + row * 90 },
      data: { ...(node.data || {}), label: node.label },
      style: {
        background: palette.bg,
        borderColor: palette.border,
        color: palette.color,
        borderWidth: 1,
        borderRadius: 6,
        fontSize: 12,
        padding: "6px 12px",
      },
    };
  });
}

function styleEdges(rawEdges) {
  return rawEdges.map((edge) => ({
    ...edge,
    animated: edge.label?.includes("n3"),
    label: edge.label || "",
    style: { stroke: "#475569" },
    labelStyle: { fill: "#94a3b8", fontSize: 10 },
    labelBgStyle: { fill: "#0f172a", fillOpacity: 0.8 },
  }));
}

export default function TopologyMap({ topology, selectedEdge, onSelectEdge, bridgeFlows, onLoadFlows }) {
  const [selectedNode, setSelectedNode] = useState(null);

  const nodes = useMemo(() => computeLayout(topology.nodes || []), [topology]);
  const edges = useMemo(() => styleEdges(topology.edges || []), [topology]);

  const onNodeClick = useCallback((_, node) => setSelectedNode(node), []);
  const onEdgeClick = useCallback((_, edge) => onSelectEdge(edge), [onSelectEdge]);

  return (
    <div className="flex h-full gap-3">
      <div className="flex-1 rounded border border-slate-700" style={{ minHeight: 400 }}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          fitView
          fitViewOptions={{ padding: 0.2 }}
          onNodeClick={onNodeClick}
          onEdgeClick={onEdgeClick}
          proOptions={{ hideAttribution: true }}
          style={{ background: "#0f172a" }}
        >
          <MiniMap
            nodeColor={(n) => (NODE_COLORS[n.type] || DEFAULT_COLOR).border}
            maskColor="rgba(15, 23, 42, 0.7)"
            style={{ background: "#1e293b" }}
          />
          <Controls className="[&>button]:bg-slate-800 [&>button]:border-slate-700 [&>button]:text-slate-300" />
          <Background color="#1e293b" gap={20} />
        </ReactFlow>
      </div>

      <div className="w-72 flex-shrink-0 rounded border border-slate-700 bg-slate-900 p-3 text-sm overflow-y-auto">
        <div className="mb-2 font-semibold">Interface Inspector</div>

        {!selectedEdge && !selectedNode && (
          <div className="text-slate-400">Click a node or edge to inspect.</div>
        )}

        {selectedNode && (
          <div className="mb-4 space-y-1">
            <div className="text-xs text-slate-500 uppercase tracking-wide">Node</div>
            <div className="font-mono text-xs text-slate-200">{selectedNode.data?.label || selectedNode.id}</div>
            <div className="text-xs text-slate-400">Type: {selectedNode.type}</div>
            {selectedNode.data?.phase && (
              <div className="text-xs text-slate-400">Phase: {selectedNode.data.phase}</div>
            )}
            {selectedNode.data?.ports && (
              <div className="mt-2">
                <div className="text-xs text-slate-500 mb-1">Ports</div>
                <div className="space-y-0.5">
                  {selectedNode.data.ports.map((p) => (
                    <div key={p} className="font-mono text-[11px] text-slate-300">{p}</div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {selectedEdge && (
          <div className="space-y-2">
            <div>
              <span className="text-slate-400">Link:</span> {selectedEdge.label}
            </div>
            <pre className="overflow-auto rounded bg-slate-950 p-2 text-xs text-slate-300">
              {JSON.stringify(selectedEdge.data || {}, null, 2)}
            </pre>
          </div>
        )}

        {selectedNode?.id?.startsWith("bridge:") && (
          <div className="mt-4 space-y-2">
            <button
              type="button"
              className="rounded bg-indigo-600 px-2 py-1 text-xs hover:bg-indigo-500 transition-colors"
              onClick={() => onLoadFlows(selectedNode.data?.bridge || selectedNode.label)}
            >
              Load OVS Flows
            </button>
            {bridgeFlows && (
              <pre className="max-h-52 overflow-auto rounded bg-slate-950 p-2 text-[11px] text-slate-300">{bridgeFlows}</pre>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
