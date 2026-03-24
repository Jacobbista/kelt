import React, { useEffect, useState, useCallback, useRef } from "react";
import {
  getUeSummary,
  getUeEvents,
  getActiveUes,
  getUeGnbs,
  getUePods,
  getRanStatus,
  runUePing,
  runUeIperf,
} from "../api";
import Loader from "../components/Loader";

/* ── helpers ─────────────────────────────────────────────────── */

function StatCard({ label, value, sub, color = "text-indigo-300", onClick }) {
  return (
    <div
      className={`rounded-lg border border-slate-700 bg-slate-900 p-4${onClick ? " cursor-pointer hover:border-slate-600" : ""}`}
      onClick={onClick}
    >
      <div className="text-xs text-slate-400 mb-1">{label}</div>
      <div className={`text-3xl font-bold tabular-nums ${color}`}>{value}</div>
      {sub && <div className="mt-1 text-[11px] text-slate-500">{sub}</div>}
    </div>
  );
}

const DISCONNECT_TYPES = ["gnb_disconnect", "ue_context_release", "pdu_session_rel", "ue_removed", "session_removed"];
const ERROR_TYPES = ["registration_fail", "auth_reject"];

function EventIcon({ ev }) {
  if (ev.type === "auth_reject") {
    return <span className="inline-block h-2.5 w-2.5 rounded-full bg-rose-500" title="Auth rejected" />;
  }
  if (ev.type === "ue_context_release" || DISCONNECT_TYPES.includes(ev.type)) {
    return <span className="inline-block h-2 w-2 rounded-full bg-rose-400" title="Disconnect" />;
  }
  if (ev.type === "pdu_session_rel") {
    return <span className="inline-block h-2 w-2 rounded-full bg-amber-400" title="Session released" />;
  }
  const cls =
    ev.severity === "warning"
      ? "bg-amber-400"
      : ev.severity === "error"
        ? "bg-rose-400"
        : "bg-emerald-400";
  return <span className={`inline-block h-2 w-2 rounded-full ${cls}`} />;
}

