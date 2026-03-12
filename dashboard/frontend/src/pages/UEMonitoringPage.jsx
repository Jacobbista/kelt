import React, { useEffect, useState, useCallback } from "react";
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

function StatCard({ label, value, sub, color = "text-indigo-300" }) {
  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900 p-4">
      <div className="text-xs text-slate-400 mb-1">{label}</div>
      <div className={`text-3xl font-bold tabular-nums ${color}`}>{value}</div>
      {sub && <div className="mt-1 text-[11px] text-slate-500">{sub}</div>}
    </div>
  );
}

function EventIcon({ ev }) {
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

  if (loading) {
    return (
      <div className="flex min-h-[200px] items-center justify-center p-6">
        <Loader size="lg" label="Loading UE monitoring data…" />
      </div>
    );
  }

  const s = summary || {};

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
              {prereqsOk ? "No gNB connected yet" : "No gNBs connected — check prerequisites"}
            </h3>
            {prereqsOk ? (
              <p className="text-xs text-slate-400">
                Core and RAN infra are ready. If using a physical femtocell, ensure it is powered on and connected to the worker NIC.
              </p>
            ) : (
              <>
                <p className="text-xs text-slate-400 mb-3">If using a physical femtocell, verify:</p>
                <ul className="text-xs text-slate-400 space-y-1 mb-3">
                  <li>• AMF pod: {ranStatus.amf_pod_ready ? "✓ Running" : "✗ Not ready"}</li>
                  <li>• br-ran bridge: {ranStatus.bridge_exists ? "✓ Exists" : "✗ Missing"}</li>
                  <li>• Worker NIC (in br-ran): {ranStatus.bridge_detected ? `✓ ${ranStatus.ran_interface_detected || "detected"}` : "✗ Not found"}</li>
                  <li>• AMF n2-physical: {ranStatus.amf_has_physical_ran ? "✓ Enabled" : "✗ Disabled — click Enable in RAN Config"}</li>
                </ul>
                <p className="text-[11px] text-slate-500">
                  RAN page → Physical RAN: click <strong>Enable Physical</strong> to add n2-physical to AMF.
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
          <CounterCell label="Auth Reject" value={s.auth_reject} bad />
        </div>
      </div>

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
                    <td className="py-2 pr-4 font-mono text-cyan-300">{g.gnb_id ?? "—"}</td>
                    <td className="py-2 pr-4 font-mono text-slate-200">{g.plmn ?? "—"}</td>
                    <td className="py-2 pr-4 font-mono text-slate-400">{g.peer ?? "—"}</td>
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
                {activeUes.map((ue) => (
                  <tr key={ue.imsi} className="border-b border-slate-800">
                    <td className="py-2 pr-4 font-mono text-slate-200">
                      {ue.imsi}
                    </td>
                    <td className="py-2 pr-4">
                      <span
                        className={`rounded px-1.5 py-0.5 text-[10px] font-bold uppercase ${
                          ue.status === "registered"
                            ? "bg-emerald-900/40 text-emerald-400"
                            : "bg-slate-800 text-slate-500"
                        }`}
                      >
                        {ue.status}
                      </span>
                    </td>
                    <td className="py-2 pr-4">
                      {ue.sessions && ue.sessions.length > 0 ? (
                        ue.sessions.map((sess, i) => (
                          <span
                            key={i}
                            className="mr-2 inline-block rounded bg-indigo-900/30 px-1.5 py-0.5 text-[10px] text-indigo-300"
                          >
                            {sess.ue_ip || "?"} ({sess.dnn || "?"})
                          </span>
                        ))
                      ) : (
                        <span className="text-slate-600">-</span>
                      )}
                    </td>
                    <td className="py-2 text-slate-500">{formatTs(ue.last_seen)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Event feed */}
      <div className="rounded-lg border border-slate-700 bg-slate-900 p-4">
        <h3 className="text-sm font-medium text-slate-300 mb-3">
          Event Feed
          <span className="ml-2 text-xs text-slate-500 font-normal">last 10 min</span>
        </h3>
        {events.length === 0 ? (
          <p className="text-xs text-slate-500">No UE events in recent logs</p>
        ) : (
          <div className="max-h-72 overflow-y-auto space-y-1">
            {events.map((ev, i) => (
              <div
                key={i}
                className="flex items-start gap-2 rounded px-2 py-1.5 text-xs hover:bg-slate-800/50"
              >
                <EventIcon ev={ev} />
                <span className="text-slate-500 w-16 shrink-0 tabular-nums">
                  {formatTs(ev.ts)}
                </span>
                <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-400 uppercase shrink-0">
                  {ev.source}
                </span>
                <span className="text-slate-300">
                  {ev.type === "gnb_connect" && ev.gnb_ip && gnbs.length > 0
                    ? (() => {
                        const g = gnbs.find((x) => (x.peer || "").includes(ev.gnb_ip));
                        return g
                          ? `gNB ${g.gnb_id ?? "?"} (PLMN ${g.plmn ?? "?"}) from ${ev.gnb_ip}`
                          : ev.detail;
                      })()
                    : ev.detail}
                </span>
              </div>
            ))}
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
