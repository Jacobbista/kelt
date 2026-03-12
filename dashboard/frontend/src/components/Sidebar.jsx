import React from "react";

const NAV_ITEMS = [
  { id: "overview", label: "Overview", icon: "\u25A3" },
  { id: "core", label: "5G Core", icon: "\u2B22" },
  { id: "topology", label: "Topology", icon: "\u2B95" },
  { id: "ran", label: "RAN", icon: "\u2699" },
  { id: "subscribers", label: "Subscribers", icon: "\u2263" },
  { id: "ue-monitoring", label: "UE Monitor", icon: "\u25C9" },
  { id: "diagnostics", label: "Diagnostics", icon: "\u2295" },
  { id: "metrics", label: "Metrics", icon: "\u2261" },
];

export default function Sidebar({ activePage, onNavigate, runtime }) {
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

      <div className="border-t border-slate-800 px-3 py-3">
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