function formatTs(ts) {
  if (!ts) return "-";
  try {
    const d = new Date(ts);
    if (isNaN(d)) return ts.slice(11, 19) || ts;
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch {
    return ts;
  }
}

function relativeTime(ts) {
  if (!ts) return "-";
  try {
    const now = new Date();
    const then = new Date(ts);
    const diffSec = Math.floor((now - then) / 1000);
    if (diffSec < 0) return "just now";
    if (diffSec < 60) return `${diffSec}s ago`;
    if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
    return `${Math.floor(diffSec / 3600)}h ago`;
  } catch {
    return ts;
  }
}

function eventKey(ev, i) {
  return `${ev.ts || ""}-${ev.type || ""}-${ev.imsi || ev.gnb_ip || i}`;
}

const STATUS_STYLES = {
  registered: "bg-emerald-900/40 text-emerald-400",
  deregistered: "bg-slate-800 text-slate-500",
  released: "bg-amber-900/30 text-amber-400",
  stale: "bg-rose-900/20 text-rose-400 italic",
};

const EVENT_FILTERS = [
  { key: "all", label: "All" },
  { key: "connect", label: "Connect", types: ["gnb_connect", "registration_ok", "registration_req", "pdu_session_est", "pdu_request"] },
  { key: "disconnect", label: "Disconnect", types: [...DISCONNECT_TYPES, "registration_fail", "ue_count_change", "session_count_change"].filter((_, i, arr) => arr.indexOf(arr[i]) === i) },
  { key: "error", label: "Errors", types: ERROR_TYPES },
  { key: "count", label: "Counts", types: ["ue_count_change", "session_count_change"] },
];

/* ── main page ───────────────────────────────────────────────── */

export default function UEMonitoringPage() {
  const [summary, setSummary] = useState(null);
  const [events, setEvents] = useState([]);
  const [activeUes, setActiveUes] = useState([]);
  const [gnbs, setGnbs] = useState([]);
  const [uePods, setUePods] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [testPod, setTestPod] = useState("");
  const [testTarget, setTestTarget] = useState("8.8.8.8");
  const [testRunning, setTestRunning] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [ranStatus, setRanStatus] = useState(null);

  const [eventFilter, setEventFilter] = useState("all");
  const [expandedEvent, setExpandedEvent] = useState(null);
  const eventFeedRef = useRef(null);

  const refresh = useCallback(async () => {
    try {
      const [s, e, u, g, p] = await Promise.all([
        getUeSummary(),
        getUeEvents(10),
        getActiveUes(),
        getUeGnbs().catch(() => []),
        getUePods(),
      ]);
      setSummary(s);
      setEvents(e);
      setActiveUes(u);
      setGnbs(Array.isArray(g) ? g : []);
      setUePods(p);
      if (p.length > 0 && !testPod) setTestPod(p[0].name);
      setError(null);
      if ((s?.connected_gnbs ?? 0) === 0) {
        getRanStatus().then(setRanStatus).catch(() => setRanStatus(null));
      } else {
        setRanStatus(null);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [testPod]);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 8000);
    return () => clearInterval(id);
  }, [refresh]);

  async function handlePing() {
    setTestRunning(true);
    setTestResult(null);
    try {
      const r = await runUePing(testPod, testTarget);
      setTestResult({ type: "ping", ...r });
    } catch (err) {
      setTestResult({ type: "ping", exit_code: 1, stderr: err.message });
    } finally {
      setTestRunning(false);
    }
  }

  async function handleIperf() {
    setTestRunning(true);
    setTestResult(null);
    try {
      const r = await runUeIperf(testPod);
      setTestResult({ type: "iperf", ...r });
    } catch (err) {
      setTestResult({ type: "iperf", exit_code: 1, stderr: err.message });
    } finally {
      setTestRunning(false);
    }
  }

  function scrollToEvents(filter) {
    setEventFilter(filter);
    eventFeedRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  if (loading) {
    return (
      <div className="flex min-h-[200px] items-center justify-center p-6">
        <Loader size="lg" label="Loading UE monitoring data..." />
      </div>
    );
  }

  const s = summary || {};

  const filteredEvents = eventFilter === "all"
    ? events
    : events.filter((ev) => {
        const filter = EVENT_FILTERS.find((f) => f.key === eventFilter);
        if (!filter?.types) return true;
        // For disconnect filter, also include warning-severity count changes
        if (eventFilter === "disconnect" && ev.severity === "warning") return true;
        return filter.types.includes(ev.type);
      });

  return (
    <div className="space-y-6 p-6">
      <h2 className="text-xl font-semibold text-white">UE Monitoring</h2>

      {error && (
        <div className="rounded border border-rose-700/40 bg-rose-950/30 p-3 text-sm text-rose-300">
          {error}
        </div>
      )}

      {/* Info when no gNBs - only if both Prometheus and infoAPI say 0 */}
      {(s.connected_gnbs ?? 0) === 0 && gnbs.length === 0 && ranStatus && (() => {
        const prereqsOk = ranStatus.amf_pod_ready && ranStatus.bridge_exists && ranStatus.amf_has_physical_ran;
        return (
          <div className={`rounded-lg border p-4 ${
            prereqsOk
              ? "border-slate-700/50 bg-slate-900/30"
              : "border-amber-800/50 bg-amber-950/20"
          }`}>
            <h3 className={`text-sm font-medium mb-2 ${prereqsOk ? "text-slate-300" : "text-amber-300"}`}>
              {prereqsOk ? "No gNB connected yet" : "No gNBs connected -- check prerequisites"}
            </h3>
            {prereqsOk ? (
              <p className="text-xs text-slate-400">
                Core and RAN infra are ready. If using a physical femtocell, ensure it is powered on and connected to the worker NIC.
              </p>
            ) : (
              <>
                <p className="text-xs text-slate-400 mb-3">If using a physical femtocell, verify:</p>
                <ul className="text-xs text-slate-400 space-y-1 mb-3">
                  <li>AMF pod: {ranStatus.amf_pod_ready ? "Running" : "Not ready"}</li>
                  <li>br-ran bridge: {ranStatus.bridge_exists ? "Exists" : "Missing"}</li>
                  <li>Worker NIC (in br-ran): {ranStatus.bridge_detected ? `${ranStatus.ran_interface_detected || "detected"}` : "Not found"}</li>
                  <li>AMF n2-physical: {ranStatus.amf_has_physical_ran ? "Enabled" : "Disabled -- click Enable in RAN Config"}</li>
                </ul>
                <p className="text-[11px] text-slate-500">
                  RAN page &rarr; Physical RAN: click <strong>Enable Physical</strong> to add n2-physical to AMF.
                  If the host interface changed (e.g. via hub), run{" "}
                  <code className="rounded bg-slate-800 px-1">PHYSICAL_RAN_BRIDGE=&lt;host_nic&gt; vagrant reload worker</code>.
                </p>
              </>
            )}
          </div>
        );
      })()}

      {/* Summary cards - use infoAPI count when Prometheus returns 0 */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard
          label="Connected gNBs"
          value={Math.max(s.connected_gnbs ?? 0, gnbs.length)}
          color={(s.connected_gnbs ?? 0) > 0 || gnbs.length > 0 ? "text-emerald-400" : "text-slate-500"}
        />
        <StatCard
          label="RAN UEs"
          value={s.ran_ues ?? 0}
          color={s.ran_ues > 0 ? "text-cyan-400" : "text-slate-500"}
        />
        <StatCard
          label="Active Sessions"
          value={s.amf_sessions ?? 0}
          color={s.amf_sessions > 0 ? "text-indigo-400" : "text-slate-500"}
        />
        <StatCard
          label="Registered Subscribers"
          value={s.registered_subscribers ?? 0}
          color={s.registered_subscribers > 0 ? "text-violet-400" : "text-slate-500"}
        />
      </div>

      {/* Registration counters */}
      <div className="rounded-lg border border-slate-700 bg-slate-900 p-4">
        <h3 className="text-sm font-medium text-slate-300 mb-3">Registration Activity (cumulative)</h3>
        <div className="grid grid-cols-3 gap-4 sm:grid-cols-6 text-center">
          <CounterCell label="Init Req" value={s.reg_init_req} />
          <CounterCell label="Init OK" value={s.reg_init_succ} ok />
          <CounterCell label="Init Fail" value={s.reg_init_fail} bad />
          <CounterCell label="Mobility Req" value={s.reg_mobility_req} />
          <CounterCell label="Mobility OK" value={s.reg_mobility_succ} ok />
          <div
            onClick={() => (s.auth_reject ?? 0) > 0 && scrollToEvents("error")}
            className={(s.auth_reject ?? 0) > 0 ? "cursor-pointer" : ""}
            title={(s.auth_reject ?? 0) > 0 ? "Click to see auth reject details in event feed" : ""}
          >
            <CounterCell label="Auth Reject" value={s.auth_reject} bad />
          </div>
        </div>
      </div>

      {/* Auth reject context banner */}
      {(s.auth_reject ?? 0) > 0 && (
        <div className="rounded border border-rose-800/40 bg-rose-950/20 px-3 py-2 text-xs text-rose-300">
          <strong>{s.auth_reject}</strong> authentication reject(s) detected.
          {" "}Check the event feed below for per-UE details, or verify subscriber K/OPc keys on the Subscribers page.
          <button
            onClick={() => scrollToEvents("error")}
            className="ml-2 underline text-rose-400 hover:text-rose-300"
          >
            View errors
          </button>
        </div>
      )}

      {/* Connected gNBs (from AMF infoAPI) */}
      {gnbs.length > 0 && (
        <div className="rounded-lg border border-slate-700 bg-slate-900 p-4">
          <h3 className="text-sm font-medium text-slate-300 mb-3">
            Connected gNBs
            <span className="ml-2 text-xs text-slate-500 font-normal">gnb_id, PLMN, peer</span>
          </h3>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-slate-700 text-left text-slate-400">
                  <th className="pb-2 pr-4">gNB ID</th>
                  <th className="pb-2 pr-4">PLMN</th>
                  <th className="pb-2 pr-4">Peer (SCTP)</th>
                  <th className="pb-2">UEs</th>
                </tr>
              </thead>
              <tbody>
                {gnbs.map((g, i) => (
                  <tr key={i} className="border-b border-slate-800">
                    <td className="py-2 pr-4 font-mono text-cyan-300">{g.gnb_id ?? "--"}</td>
                    <td className="py-2 pr-4 font-mono text-slate-200">{g.plmn ?? "--"}</td>
                    <td className="py-2 pr-4 font-mono text-slate-400">{g.peer ?? "--"}</td>
                    <td className="py-2 text-slate-500">{g.num_connected_ues ?? 0}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Active UEs table */}
      <div className="rounded-lg border border-slate-700 bg-slate-900 p-4">
        <h3 className="text-sm font-medium text-slate-300 mb-3">
          Active UEs
          {activeUes.length === 0 && (
            <span className="ml-2 text-xs text-slate-500 font-normal">
              No UE registrations detected in recent logs
            </span>
          )}
        </h3>
        {activeUes.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-slate-700 text-left text-slate-400">
                  <th className="pb-2 pr-4">IMSI</th>
                  <th className="pb-2 pr-4">Status</th>
                  <th className="pb-2 pr-4">PDU Sessions</th>
                  <th className="pb-2">Last Seen</th>
                </tr>
              </thead>
              <tbody>
                {activeUes.map((ue) => {
                  const isDead = ue.status === "deregistered" || ue.status === "released" || ue.status === "stale";
                  return (
                  <tr key={ue.imsi} className={`border-b border-slate-800${isDead ? " opacity-60" : ""}`}>
                    <td className="py-2 pr-4 font-mono text-slate-200">
                      {ue.imsi}
                    </td>
                    <td className="py-2 pr-4">
                      <span
                        className={`rounded px-1.5 py-0.5 text-[10px] font-bold uppercase ${
                          STATUS_STYLES[ue.status] || "bg-slate-800 text-slate-500"
                        }`}
                        title={ue.status === "stale" ? "Prometheus reports 0 active UEs" : ""}
                      >
                        {ue.status}
                      </span>
                    </td>
                    <td className="py-2 pr-4">
                      {ue.status === "deregistered" || ue.status === "released" || ue.status === "stale" ? (
                        <span className="text-slate-600">—</span>
                      ) : ue.sessions && ue.sessions.length > 0 ? (
                        <>
                          {ue.sessions.slice(0, 3).map((sess, i) => (
                            <span
                              key={i}
                              className="mr-1.5 mb-1 inline-block rounded bg-indigo-900/30 px-1.5 py-0.5 text-[10px] text-indigo-300"
                            >
                              {sess.ue_ip || "?"} ({sess.dnn || "?"})
                            </span>
                          ))}
                          {ue.sessions.length > 3 && (
                            <span
                              className="inline-block rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-400"
                              title={ue.sessions.slice(3).map(s => `${s.ue_ip} (${s.dnn})`).join(", ")}
                            >
                              +{ue.sessions.length - 3} more
                            </span>
                          )}
                        </>
                      ) : (
                        <span className="text-slate-600">—</span>
                      )}
                    </td>
                    <td className="py-2 text-slate-500" title={formatTs(ue.last_seen)}>
                      {relativeTime(ue.last_seen)}
                    </td>
                  </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Event feed */}
      <div ref={eventFeedRef} className="rounded-lg border border-slate-700 bg-slate-900 p-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-medium text-slate-300">
            Event Feed
            <span className="ml-2 text-xs text-slate-500 font-normal">last 10 min</span>
          </h3>
          <div className="flex gap-1">
            {EVENT_FILTERS.map((f) => (
              <button
                key={f.key}
                onClick={() => { setEventFilter(f.key); setExpandedEvent(null); }}
                className={`rounded px-2 py-0.5 text-[10px] font-medium transition-colors ${
                  eventFilter === f.key
                    ? "bg-indigo-600 text-white"
                    : "bg-slate-800 text-slate-400 hover:bg-slate-700"
                }`}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>
        {filteredEvents.length === 0 ? (
          <p className="text-xs text-slate-500">
            {events.length === 0 ? "No UE events in recent logs" : "No events match this filter"}
          </p>
        ) : (
          <div className="max-h-80 overflow-y-auto space-y-0.5">
            {filteredEvents.map((ev, i) => {
              const key = eventKey(ev, i);
              const isDisconnect = DISCONNECT_TYPES.includes(ev.type) || ev.type === "ue_context_release";
              const isError = ERROR_TYPES.includes(ev.type);
              const isExpanded = expandedEvent === key;
              const hasReason = !!ev.reason;

              return (
                <div key={key}>
                  <div
                    className={`flex items-start gap-2 rounded px-2 py-1.5 text-xs hover:bg-slate-800/50 ${
                      isError
                        ? "border-l-2 border-rose-400/70"
                        : isDisconnect || ev.severity === "warning"
                          ? "border-l-2 border-amber-500/50"
                          : "border-l-2 border-transparent"
                    } ${hasReason ? "cursor-pointer" : ""}`}
                    onClick={() => hasReason && setExpandedEvent(isExpanded ? null : key)}
                  >
                    <EventIcon ev={ev} />
                    <span className="text-slate-500 w-16 shrink-0 tabular-nums">
                      {formatTs(ev.ts)}
                    </span>
                    <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-400 uppercase shrink-0">
                      {ev.source}
                    </span>
                    <span className={isError ? "text-rose-300" : isDisconnect ? "text-amber-300" : "text-slate-300"}>
                      {ev.type === "gnb_connect" && ev.gnb_ip && gnbs.length > 0
                        ? (() => {
                            const g = gnbs.find((x) => (x.peer || "").includes(ev.gnb_ip));
                            return g
                              ? `gNB ${g.gnb_id ?? "?"} (PLMN ${g.plmn ?? "?"}) from ${ev.gnb_ip}`
                              : ev.detail;
                          })()
                        : ev.detail}
                    </span>
                    {hasReason && (
                      <span className={`ml-auto shrink-0 text-[10px] text-slate-600 transition-transform ${isExpanded ? "rotate-90" : ""}`}>
                        &#9656;
                      </span>
                    )}
                  </div>
                  {isExpanded && ev.reason && (
                    <div className="ml-8 mb-1 rounded bg-slate-800/70 border border-slate-700/50 px-3 py-2 text-xs text-slate-400">
                      <span className="text-slate-500 font-medium">Why: </span>{ev.reason}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Testing panel (only if UERANSIM pods exist) */}
      {uePods.length > 0 && (
        <div className="rounded-lg border border-slate-700 bg-slate-900 p-4">
          <h3 className="text-sm font-medium text-slate-300 mb-3">
            Connectivity Tests
          </h3>
          <div className="flex flex-wrap items-end gap-3">
            <div>
              <label className="block text-[11px] text-slate-500 mb-1">UE Pod</label>
              <select
                value={testPod}
                onChange={(e) => setTestPod(e.target.value)}
                className="rounded bg-slate-800 border border-slate-700 px-2 py-1.5 text-xs text-slate-200"
              >
                {uePods.map((p) => (
                  <option key={p.name} value={p.name}>
                    {p.name} ({p.phase})
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-[11px] text-slate-500 mb-1">Target</label>
              <input
                value={testTarget}
                onChange={(e) => setTestTarget(e.target.value)}
                className="w-36 rounded bg-slate-800 border border-slate-700 px-2 py-1.5 text-xs text-slate-200"
              />
            </div>
            <button
              onClick={handlePing}
              disabled={testRunning}
              className="rounded bg-cyan-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-cyan-500 disabled:opacity-50"
            >
              {testRunning ? "Running..." : "Ping"}
            </button>
            <button
              onClick={handleIperf}
              disabled={testRunning}
              className="rounded bg-violet-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-violet-500 disabled:opacity-50"
            >
              {testRunning ? "Running..." : "iperf3"}
            </button>
          </div>
          {testResult && (
            <pre className="mt-3 max-h-48 overflow-auto rounded bg-slate-950 border border-slate-700 p-3 text-xs font-mono text-slate-300 whitespace-pre-wrap">
              {testResult.stdout || testResult.stderr || "No output"}
            </pre>
          )}
        </div>
      )}

      {/* Manual commands hint for physical UE */}
      {uePods.length === 0 && (
        <div className="rounded-lg border border-slate-700/50 bg-slate-900/50 p-4">
          <h3 className="text-sm font-medium text-slate-400 mb-2">
            Connectivity Tests
          </h3>
          <p className="text-xs text-slate-500">
            No UERANSIM UE pods detected. If using a physical UE dongle, run tests
            manually from your host:
          </p>
          <pre className="mt-2 rounded bg-slate-950 border border-slate-700 p-2 text-xs font-mono text-amber-300">
            {`# Ping through UE tunnel\nping -I uesimtun0 8.8.8.8\n\n# iperf3 to UPF\niperf3 -c 10.45.0.1 -t 10`}
          </pre>
        </div>
      )}
    </div>
  );
}

function CounterCell({ label, value, ok, bad }) {
  const v = value ?? 0;
  let color = "text-slate-300";
  if (ok && v > 0) color = "text-emerald-400";
  if (bad && v > 0) color = "text-rose-400";
  return (
    <div>
      <div className={`text-lg font-bold tabular-nums ${color}`}>{v}</div>
      <div className="text-[10px] text-slate-500">{label}</div>
    </div>
  );
}
