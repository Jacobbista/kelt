import React, { useEffect, useState } from "react";
import { getIsolationPlanes, getIsolationPolicies, getIsolationSamples, getIsolationTargets } from "../api";
import PlaneMatrix from "../components/isolation/PlaneMatrix";
import BlockedSamples from "../components/isolation/BlockedSamples";
import NamespacePolicies from "../components/isolation/NamespacePolicies";
import FlowCheck from "../components/isolation/FlowCheck";

// What may talk to what, on the 5G planes (plane filter, phase 04) and on the
// pod network (NetworkPolicies, phase 13), and what was stopped. Read-only: the
// rules change in the playbooks, named at the bottom of the page.
// See docs/architecture/plane-isolation.md and namespaces.md.

function useLoad(fn) {
  const [state, setState] = useState({ data: null, error: "" });
  useEffect(() => {
    let alive = true;
    fn().then((data) => alive && setState({ data, error: "" }))
      .catch((e) => alive && setState({ data: null, error: e.message || "Request failed" }));
    return () => { alive = false; };
  }, [fn]);
  return state;
}

function Chip({ value, label, tone = "text-slate-100", warn = false }) {
  return (
    <div className={`flex items-center gap-2 rounded-md border bg-slate-900 px-3 py-1.5 ${warn ? "border-amber-500/40" : "border-slate-700"}`}>
      <span className={`text-base font-bold tabular-nums ${tone}`}>{value ?? "–"}</span>
      <span className="text-[10px] uppercase tracking-wider text-slate-500">{label}</span>
    </div>
  );
}

function SectionHead({ title, sub, enforce }) {
  return (
    <div className="mb-1 flex flex-wrap items-baseline gap-2.5">
      <h3 className="text-sm font-semibold text-slate-100">{title}</h3>
      {enforce && (
        <span className={`rounded-full px-2 py-0.5 text-[9.5px] font-semibold uppercase tracking-wider ${
          enforce === "enforce" ? "bg-emerald-500/15 text-emerald-300" : "bg-amber-500/15 text-amber-300"
        }`}>{enforce}</span>
      )}
      {sub && <span className="font-mono text-[11px] text-slate-500">{sub}</span>}
    </div>
  );
}

function Unavailable({ what, reason }) {
  return <div className="rounded-lg border border-slate-700 bg-slate-900 px-4 py-3 text-xs text-slate-400">{what} unavailable: {reason}</div>;
}

const loadPlanes = () => getIsolationPlanes("24h");
const loadSamples = () => getIsolationSamples(5);

