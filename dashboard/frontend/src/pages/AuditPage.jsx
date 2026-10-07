import React, { useState } from "react";
import { getAudit } from "../api";
import useResource from "../data/useResource";
import { Card } from "../components/Card";

// Settings -> Audit: who signed in, and who changed what. Keycloak's events
// (sign-ins, failures, admin changes) and the dashboard's own record of
// actions, as one list, newest first (backend app/services/audit.py).
const KINDS = [
  { id: "all", label: "All" },
  { id: "access", label: "Sign-ins" },
  { id: "changes", label: "Changes" },
  { id: "failures", label: "Failures" },
];
const PERIODS = [
  { days: 1, label: "24 h" },
  { days: 7, label: "7 days" },
  { days: 30, label: "30 days" },
];

const when = (iso) => {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return { day: "—", time: "" };
  return {
    day: d.toLocaleDateString([], { day: "2-digit", month: "short" }),
    time: d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }),
  };
};

function Pills({ items, value, onChange, keyOf, label }) {
  return (
    <div role="group" aria-label={label} className="inline-flex rounded-md bg-slate-800/70 p-0.5">
      {items.map((it) => {
        const k = keyOf(it);
        const on = k === value;
        return (
          <button
            key={k}
            type="button"
            aria-pressed={on}
            onClick={() => onChange(k)}
            className={`rounded px-2.5 py-1 text-xs transition-colors ${on ? "bg-slate-700 font-medium text-white" : "text-slate-400 hover:text-slate-200"}`}
          >
            {it.label}
          </button>
        );
      })}
    </div>
  );
}

function Row({ r }) {
  const [open, setOpen] = useState(false);
  const t = when(r.ts);
  const failed = r.outcome === "failed";
  return (
    <>
      <tr
        onClick={() => setOpen(!open)}
        className="cursor-pointer border-b border-slate-800/70 align-top transition-colors hover:bg-slate-800/40"
        aria-expanded={open}
      >
        <td className="whitespace-nowrap px-4 py-2 font-mono text-xs tabular-nums text-slate-400">
          <span className="text-slate-500">{t.day}</span> {t.time}
        </td>
        <td className="truncate px-3 py-2 text-xs text-slate-200" title={r.who}>{r.who}</td>
        <td className="truncate px-3 py-2 text-xs" title={r.action}>
          <span className={failed ? "text-rose-300" : r.kind === "access" ? "text-sky-300" : "text-slate-200"}>{r.action}</span>
        </td>
        <td className="px-3 py-2 text-[11px] text-slate-500">{r.source === "keycloak" ? "Keycloak" : "Dashboard"}</td>
        <td className="px-4 py-2 text-right font-mono text-[11px] text-slate-500">{r.ip || ""}</td>
      </tr>
      {open && (
        <tr className="border-b border-slate-800/70 bg-slate-950/40">
          <td />
          <td colSpan={4} className="px-3 py-2.5">
            {Object.keys(r.meta || {}).length ? (
              <dl className="grid grid-cols-[7rem_minmax(0,1fr)] gap-x-4 gap-y-1 text-[11px]">
                {Object.entries(r.meta).map(([k, v]) => (
                  <React.Fragment key={k}>
                    <dt className="text-slate-500">{k}</dt>
                    <dd className="break-all font-mono text-slate-300">{String(v)}</dd>
                  </React.Fragment>
                ))}
                {r.ip && (
                  <>
                    <dt className="text-slate-500">from</dt>
                    <dd className="font-mono text-slate-300">{r.ip} <span className="font-sans text-slate-500">(public address as the edge saw it)</span></dd>
                  </>
                )}
              </dl>
            ) : (
              <p className="text-[11px] text-slate-500">No further detail.</p>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

export default function AuditPage() {
  const [kind, setKind] = useState("all");
  const [days, setDays] = useState(7);
  const r = useResource(`audit:${kind}:${days}`, () => getAudit(kind, days), { every: 30000 });
  const rows = r.data?.rows || [];
  const missing = r.data?.missing || [];
  return (
    <div className="svc-fade grid max-w-5xl items-start gap-3">
      <div className="flex flex-wrap items-center gap-3">
        <Pills label="What" items={KINDS} value={kind} onChange={setKind} keyOf={(k) => k.id} />
        <Pills label="Period" items={PERIODS} value={days} onChange={setDays} keyOf={(p) => p.days} />
        <span className="text-[11px] text-slate-500">
          Kept for {r.data?.retention_days ?? 30} days. Successful session renewals are not recorded.
        </span>
      </div>
      <Card
        title="Audit"
        sub={r.data ? `${rows.length}${rows.length === 500 ? "+" : ""} entries` : "Sign-ins and changes"}
        updatedAt={r.updatedAt}
        busy={!r.data && !r.error}
        error={r.error ? String(r.error.message || r.error) : null}
      >
        {missing.map((m) => (
          <div key={m.source} className="border-b border-slate-800 px-4 py-1.5 text-[11px] text-amber-300">
            {m.source === "keycloak" ? "Keycloak did not answer: sign-ins and identity changes are missing." : `${m.source} is missing.`}
          </div>
        ))}
        {r.data && rows.length === 0 ? (
          <p className="px-4 py-6 text-center text-xs text-slate-500">Nothing recorded in this period.</p>
        ) : (
          <div className="overflow-x-auto">
            {/* Fixed columns: opening a long detail never moves them. */}
            <table className="w-full table-fixed text-left">
              <colgroup>
                <col className="w-44" />
                <col className="w-32" />
                <col />
                <col className="w-28" />
                <col className="w-36" />
              </colgroup>
              <thead>
                <tr className="border-b border-slate-800 text-[10px] uppercase tracking-wider text-slate-500">
                  <th className="px-4 py-2 font-medium">When</th>
                  <th className="px-3 py-2 font-medium">Who</th>
                  <th className="px-3 py-2 font-medium">What</th>
                  <th className="px-3 py-2 font-medium">Source</th>
                  <th className="px-4 py-2 text-right font-medium">From</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row, i) => <Row key={`${row.ts}-${i}`} r={row} />)}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
