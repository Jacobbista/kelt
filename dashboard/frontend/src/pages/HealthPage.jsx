import React from "react";
import NetworkHealth from "../components/NetworkHealth";
import useTrafficStream from "../hooks/useTrafficStream";

// Per-interface health of the 5G planes (N2, N3, N4, N6c) with live traffic.
export default function HealthPage() {
  const { links: trafficData, connected: trafficConnected } = useTrafficStream();

  return (
    <div className="flex h-[calc(100vh-6rem)] flex-col">
      {trafficConnected && (
        <div className="mb-4 flex flex-shrink-0 items-center gap-4">
          <span className="ml-auto flex items-center gap-1.5 text-[10px] text-emerald-500">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-500" />
            Traffic stream
          </span>
        </div>
      )}
      <div className="min-h-0 flex-1">
        <NetworkHealth trafficData={trafficData} />
      </div>
    </div>
  );
}
