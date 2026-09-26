import React from "react";
import LiveSniffer from "../components/LiveSniffer";

// Live packet capture. It runs a privileged pod, so the route is admin-only.
export default function CapturePage() {
  return (
    <div className="flex h-[calc(100vh-6rem)] flex-col">
      <div className="min-h-0 flex-1">
        <LiveSniffer />
      </div>
    </div>
  );
}
