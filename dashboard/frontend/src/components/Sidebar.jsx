import React, { useState, useEffect, useCallback, useRef } from "react";
import TimeSyncPopover from "./TimeSyncPopover";

const NAV_ITEMS = [
  { id: "overview", label: "Overview", icon: "\u25A3" },
  { id: "kubernetes", label: "Kubernetes", icon: "\u2638" },
  { id: "core", label: "5G Core", icon: "\u2B22" },
  { id: "topology", label: "Topology", icon: "\u2B95" },
  { id: "ran", label: "RAN", icon: "\u2699" },
  { id: "subscribers", label: "Subscribers", icon: "\u2263" },
  { id: "ue-monitoring", label: "UE Monitor", icon: "\u25C9" },
  { id: "diagnostics", label: "Diagnostics", icon: "\u2295" },
  { id: "metrics", label: "Metrics", icon: "\u2261" },
];

const _localFmt = new Intl.DateTimeFormat(undefined, {
  hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
});
const _tzAbbr = (() => {
  // Extract timezone abbreviation (e.g. "CET", "EST") from a formatted date
  const parts = new Intl.DateTimeFormat(undefined, { timeZoneName: "short" }).formatToParts(new Date());
  return parts.find((p) => p.type === "timeZoneName")?.value ?? "LOC";
})();

function useServerClock(serverTime) {
  const offsetRef = useRef(0);
  const [display, setDisplay] = useState(() => _localFmt.format(new Date()));

  useEffect(() => {
    if (serverTime) {
      offsetRef.current = new Date(serverTime).getTime() - Date.now();
    }
  }, [serverTime]);

  useEffect(() => {
    function tick() {
      setDisplay(_localFmt.format(new Date(Date.now() + offsetRef.current)));
    }
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, []);

  return display;
}

export default function Sidebar({ activePage, onNavigate, runtime, serverTime }) {
  const [showSync, setShowSync] = useState(false);
  const clockStr = useServerClock(serverTime);
  const toggleSync = useCallback(() => setShowSync((v) => !v), []);
  const closeSync = useCallback(() => setShowSync(false), []);

  const modeBadgeClass =
    runtime.mode === "dev"
      ? "bg-amber-600 text-amber-50"
      : runtime.mode === "prod"
        ? "bg-emerald-600 text-emerald-50"
        : "bg-slate-600 text-slate-100";

  return (
    <aside className="fixed left-0 top-0 flex h-screen w-56 flex-col bg-slate-900 border-r border-slate-800">
      <div className="px-4 py-5">
        <h1 className="text-lg font-semibold text-white">5G Dashboard</h1>
        <p className="mt-0.5 text-xs text-slate-400">Out-of-band control room</p>
      </div>

      <nav className="flex-1 px-2">
        {NAV_ITEMS.map((item) => (
          <button
            key={item.id}
            type="button"
            onClick={() => onNavigate(item.id)}
            className={`mb-1 flex w-full items-center gap-3 rounded-md px-3 py-2.5 text-left text-sm transition-colors ${
              activePage === item.id
                ? "bg-indigo-600/20 text-indigo-300 font-medium"
                : "text-slate-300 hover:bg-slate-800 hover:text-white"
            }`}
          >
            <span className="text-base">{item.icon}</span>
            {item.label}
          </button>
        ))}
      </nav>

      <div className="relative border-t border-slate-800 px-3 py-3">
        <button
          type="button"
          onClick={toggleSync}
          className="mb-2 flex w-full items-center gap-2 rounded px-2 py-1.5 text-xs text-slate-400 hover:bg-slate-800 hover:text-slate-200 transition-colors"
          title="Cluster time — click for sync details"
        >
          <span className="text-[10px]">&#x25F7;</span>
          <span className="font-mono tabular-nums">
            {clockStr}
          </span>
          <span className="text-[9px] text-slate-600">{_tzAbbr}</span>
        </button>

        {showSync && <TimeSyncPopover onClose={closeSync} />}

        <a
          href="http://192.168.56.11:30300"
          target="_blank"
          rel="noreferrer"
          className="flex items-center gap-2 rounded px-2 py-1.5 text-xs text-slate-500 hover:bg-slate-800 hover:text-slate-300 transition-colors"
        >
          <span className="text-[10px]">&#x2197;</span>
          Grafana (advanced)
        </a>

        <div className="mt-2 flex items-center gap-2">
          <span className={`rounded px-1.5 py-0.5 text-[10px] font-bold uppercase ${modeBadgeClass}`}>
            {runtime.mode}
          </span>
          <span className="truncate text-[10px] text-slate-500" title={runtime.runtime_source}>
            {runtime.runtime_source}
          </span>
        </div>
      </div>
    </aside>
  );
}
