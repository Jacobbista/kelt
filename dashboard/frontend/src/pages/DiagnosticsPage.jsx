import React, { useState } from "react";
import LiveSniffer from "../components/LiveSniffer";
import NetworkHealth from "../components/NetworkHealth";
import useTrafficStream from "../hooks/useTrafficStream";

const TABS = [
  { id: "health", label: "Network Health" },
  { id: "sniffer", label: "Packet Sniffer" },
];

export default function DiagnosticsPage() {
  const [tab, setTab] = useState("health");
  const { links: trafficData, connected: trafficConnected } = useTrafficStream();

  return (
    <div className="flex h-[calc(100vh-3rem)] flex-col">
      <div className="mb-4 flex items-center gap-4 flex-shrink-0">
        <h2 className="text-lg font-semibold">Diagnostics</h2>
        <div className="flex rounded-lg border border-slate-700 bg-slate-900 p-0.5">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => setTab(t.id)}
              className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
                tab === t.id ? "bg-indigo-600/30 text-indigo-300" : "text-slate-400 hover:text-white hover:bg-slate-800"
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
        {tab === "health" && <NetworkHealth trafficData={trafficData} />}
        {tab === "sniffer" && <LiveSniffer />}
      </div>
    </div>
  );
}
