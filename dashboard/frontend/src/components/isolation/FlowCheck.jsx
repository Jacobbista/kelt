import React, { useEffect, useMemo, useState } from "react";
import { checkIsolationFlow } from "../../api";
import { inputCls } from "../ui";

const INTERNET = "internet";
const destKey = (d) => `${d.namespace}/${d.service}:${d.port}`;

// Pick a source and a destination; the backend evaluates the live policies and
// answers with the verdict and the policy behind each step. Nothing is sent.
export default function FlowCheck({ targets }) {
  const destinations = useMemo(
    () => [...targets.destinations].sort((a, b) => destKey(a).localeCompare(destKey(b))),
    [targets.destinations],
  );
  const [source, setSource] = useState(() => (targets.sources.some((s) => s.id === "mec") ? "mec" : targets.sources[0]?.id));
  const [dest, setDest] = useState(() => {
    const mongo = destinations.find((d) => d.service === "mongodb" && d.port === 27017);
    return mongo ? destKey(mongo) : INTERNET;
  });
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!source) {
      setResult(null);
      setError("No source namespace to check from: no isolated namespace was found.");
      return undefined;
    }
    let alive = true;
    const d = destinations.find((x) => destKey(x) === dest);
    const destination = dest === INTERNET || !d
      ? { internet: true }
      : { namespace: d.namespace, service: d.service, port: d.port, protocol: d.protocol };
    setBusy(true);
    setError("");
    checkIsolationFlow(source, destination)
      .then((r) => { if (alive) setResult(r); })
      .catch((e) => { if (alive) { setResult(null); setError(e.message || "Check failed"); } })
      .finally(() => { if (alive) setBusy(false); });
    return () => { alive = false; };
  }, [source, dest, destinations]);

  const byNamespace = useMemo(() => {
    const groups = {};
    for (const d of destinations) (groups[d.namespace] ||= []).push(d);
    return groups;
  }, [destinations]);

  return (
    <div className="grid gap-4 md:grid-cols-2">
      <div className="grid content-start gap-3 rounded-lg border border-slate-700 bg-slate-900 p-4">
        <label className="grid gap-1 text-[10px] font-semibold uppercase tracking-wider text-slate-500" htmlFor="flow-src">
          From
          <select id="flow-src" className={`${inputCls} py-1.5 text-xs normal-case tracking-normal`} value={source ?? ""} onChange={(e) => setSource(e.target.value)}>
            {targets.sources.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
          </select>
        </label>
        <p className="-mt-1 text-[10.5px] leading-snug text-slate-500">
          A namespace stands for a pod of it with no labels: a policy that picks source pods by label is not applied.
        </p>
        <div className="-my-1 text-center text-slate-600">↓</div>
        <label className="grid gap-1 text-[10px] font-semibold uppercase tracking-wider text-slate-500" htmlFor="flow-dst">
          To
          <select id="flow-dst" className={`${inputCls} py-1.5 text-xs normal-case tracking-normal`} value={dest} onChange={(e) => setDest(e.target.value)}>
            {Object.entries(byNamespace).map(([ns, list]) => (
              <optgroup key={ns} label={ns}>
                {list.map((d) => (
                  <option key={destKey(d)} value={destKey(d)}>{d.service} :{d.port}{d.protocol !== "TCP" ? `/${d.protocol}` : ""}</option>
                ))}
              </optgroup>
            ))}
            <option value={INTERNET}>Internet (a public address)</option>
          </select>
        </label>
      </div>

      <div className={`rounded-lg border border-slate-700 bg-slate-900 p-4 transition-opacity ${busy ? "opacity-60" : ""}`} aria-live="polite">
        {error ? (
          <div className="text-xs text-rose-300">{error}</div>
        ) : !result ? (
          <div className="h-24 animate-pulse rounded bg-slate-800/60" />
        ) : (
          <div className="grid gap-3">
            <span className={`w-fit rounded-md px-2.5 py-1 text-[11px] font-bold uppercase tracking-wider ${
              result.verdict === "passes" ? "bg-emerald-500/15 text-emerald-300" : "bg-rose-500/15 text-rose-300"
            }`}>
              {result.verdict === "passes" ? "Passes" : "Blocked"}
            </span>
            <ul className="grid gap-2">
              {result.steps.map((s) => (
                <li key={s.side} className="grid grid-cols-[1rem_1fr] gap-2 text-xs leading-relaxed text-slate-300">
                  <span className={`font-bold ${s.result === "ok" ? "text-emerald-400" : s.result === "no" ? "text-rose-400" : "text-slate-600"}`}>
                    {s.result === "ok" ? "✓" : s.result === "no" ? "✕" : "–"}
                  </span>
                  <span>
                    {s.text}
                    {s.policy && <span className="block font-mono text-[10px] text-slate-500">{s.policy}</span>}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}
