import React from "react";
import RanConfig from "../RanConfig";

// UERANSIM stays visible so the option is known, but it is not supported in
// this version (docs/status.md: Experimental, not exercised; a v1 objective).
// Workloads left by an earlier install are still managed here.
export default function SimulatedRan({ sim }) {
  const installed = !!sim && ((sim.gnbs || []).length > 0 || (sim.ues || []).length > 0);
  if (installed) return <RanConfig />;
  return (
    <section className="flex flex-col gap-3 rounded-lg border border-dashed border-slate-700 bg-slate-900/40 p-4 md:flex-row md:items-center md:gap-6">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-slate-400">UERANSIM</span>
          <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-400">Not supported yet</span>
        </div>
        <p className="mt-1 max-w-[70ch] text-xs text-slate-500">Simulated gNBs and UEs as pods on the worker, for tests without radio hardware. Planned for version 1: the phase that installs it today is not maintained, so this page does not offer it.</p>
      </div>
    </section>
  );
}
