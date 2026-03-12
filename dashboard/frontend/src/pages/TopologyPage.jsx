import React, { useEffect, useState } from "react";
import { getClusterSummary, getNads, getNetworkInterfaces, getNfStatus, getTopology } from "../api";
import Loader from "../components/Loader";
import TopologyInfra from "../components/TopologyInfra";
import TopologyLogical from "../components/TopologyLogical";
import useTrafficStream from "../hooks/useTrafficStream";

const TABS = [
  { id: "logical", label: "Logical" },
  { id: "infra", label: "Infrastructure" },
];

export default function TopologyPage() {
  const [tab, setTab] = useState("logical");
  const [loading, setLoading] = useState(true);
  const [nfStatus, setNfStatus] = useState({ control_plane: [], user_plane: [], data: [], other: [] });
  const [nads, setNads] = useState([]);
  const [interfaces, setInterfaces] = useState([]);
  const [topology, setTopology] = useState({ nodes: [], edges: [] });
  const [clusterNodes, setClusterNodes] = useState([]);
  const [error, setError] = useState("");

  const { links: trafficData, connected: trafficConnected } = useTrafficStream();

  useEffect(() => {
    async function load() {
      try {
        setError("");
        const [nf, nad, ifaces, topo, cluster] = await Promise.all([
          getNfStatus(),
          getNads(),
          getNetworkInterfaces(),
          getTopology(),
          getClusterSummary(),
        ]);
        setNfStatus(nf);
        setNads(nad);
        setInterfaces(ifaces);
        setTopology(topo);
        setClusterNodes(cluster.nodes || []);
      } catch (err) {
        setError(String(err.message || err));
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  if (loading) {
    return (
      <div className="flex min-h-[200px] items-center justify-center">
        <Loader size="lg" label="Loading topology…" />
      </div>
    );
  }

  return (
    <div className="flex h-[calc(100vh-3rem)] flex-col">
      <div className="mb-4 flex items-center gap-4 flex-shrink-0">
        <h2 className="text-lg font-semibold">Network Topology</h2>
        {error && (
          <div className="rounded border border-rose-700 bg-rose-950/50 px-3 py-1.5 text-sm text-rose-300">{error}</div>
        )}
        <div className="flex rounded-lg border border-slate-700 bg-slate-900 p-0.5">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => setTab(t.id)}
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
        {trafficConnected && (
          <span className="ml-auto flex items-center gap-1.5 text-[10px] text-emerald-500">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse" />
            Traffic stream
          </span>
        )}
      </div>

      <div className="flex-1 min-h-0">
        {tab === "logical" && (
          <TopologyLogical
            nfStatus={nfStatus}
            nads={nads}
            interfaces={interfaces}
            trafficData={trafficData}
          />
        )}
        {tab === "infra" && (
          <TopologyInfra
            topology={topology}
            clusterNodes={clusterNodes}
          />
        )}
      </div>
    </div>
  );
}
