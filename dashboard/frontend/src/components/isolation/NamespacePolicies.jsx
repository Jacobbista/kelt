import React, { useState } from "react";

// One row per namespace phase 13 isolates: what it holds, who may enter which
// app, whether its outbound traffic is limited. Expanding a row lists the
// destinations of a limited egress and the policy names.
export default function NamespacePolicies({ policies }) {
  const [open, setOpen] = useState(() => new Set(["mec"]));
  const toggle = (name) => setOpen((prev) => {
    const next = new Set(prev);
    if (next.has(name)) next.delete(name); else next.add(name);
    return next;
  });

  if (!policies.enabled) {
    return (
      <div className="rounded-lg border border-slate-700 bg-slate-900 px-4 py-3 text-xs text-slate-400">
        No NetworkPolicy is applied: every namespace accepts every connection. Phase 13
        adds them when <span className="font-mono">network_policies_enabled</span> is true in all.yml.
      </div>
    );
  }

  const grid = "grid grid-cols-[7rem_minmax(0,1.2fr)_minmax(0,1.5fr)_5.5rem_1rem] items-center gap-3";
  return (
    <div className="overflow-hidden rounded-lg border border-slate-700 bg-slate-900">
      <div className={`${grid} border-b border-slate-800 bg-slate-950/40 px-4 py-2 text-[10px] font-semibold uppercase tracking-wider text-slate-500 max-md:hidden`}>
        <span>Namespace</span><span>Holds</span><span>Also accepts</span><span>Egress</span><span />
      </div>
      {policies.namespaces.map((ns) => {
        const isOpen = open.has(ns.name);
        return (
          <div key={ns.name} className="border-b border-slate-800 last:border-b-0">
            <button
              type="button"
              onClick={() => toggle(ns.name)}
              aria-expanded={isOpen}
              className={`${grid} w-full px-4 py-2.5 text-left text-xs transition-colors hover:bg-slate-800/40 max-md:grid-cols-[1fr_1rem]`}
            >
              <span className="font-mono font-semibold text-slate-100">{ns.name}</span>
              <span className="text-slate-500 max-md:hidden">{ns.role}</span>
              <span className="flex flex-wrap gap-1 max-md:hidden">
                {ns.allow.length === 0 ? (
                  <span className="text-slate-600">nothing else</span>
                ) : (
                  ns.allow.map((a, i) => (
                    <span key={`${a.from}-${a.to}-${i}`} className="whitespace-nowrap rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-300">
                      {a.from} <span className="text-slate-500">→ {a.to}</span>
                    </span>
                  ))
                )}
              </span>
              <span className={`max-md:hidden ${ns.egress.limited ? "font-semibold text-amber-300" : "text-slate-500"}`}>
                {ns.egress.limited ? "Limited" : "Open"}
              </span>
              <span className={`text-slate-500 transition-transform duration-150 ${isOpen ? "rotate-90" : ""}`}>›</span>
            </button>
            {isOpen && (
              <div className="bg-slate-950/40 px-4 pb-3 pt-1 text-xs leading-relaxed text-slate-400 md:pl-[8.75rem]">
                <div className="md:hidden">{ns.role}</div>
                {ns.allow.length > 0 ? (
                  <div>Accepts, besides the common sources: {ns.allow.map((a) => `${a.from} to ${a.to}`).join("; ")}.</div>
                ) : (
                  <div>Accepts only the common sources.</div>
                )}
                {ns.egress.limited && (
                  <div className="mt-1">Outbound limited to: {ns.egress.to.join("; ")}. Everything else is refused.</div>
                )}
                <div className="mt-1.5 font-mono text-[10px] text-slate-500">{ns.policies.join(" · ")}</div>
              </div>
            )}
          </div>
        );
      })}
      <div className="bg-slate-950/40 px-4 py-2.5 text-[11px] text-slate-500">
        Common sources, accepted by every namespace above: {policies.common_sources.join(", ")}.
        {policies.unlisted.length > 0 && (
          <> Not isolated (Kubernetes default, accept everything): <span className="font-mono">{policies.unlisted.join(", ")}</span>.</>
        )}
      </div>
    </div>
  );
}