export default function IsolationPage() {
  const planes = useLoad(loadPlanes);
  const samples = useLoad(loadSamples);
  const policies = useLoad(getIsolationPolicies);
  const targets = useLoad(getIsolationTargets);

  const p = planes.data;
  const pol = policies.data;
  const allowed = p?.available ? p.pairs.filter((x) => x.verdict === "allowed").length : null;
  const blocked = p?.available ? p.blocked_total : null;
  const isolated = pol ? pol.namespaces.length : null;
  const limited = pol ? pol.namespaces.filter((n) => n.egress.limited).length : null;

  return (
    <div className="flex max-w-6xl flex-col gap-7">
      <header>
        <p className="max-w-3xl text-xs leading-relaxed text-slate-400">
          What may talk to what, on the 5G planes and on the pod network, and what was stopped. Read-only: the
          rules come from phases 04 and 13.
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          <Chip value={allowed} label="Allowed crossings" tone="text-emerald-400" />
          <Chip value={blocked} label={p?.mode === "observe" ? "Not allowed, 24 h" : "Blocked, 24 h"} tone={blocked ? "text-amber-300" : "text-emerald-400"} warn={blocked > 0} />
          <Chip value={isolated} label="Namespaces isolated" tone="text-emerald-400" />
          <Chip value={limited} label="With limited egress" tone={limited ? "text-amber-300" : "text-slate-100"} />
        </div>
      </header>

      <section>
        <SectionHead title="5G planes" enforce={p?.mode} sub="nftables inet kelt_planes · worker" />
        <p className="mb-3 max-w-3xl text-xs leading-relaxed text-slate-400">
          Each plane is its own bridge on the worker. Traffic may cross only where the matrix is green: the radio
          network to N2 and N3, and N6c out to the internet. Everything else is{" "}
          {p?.mode === "observe" ? "let through but counted (observe mode)" : "dropped and counted"} where it was
          headed. What comes from outside the planes is listed beside the matrix.
        </p>
        {planes.error ? (
          <Unavailable what="Plane counters" reason={planes.error} />
        ) : !p ? (
          <div className="h-72 animate-pulse rounded-lg bg-slate-900" />
        ) : !p.available ? (
          <Unavailable what="Plane counters" reason={p.reason} />
        ) : (
          <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_22rem]">
            <div className="rounded-lg border border-slate-700 bg-slate-900 p-4">
              <div className="mb-2 flex items-baseline justify-between text-[10px] font-semibold uppercase tracking-wider text-slate-500">
                <span>From → to</span><span className="normal-case tracking-normal text-slate-600">packets, last {p.window}</span>
              </div>
              <PlaneMatrix planes={p} />
            </div>
            <div className="overflow-hidden rounded-lg border border-slate-700 bg-slate-900">
              <div className="flex items-baseline justify-between border-b border-slate-800 px-4 py-2.5 text-[10px] font-semibold uppercase tracking-wider text-slate-500">
                <span>{p.mode === "observe" ? "Packets not allowed" : "Blocked packets"}</span><span className="normal-case tracking-normal text-slate-600">sampled, 6/min per rule</span>
              </div>
              <BlockedSamples mode={p.mode} outside={p.outside} window={p.window} samples={samples.error ? { available: false, reason: samples.error } : samples.data} />
            </div>
          </div>
        )}
      </section>

      <section>
        <SectionHead title="Pod network" enforce={pol?.enabled ? "enforce" : null} sub="NetworkPolicy · kube-router" />
        <p className="mb-3 max-w-3xl text-xs leading-relaxed text-slate-400">
          Every namespace below refuses incoming traffic unless a policy allows it. All of them accept their own
          pods, metrics scrapes from monitoring, and the nodes and management network (kubelet probes, NodePorts).
          The table shows what each one accepts on top of that.
        </p>
        {policies.error ? (
          <Unavailable what="Policies" reason={policies.error} />
        ) : !pol ? (
          <div className="h-64 animate-pulse rounded-lg bg-slate-900" />
        ) : (
          <NamespacePolicies policies={pol} />
        )}
      </section>

      <section>
        <SectionHead title="Check a flow" sub="evaluated on the live policies, nothing is sent" />
        <div className="mt-2">
          {targets.error ? (
            <Unavailable what="Flow check" reason={targets.error} />
          ) : !targets.data ? (
            <div className="h-32 animate-pulse rounded-lg bg-slate-900" />
          ) : (
            <FlowCheck targets={targets.data} />
          )}
        </div>
      </section>

      <div className="rounded-lg border border-dashed border-slate-700 px-4 py-3 text-xs leading-relaxed text-slate-400">
        <span className="font-semibold text-slate-200">Where this is changed.</span> Plane crossings:{" "}
        <code className="font-mono text-[11px] text-indigo-300">ansible/phases/04-overlay-network/scripts/ovs-setup.sh</code>, mode in{" "}
        <code className="font-mono text-[11px] text-indigo-300">plane_filter_mode</code> (all.yml), then{" "}
        <code className="font-mono text-[11px] text-indigo-300">kelt run-phase 04-overlay-network</code>. Pod network flows: the table in{" "}
        <code className="font-mono text-[11px] text-indigo-300">ansible/phases/13-network-policies/roles/network_policies/defaults/main.yml</code>, then{" "}
        <code className="font-mono text-[11px] text-indigo-300">kelt run-phase 13-network-policies</code>.
      </div>
    </div>
  );
}
