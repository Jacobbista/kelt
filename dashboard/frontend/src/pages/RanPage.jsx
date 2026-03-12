import React, { useState } from "react";
import RanConfig from "../components/RanConfig";

const TABS = [
  { id: "physical", label: "Physical RAN" },
  { id: "ueransim", label: "UERANSIM" },
];

export default function RanPage() {
  const [tab, setTab] = useState("physical");

  return (
    <div className="flex h-[calc(100vh-3rem)] flex-col">
      <div className="mb-4 flex items-center gap-4 flex-shrink-0">
        <h2 className="text-lg font-semibold">RAN Control</h2>
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
      </div>

      <div className="flex-1 min-h-0">
        <RanConfig activeTab={tab} />
      </div>
    </div>
  );
}
