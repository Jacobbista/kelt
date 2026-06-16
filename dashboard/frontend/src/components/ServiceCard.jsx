import { Link } from "react-router-dom";
import { IconArrowRight } from "./icons";

const PHASE_DOT = {
  Running: "bg-emerald-400",
  Pending: "bg-amber-400 animate-pulse",
  ContainerCreating: "bg-amber-400 animate-pulse",
  Terminating: "bg-slate-500 animate-pulse",
  Succeeded: "bg-sky-400",
};
const phaseDot = (p) => PHASE_DOT[p] || "bg-rose-400";

function StatusPill({ status }) {
  if (status === "on") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-900/40 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-emerald-300">
        <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" /> live
      </span>
    );
  }
  if (status === "off") {
    return (
      <span className="rounded-full bg-slate-800 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-slate-400">off</span>
    );
  }
  if (status === "planned") {
    return (
      <span className="rounded-full bg-indigo-900/30 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-indigo-300">planned</span>
    );
  }
  return null;
}

/**
 * A domain card on the Services hub. `to` makes the whole card a link; planned
 * cards render dimmed and non-interactive. Plain card styling consistent with
 * NfCard, the house idiom for an ops dashboard.
 */
export default function ServiceCard({ icon: Icon, title, subtitle, status = "planned", stats = [], statusDots = [], to, cta = "manage" }) {
  const planned = status === "planned";
  const Wrapper = to && !planned ? Link : "div";
  const wrapperProps = to && !planned ? { to } : {};

  return (
    <Wrapper
      {...wrapperProps}
      className={`svc-fade group block rounded-lg border bg-slate-900 p-5 transition-colors ${
        planned ? "border-slate-800 opacity-60" : "cursor-pointer border-slate-700 hover:border-slate-600"
      }`}
    >
      <div className="flex items-start gap-3">
        <span className="svc-badge">
          <Icon size={20} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="truncate text-sm font-semibold text-slate-100">{title}</h3>
            <StatusPill status={status} />
          </div>
          <p className="mt-0.5 text-xs text-slate-400">{subtitle}</p>
        </div>
      </div>

      {statusDots.length > 0 && (
        <div className="mt-4 flex flex-wrap items-center gap-1.5">
          {statusDots.map((p, i) => (
            <span key={i} title={p} className={`h-2 w-2 rounded-full ${phaseDot(p)}`} />
          ))}
        </div>
      )}

      {stats.length > 0 && (
        <div className="mt-4 flex flex-wrap gap-x-6 gap-y-1">
          {stats.map((s) => (
            <div key={s.label} className="leading-tight">
              <div className="text-base font-semibold tabular-nums text-slate-100">{s.value}</div>
              <div className="text-[10px] uppercase tracking-wide text-slate-500">{s.label}</div>
            </div>
          ))}
        </div>
      )}

      <div className="mt-4 flex items-center justify-end">
        {planned ? (
          <span className="text-[11px] text-slate-500">coming soon</span>
        ) : (
          <span className="inline-flex items-center gap-1 text-xs font-medium text-sky-300 transition-transform group-hover:translate-x-0.5">
            {cta} <IconArrowRight size={15} />
          </span>
        )}
      </div>
    </Wrapper>
  );
}
