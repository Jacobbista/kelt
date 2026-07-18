import React, { useState } from "react";
import { describePod, scaleDeployment } from "../api";

const NF_LABELS = {
  amf: "AMF",
  smf: "SMF",
  upf: "UPF",
  nrf: "NRF",
  udm: "UDM",
  udr: "UDR",
  ausf: "AUSF",
  pcf: "PCF",
  bsf: "BSF",
  nssf: "NSSF",
  mongodb: "MongoDB",
  gnb: "gNB",
  ue: "UE",
  unknown: "Other",
};

function phaseColor(phase) {
  if (phase === "Running") return { dot: "bg-emerald-400", text: "text-emerald-400" };
  if (phase === "Pending" || phase === "ContainerCreating") return { dot: "bg-amber-400 animate-pulse", text: "text-amber-400" };
  if (phase === "Terminating") return { dot: "bg-slate-500 animate-pulse", text: "text-slate-400" };
  if (phase === "Succeeded") return { dot: "bg-sky-400", text: "text-sky-400" };
  return { dot: "bg-rose-400", text: "text-rose-400" };
}

function timeSince(isoString) {
  if (!isoString) return "—";
  const diff = Date.now() - new Date(isoString).getTime();
  if (diff < 0) return "just now";
  const secs = Math.floor(diff / 1000);
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ${mins % 60}m`;
  const days = Math.floor(hours / 24);
  return `${days}d ${hours % 24}h`;
}

// canWrite=false (a read-only account) hides the actions whose backend routers
// are admin-only — restart, exec terminal, image rollout — instead of leaving
// buttons that answer 403. Logs stay: log streaming is a viewer capability.
export default function NfCard({ nf, onRestart, onOpenLogs, onOpenTerminal, onOpenIperf3Logs, expanded, onToggle, isRestarting, versionInfo, onUpdate, canWrite = true }) {
  const [confirmRestart, setConfirmRestart] = useState(false);
  const [details, setDetails] = useState(null);
  const [detailsLoading, setDetailsLoading] = useState(false);
  const [showScale, setShowScale] = useState(false);
  const [scaleTarget, setScaleTarget] = useState(nf.ready_replicas ?? 1);
  const [scaling, setScaling] = useState(false);
  const label = NF_LABELS[nf.nf_type] || nf.nf_type.toUpperCase();
  const { dot, text } = phaseColor(nf.phase);
  const isTerminating = nf.phase === "Terminating";
  const showRestarting = !isTerminating && (isRestarting || nf.phase === "Pending" || nf.phase === "ContainerCreating");

  return (
    <div className={`rounded-lg border transition-colors ${
      isTerminating
        ? "border-slate-700/50 bg-slate-950 opacity-60"
        : showRestarting
          ? "border-amber-600/40 bg-slate-900"
          : nf.phase === "Running"
            ? "border-slate-700 bg-slate-900 hover:border-slate-600"
            : "border-rose-700/40 bg-slate-900"
    }`}>
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center gap-3 px-4 py-3 text-left"
      >
        <span className={`h-2.5 w-2.5 flex-shrink-0 rounded-full ${dot}`} />
        <span className="min-w-[56px] text-sm font-semibold text-white">{label}</span>
        <span className={`text-xs ${text}`}>{nf.phase}</span>
        {showRestarting && (
          <span className="rounded bg-amber-900/40 px-1.5 py-0.5 text-[10px] font-medium text-amber-300 animate-pulse">
            restarting...
          </span>
        )}
        {versionInfo && versionInfo.deployed && (
          versionInfo.up_to_date ? (
            <span
              className="rounded bg-emerald-900/30 px-1.5 py-0.5 text-[10px] font-mono text-emerald-400"
              title={versionInfo.deployed_image}
            >
              {versionInfo.deployed_tag}
            </span>
          ) : versionInfo.available_tag && canWrite ? (
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); onUpdate?.(versionInfo.available_tag); }}
              className="rounded bg-amber-900/40 px-1.5 py-0.5 text-[10px] font-mono text-amber-300 hover:bg-amber-800/50"
              title={`Update available: ${versionInfo.available_image}`}
            >
              {versionInfo.deployed_tag} → {versionInfo.available_tag}
            </button>
          ) : (
            <span
              className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] font-mono text-slate-500"
              title={versionInfo.deployed_image}
            >
              {versionInfo.deployed_tag}
            </span>
          )
        )}
        <span className="ml-auto flex items-center gap-3 text-xs text-slate-500">
          <span className="font-mono text-slate-400">{nf.pod_ip || "—"}</span>
          <span>{nf.node || ""}</span>
          {nf.restarts > 0 && (() => {
            const ageMs = nf.start_time ? Date.now() - new Date(nf.start_time).getTime() : Infinity;
            const recent = ageMs < 3600_000; // pod started < 1h ago → restarts are recent
            return (
              <span
                className={`rounded px-1.5 py-0.5 ${recent ? "bg-rose-900/40 text-rose-400" : "bg-slate-700/40 text-slate-400"}`}
                title={`${nf.restarts} restarts — pod up since ${timeSince(nf.start_time)}`}
              >
                {nf.restarts}x
              </span>
            );
          })()}
          <span className="tabular-nums">{timeSince(nf.start_time)}</span>
          <span className="text-slate-600">{expanded ? "\u25B2" : "\u25BC"}</span>
        </span>
      </button>

      {expanded && (
        <div className="border-t border-slate-800 px-4 py-3">
          <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-xs mb-3">
            <div>
              <span className="text-slate-500">Pod</span>
              <div className="text-slate-300 font-mono text-[11px] mt-0.5">{nf.name}</div>
            </div>
            <div>
              <span className="text-slate-500">Deployment</span>
              <div className="text-slate-300 font-mono text-[11px] mt-0.5">{nf.deployment || "—"}</div>
            </div>
            <div>
              <span className="text-slate-500">Uptime</span>
              <div className="text-slate-300 mt-0.5">{timeSince(nf.start_time)}</div>
            </div>
            <div>
              <span className="text-slate-500">Containers</span>
              <div className="text-slate-300 mt-0.5">{nf.containers?.join(", ") || "—"}</div>
            </div>
          </div>

          <div className="flex items-center gap-2">
            {isTerminating ? (
              <span className="text-xs text-slate-500 italic">Pod shutting down...</span>
            ) : (
              <>
                {canWrite && nf.deployment && !confirmRestart && (
                  <button
                    type="button"
                    disabled={isRestarting}
                    onClick={(e) => { e.stopPropagation(); setConfirmRestart(true); }}
                    className="rounded bg-amber-600/20 px-3 py-1.5 text-xs font-medium text-amber-300 hover:bg-amber-600/30 transition-colors disabled:opacity-40"
                  >
                    {isRestarting ? "Restarting..." : "Restart"}
                  </button>
                )}

                {canWrite && confirmRestart && (
                  <div className="flex items-center gap-2 rounded border border-amber-600/40 bg-amber-950/30 px-3 py-1.5">
                    <span className="text-xs text-amber-300">Restart {label}?</span>
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        setConfirmRestart(false);
                        onRestart?.(nf);
                      }}
                      className="rounded bg-amber-600 px-2 py-0.5 text-xs font-medium text-white hover:bg-amber-500 transition-colors"
                    >
                      Confirm
                    </button>
                    <button
                      type="button"
                      onClick={(e) => { e.stopPropagation(); setConfirmRestart(false); }}
                      className="text-xs text-slate-400 hover:text-white transition-colors"
                    >
                      Cancel
                    </button>
                  </div>
                )}

                <button
                  type="button"
                  onClick={(e) => { e.stopPropagation(); onOpenLogs?.(nf); }}
                  className="rounded bg-indigo-600/20 px-3 py-1.5 text-xs font-medium text-indigo-300 hover:bg-indigo-600/30 transition-colors"
                >
                  Logs
                </button>

                {canWrite && (
                <button
                  type="button"
                  onClick={(e) => { e.stopPropagation(); onOpenTerminal?.(nf); }}
                  className="rounded bg-emerald-600/20 px-3 py-1.5 text-xs font-medium text-emerald-300 hover:bg-emerald-600/30 transition-colors"
                >
                  Terminal
                </button>
                )}

                {nf.nf_type === "upf" && (
                  <button
                    type="button"
                    onClick={(e) => { e.stopPropagation(); onOpenIperf3Logs?.(nf); }}
                    className="rounded bg-cyan-600/20 px-3 py-1.5 text-xs font-medium text-cyan-300 hover:bg-cyan-600/30 transition-colors"
                  >
                    iperf3 Server
                  </button>
                )}

                {nf.deployment && (
                  showScale ? (
                    <div className="flex items-center gap-1.5 rounded border border-slate-700 bg-slate-800/50 px-2 py-1" onClick={(e) => e.stopPropagation()}>
                      <button
                        type="button"
                        onClick={() => setScaleTarget(Math.max(0, scaleTarget - 1))}
                        className="rounded bg-slate-700 px-1.5 py-0.5 text-xs text-slate-300 hover:bg-slate-600"
                      >−</button>
                      <span className="min-w-[20px] text-center text-xs font-mono text-slate-200">{scaleTarget}</span>
                      <button
                        type="button"
                        onClick={() => setScaleTarget(Math.min(10, scaleTarget + 1))}
                        className="rounded bg-slate-700 px-1.5 py-0.5 text-xs text-slate-300 hover:bg-slate-600"
                      >+</button>
                      <button
                        type="button"
                        disabled={scaling}
                        onClick={async () => {
                          setScaling(true);
                          try {
                            await scaleDeployment(nf.deployment, scaleTarget);
                          } catch {} finally {
                            setScaling(false);
                            setShowScale(false);
                          }
                        }}
                        className="rounded bg-indigo-600 px-2 py-0.5 text-xs font-medium text-white hover:bg-indigo-500 disabled:opacity-40"
                      >{scaling ? "..." : "Apply"}</button>
                      <button
                        type="button"
                        onClick={() => setShowScale(false)}
                        className="text-xs text-slate-500 hover:text-slate-300"
                      >✕</button>
                    </div>
                  ) : (
                    <button
                      type="button"
                      onClick={(e) => { e.stopPropagation(); setScaleTarget(nf.ready_replicas ?? 1); setShowScale(true); }}
                      className="rounded bg-slate-700/60 px-3 py-1.5 text-xs font-medium text-slate-300 hover:bg-slate-700 transition-colors"
                    >
                      Scale
                    </button>
                  )
                )}

                <button
                  type="button"
                  disabled={detailsLoading}
                  onClick={async (e) => {
                    e.stopPropagation();
                    if (details) { setDetails(null); return; }
                    setDetailsLoading(true);
                    try {
                      setDetails(await describePod(nf.name));
                    } catch (err) {
                      setDetails({ _error: String(err.message || err) });
                    } finally {
                      setDetailsLoading(false);
                    }
                  }}
                  className="rounded bg-slate-700/60 px-3 py-1.5 text-xs font-medium text-slate-300 hover:bg-slate-700 transition-colors disabled:opacity-40"
                >
                  {detailsLoading ? "Loading..." : details ? "Hide Details" : "Describe"}
                </button>
              </>
            )}
          </div>

          {details && !details._error && <PodDetails data={details} />}
          {details?._error && (
            <div className="mt-2 rounded border border-rose-700/40 bg-rose-950/30 p-2 text-xs text-rose-300">{details._error}</div>
          )}
        </div>
      )}
    </div>
  );
}

function PodDetails({ data }) {
  return (
    <div className="mt-3 space-y-3 border-t border-slate-800 pt-3">
      {/* Init containers */}
      {data.init_containers?.length > 0 && (
        <div>
          <div className="text-[10px] font-semibold text-slate-500 uppercase tracking-wide mb-1">Init Containers</div>
          {data.init_containers.map((c) => (
            <div key={c.name} className="rounded bg-slate-950 p-2 mb-1 text-xs">
              <div className="flex items-center gap-2">
                <span className={`h-2 w-2 rounded-full ${c.state === "running" ? "bg-emerald-500" : c.state === "waiting" ? "bg-amber-500 animate-pulse" : "bg-slate-500"}`} />
                <span className="font-mono text-slate-200">{c.name}</span>
                <span className="text-slate-500">{c.state}</span>
                {c.reason && <span className="text-amber-400">{c.reason}</span>}
              </div>
              {c.message && <div className="mt-1 text-slate-400 break-words">{c.message}</div>}
            </div>
          ))}
        </div>
      )}

      {/* Containers */}
      {data.containers?.length > 0 && (
        <div>
          <div className="text-[10px] font-semibold text-slate-500 uppercase tracking-wide mb-1">Containers</div>
          {data.containers.map((c) => (
            <div key={c.name} className="rounded bg-slate-950 p-2 mb-1 text-xs">
              <div className="flex items-center gap-2">
                <span className={`h-2 w-2 rounded-full ${c.ready ? "bg-emerald-500" : c.state === "waiting" ? "bg-amber-500 animate-pulse" : "bg-slate-500"}`} />
                <span className="font-mono text-slate-200">{c.name}</span>
                <span className="text-slate-500">{c.state}</span>
                {c.reason && <span className="text-amber-400">{c.reason}</span>}
                {c.restart_count > 0 && <span className="text-rose-400">{c.restart_count}x restarts</span>}
              </div>
              {c.message && <div className="mt-1 text-slate-400 break-words">{c.message}</div>}
            </div>
          ))}
        </div>
      )}

      {/* Conditions */}
      {data.conditions?.length > 0 && (
        <div>
          <div className="text-[10px] font-semibold text-slate-500 uppercase tracking-wide mb-1">Conditions</div>
          <div className="grid grid-cols-2 gap-1">
            {data.conditions.map((c) => (
              <div key={c.type} className="flex items-center gap-2 text-xs">
                <span className={`h-2 w-2 rounded-full ${c.status === "True" ? "bg-emerald-500" : "bg-slate-600"}`} />
                <span className="text-slate-300">{c.type}</span>
                {c.reason && <span className="text-slate-500">{c.reason}</span>}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Network annotations */}
      {data.networks_requested && (
        <div>
          <div className="text-[10px] font-semibold text-slate-500 uppercase tracking-wide mb-1">Networks Requested</div>
          <pre className="rounded bg-slate-950 p-2 text-[11px] font-mono text-slate-400 overflow-x-auto whitespace-pre-wrap">{data.networks_requested}</pre>
        </div>
      )}

      {/* Events */}
      {data.events?.length > 0 && (
        <div>
          <div className="text-[10px] font-semibold text-slate-500 uppercase tracking-wide mb-1">Events</div>
          <div className="max-h-48 overflow-y-auto space-y-1">
            {data.events.map((e, i) => (
              <div key={i} className="rounded bg-slate-950 px-2 py-1.5 text-xs">
                <div className="flex items-center gap-2">
                  <span className={`rounded px-1 py-0.5 text-[10px] font-medium ${e.type === "Warning" ? "bg-amber-900/40 text-amber-300" : "bg-slate-800 text-slate-400"}`}>
                    {e.type}
                  </span>
                  <span className="font-medium text-slate-200">{e.reason}</span>
                  {e.count > 1 && <span className="text-slate-500">x{e.count}</span>}
                  <span className="ml-auto text-[10px] text-slate-600">{e.source}</span>
                </div>
                <div className="mt-0.5 text-slate-400 break-words">{e.message}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
