import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  activateUeransimGnb,
  activateUeransimUe,
  createUeransimGnbForm,
  createUeransimUeForm,
  deactivateUeransimGnb,
  deactivateUeransimUe,
  deleteUeransimGnb,
  deleteUeransimUe,
  clearGnbConsole,
  disablePhysicalModeStream,
  disableUeransimMode,
  enablePhysicalModeStream,
  enableUeransimMode,
  getGnbConsole,
  getRanModesStatus,
  getUeransimDefaults,
  setGnbConsole,
} from "../api";
import { useOperations } from "../context/OperationsContext";
// Loader: reusable 5G-style loader. Usage: <Loader size="sm" label="…" elapsed={sec} />
import Loader from "./Loader";

function Badge({ ok, children }) {
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ${ok ? "bg-emerald-600/20 text-emerald-400" : "bg-slate-700/50 text-slate-400"}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${ok ? "bg-emerald-500" : "bg-slate-600"}`} />
      {children}
    </span>
  );
}

function Field({ label, hint, children }) {
  return (
    <div>
      <label className="block text-xs font-medium text-slate-400 mb-1">{label}</label>
      {children}
      {hint && <p className="mt-0.5 text-[10px] text-slate-600">{hint}</p>}
    </div>
  );
}

function Input({ value, onChange, placeholder, className = "", ...rest }) {
  return (
    <input
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      className={`w-full rounded border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-sm text-white placeholder:text-slate-600 focus:border-indigo-500 focus:outline-none ${className}`}
      {...rest}
    />
  );
}

function Select({ value, onChange, options, className = "" }) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={`w-full rounded border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-sm text-white focus:border-indigo-500 focus:outline-none ${className}`}
    >
      {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  );
}

function Btn({ children, onClick, disabled, variant = "primary", className = "" }) {
  const base = "rounded-lg px-4 py-2 text-sm font-medium transition-colors disabled:opacity-40";
  const styles = {
    primary: "bg-indigo-600 text-white hover:bg-indigo-500",
    danger: "border border-rose-600/50 bg-rose-600/10 text-rose-400 hover:bg-rose-600/20",
    ghost: "border border-slate-600 text-slate-300 hover:bg-slate-800",
  };
  return <button type="button" onClick={onClick} disabled={disabled} className={`${base} ${styles[variant]} ${className}`}>{children}</button>;
}

function GnbCard({ item, onClick, onToggle, onDelete, busy }) {
  const active = item.replicas > 0 && item.ready_replicas > 0;
  return (
    <div
      className="rounded-xl border-2 border-slate-700 bg-slate-900 p-4 cursor-pointer hover:border-indigo-600/50 transition-colors"
      onClick={() => onClick(item)}
    >
      <div className="flex items-center justify-between mb-2">
        <span className="font-mono font-semibold text-white">{item.name}</span>
        <Badge ok={active}>{active ? "ON" : "OFF"}</Badge>
      </div>
      <div className="text-xs text-slate-400 mb-3">Cell {item.labels?.["cell-id"] || "?"} · TAC · Slices</div>
      <div className="flex gap-2" onClick={(e) => e.stopPropagation()}>
        <button
          type="button"
          onClick={() => onToggle(item)}
          disabled={busy}
          className={`flex-1 rounded py-1.5 text-xs font-medium ${active ? "bg-amber-600/20 text-amber-300" : "bg-emerald-600/20 text-emerald-300"}`}
        >
          {active ? "Off" : "On"}
        </button>
        <Btn variant="danger" onClick={() => onDelete(item.name)} disabled={busy} className="!px-2 !py-1 !text-xs">Del</Btn>
      </div>
    </div>
  );
}

function UeCard({ item, onClick, onToggle, onDelete, busy }) {
  const active = item.replicas > 0 && item.ready_replicas > 0;
  const cellId = item.labels?.["cell-id"];
  const gnb = item.labels?.gnb || item.labels?.["ue-gnb"] || (cellId ? `gnb-${cellId}` : "?");
  return (
    <div
      className="rounded-xl border-2 border-slate-700 bg-slate-900 p-4 cursor-pointer hover:border-indigo-600/50 transition-colors"
      onClick={() => onClick(item)}
    >
      <div className="flex items-center justify-between mb-2">
        <span className="font-mono font-semibold text-white">{item.name}</span>
        <Badge ok={active}>{active ? "ON" : "OFF"}</Badge>
      </div>
      <div className="text-xs text-slate-400 mb-3">→ {gnb}</div>
      <div className="flex gap-2" onClick={(e) => e.stopPropagation()}>
        <button
          type="button"
          onClick={() => onToggle(item)}
          disabled={busy}
          className={`flex-1 rounded py-1.5 text-xs font-medium ${active ? "bg-amber-600/20 text-amber-300" : "bg-emerald-600/20 text-emerald-300"}`}
        >
          {active ? "Off" : "On"}
        </button>
        <Btn variant="danger" onClick={() => onDelete(item.name)} disabled={busy} className="!px-2 !py-1 !text-xs">Del</Btn>
      </div>
    </div>
  );
}

function AddCard({ onClick }) {
  return (
    <div
      className="flex min-h-[140px] items-center justify-center rounded-xl border-2 border-dashed border-slate-600 bg-slate-900/50 cursor-pointer hover:border-indigo-500 hover:bg-slate-900 transition-colors"
      onClick={onClick}
    >
      <span className="text-3xl text-slate-500">+</span>
    </div>
  );
}

// gNB management console: expose the physical gNB's own web UI (an IP on the RAN
// management LAN, not browser-reachable) at gnb.<base> through the dynamic apps
// route. The operator types the appliance IP:port; KELT assumes no management
// subnet exists, so an empty value means no surface. Self-contained: fetches and
// mutates its own state. Behind the same Cloudflare Access perimeter as the rest.
function GnbConsoleCard() {
  const [state, setState] = useState(null);
  const [host, setHost] = useState("");
  const [port, setPort] = useState("8400");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const load = useCallback(() => {
    getGnbConsole()
      .then((s) => {
        setState(s);
        if (s?.origin) {
          const [h, p] = String(s.origin).split(":");
          setHost(h || "");
          setPort(p || "8400");
        }
      })
      .catch(() => {});
  }, []);
  useEffect(() => { load(); }, [load]);

  const save = async () => {
    setBusy(true);
    setErr("");
    try {
      setState(await setGnbConsole(host.trim(), parseInt(port, 10) || 0));
    } catch (e) {
      setErr(String(e?.message || e));
    } finally {
      setBusy(false);
    }
  };
  const clear = async () => {
    setBusy(true);
    setErr("");
    try {
      setState(await clearGnbConsole());
      setHost("");
      setPort("8400");
    } catch (e) {
      setErr(String(e?.message || e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-xl border border-slate-700 bg-slate-900/60 p-4">
      <div className="mb-1 flex items-center justify-between">
        <h3 className="font-semibold text-slate-200">gNB Management Console</h3>
        {state?.configured
          ? (state?.reachable === false
              ? <Badge ok={false}>exposed · unreachable</Badge>
              : <Badge ok>exposed{state?.reachable ? " · reachable" : ""}</Badge>)
          : <Badge ok={false}>not exposed</Badge>}
      </div>
      <p className="mb-3 text-xs text-slate-400">
        Publish the gNB/femtocell web UI at its own subdomain through the front-door.
        Enter the appliance management address reachable from the worker (its
        management LAN IP, not the RAN one). Reached only behind the front-door
        perimeter (Cloudflare Access) plus the appliance's own login.
      </p>
      <div className="flex flex-wrap items-end gap-3">
        <Field label="Management IP">
          <Input value={host} onChange={setHost} placeholder="192.168.5.100" className="w-40" />
        </Field>
        <Field label="Port">
          <Input value={port} onChange={setPort} placeholder="8400" className="w-24" />
        </Field>
        <Btn onClick={save} disabled={busy || !host.trim()}>{busy ? "Checking…" : "Expose"}</Btn>
        {state?.configured && (
          <Btn onClick={clear} disabled={busy} variant="danger">Remove</Btn>
        )}
      </div>
      {/* Persistent hint: stays visible while typing (unlike the field placeholder). */}
      <p className="mt-2 text-[11px] text-slate-500">
        Example <span className="font-mono text-slate-400">192.168.5.100:8400</span> — find it in the
        femtocell's own admin (its management interface), not the <span className="font-mono">.6</span>
        {" "}RAN address. KELT probes it on Expose to confirm it is reachable.
      </p>
      {state?.url && (
        <p className="mt-3 text-xs text-slate-400">
          {state?.reachable === false ? "Set, but NOT reachable from KELT (check the IP/port and that the worker can reach it). " : "Reachable at "}
          <a href={state.url} target="_blank" rel="noreferrer" className="font-mono text-teal-300 hover:underline">
            {state.url.replace(/^https?:\/\//, "")}
          </a>{" "}
          (requires the apps route enabled).
        </p>
      )}
      {err && <div className="mt-3 rounded border border-rose-700/50 bg-rose-950/30 px-3 py-2 text-xs text-rose-300">{err}</div>}
    </div>
  );
}

// Read-only cheat-sheet: the values the operator must set on the physical gNB's RAN
// interface so it attaches to this core. All derived from KELT's own RAN config (the
// subnet + AMF IP the dashboard already shows), so there is nothing to guess and no
// need to open all.yml. The RAN transport is L2 bridge + worker L3 routing (no NAT),
// so the gNB points NGAP straight at the AMF's real address.
function GnbSideConfigCard({ subnet, amfIp }) {
  const sn = subnet || "192.168.6.0/24";
  const gw = sn.replace(/\.\d+(\/\d+)?$/, ".1");
  const rows = [
    ["RAN interface IP", `a free static address in ${sn}`],
    ["Default gateway", gw],
    ["AMF / NGAP (SCTP)", `${amfIp || "192.168.6.150"} : 38412`],
    ["User-plane route", `10.203.0.0/24 via ${gw}`],
  ];
  return (
    <div className="rounded-xl border border-slate-700 bg-slate-900/60 p-4">
      <h3 className="mb-1 font-semibold text-slate-200">Configure on your gNB</h3>
      <p className="mb-3 text-xs text-slate-400">
        Set these on the physical gNB/femtocell's RAN interface so it attaches to this
        core. They come from KELT's own config; no need to edit any file.
      </p>
      <div className="space-y-1.5">
        {rows.map(([k, v]) => (
          <div key={k} className="flex justify-between gap-3 text-xs">
            <span className="text-slate-500">{k}</span>
            <span className="font-mono text-slate-200 text-right">{v}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function RanConfig({ activeTab = "physical" }) {
  const ops = useOperations();
  const [modes, setModes] = useState(null);
  const [defaults, setDefaults] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [panel, setPanel] = useState(null);
  const [showAddGnb, setShowAddGnb] = useState(false);
  const [showAddUe, setShowAddUe] = useState(false);

  const [hostNic, setHostNic] = useState("");
  const [hostNicBound, setHostNicBound] = useState(null); // worker NIC when hostNic was last confirmed
  const [showHostNicEdit, setShowHostNicEdit] = useState(false);

  useEffect(() => {
    try {
      const raw = localStorage.getItem("physical_ran_host_nic");
      if (!raw) return;
      const data = raw.startsWith("{") ? JSON.parse(raw) : { hostNic: raw, workerNicWhenSet: null };
      setHostNic(data.hostNic || "");
      setHostNicBound(data.workerNicWhenSet || null);
    } catch (_) {}
  }, []);

  const lastAppliedRef = useRef(null);
  useEffect(() => {
    if (!modes?.physical) return;
    const workerNic = modes.physical.ran_interface_detected || null;
    const applied = (modes.physical.host_nic_applied || "").trim();
    if (applied) {
      if (applied !== lastAppliedRef.current) {
        lastAppliedRef.current = applied;
        setHostNic(applied);
        setHostNicBound(workerNic);
        localStorage.setItem("physical_ran_host_nic", JSON.stringify({ hostNic: applied, workerNicWhenSet: workerNic }));
      }
    } else {
      lastAppliedRef.current = null;
      if (hostNicBound !== null && workerNic !== hostNicBound) {
        setHostNic("");
        setHostNicBound(null);
        localStorage.removeItem("physical_ran_host_nic");
      } else if (workerNic && hostNic.trim() && hostNicBound === null) {
        setHostNicBound(workerNic);
        localStorage.setItem("physical_ran_host_nic", JSON.stringify({ hostNic: hostNic.trim(), workerNicWhenSet: workerNic }));
      }
    }
  }, [modes?.physical?.ran_interface_detected, modes?.physical?.host_nic_applied, hostNic, hostNicBound]);
  const [copyFeedback, setCopyFeedback] = useState(false);
  const [teardownCopyFeedback, setTeardownCopyFeedback] = useState(false);
  const [reloadCopyFeedback, setReloadCopyFeedback] = useState(false);

  const [gnbForm, setGnbForm] = useState({ cell_id: 1, tac: 1, slices: [{ sst: 1, sd: 1 }] });

  function copyToClipboard(text, setFeedback = setCopyFeedback) {
    const done = () => { setFeedback(true); setTimeout(() => setFeedback(false), 1500); };
    const fallback = () => {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.left = "-9999px";
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand("copy");
        done();
      } catch (_) {}
      document.body.removeChild(ta);
    };
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(text).then(done).catch(fallback);
    } else {
      fallback();
    }
  }
  const [ueForm, setUeForm] = useState({ gnb_name: "", apn: "internet", sst: 1, sd: 1, imsi_start: "895" });

  const refresh = useCallback(async () => {
    try {
      setError("");
      const [m, d] = await Promise.all([getRanModesStatus(), getUeransimDefaults()]);
      setModes(m);
      setDefaults(d);
      setUeForm((f) => ({ ...f, gnb_name: f.gnb_name || d.gnbs?.[0]?.name || "" }));
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  useEffect(() => {
    if (busy || ops.busy) return;
    const iv = setInterval(refresh, 10_000);
    return () => clearInterval(iv);
  }, [busy, ops.busy, refresh]);

  async function action(fn) {
    setBusy(true); setError("");
    try {
      await fn();
      await refresh();
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <div className="flex h-64 flex-col items-center justify-center gap-4"><Loader size="lg" label="Loading RAN state…" /></div>;

  const phys = modes?.physical || {};
  const sim = modes?.ueransim || {};
  const cfg = phys?.config || {};
  const warnings = modes?.warnings || [];
  const gnbList = defaults?.gnbs || sim?.gnbs || [];
  const ueList = defaults?.ues || sim?.ues || [];

  const physicalPanel = (
    <div className="space-y-5">
      <div className="rounded-lg border border-slate-700 bg-slate-900 p-5">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-base font-semibold text-white">Physical RAN</h3>
          <Badge ok={phys?.enabled}>{phys?.enabled ? "ACTIVE" : "INACTIVE"}</Badge>
        </div>
        <div className="grid grid-cols-2 gap-3 mb-4">
          <Badge ok={phys?.bridge_detected}>Worker VM NIC: {phys?.ran_interface_detected || cfg.physical_ran_interface || "not found"}</Badge>
          <Badge ok={phys?.bridge_exists}>OVS br-ran</Badge>
          <Badge ok={phys?.nad_exists}>NAD n2-physical</Badge>
          <Badge ok={phys?.amf_has_physical_ran}>AMF annotation</Badge>
          {phys?.amf_has_physical_ran && <Badge ok={phys?.amf_attached_to_bridge}>AMF ↔ br-ran (data path)</Badge>}
          {phys?.amf_has_physical_ran && <Badge ok={phys?.upf_has_return_route}>UPF return route</Badge>}
        </div>
        <div className="rounded bg-slate-950 p-3 space-y-1.5 mb-4">
          <div className="flex justify-between text-xs"><span className="text-slate-500">AMF Physical IP</span><span className="text-slate-200 font-mono">{cfg.amf_physical_ran_ip}</span></div>
          <div className="flex justify-between text-xs"><span className="text-slate-500">RAN Subnet</span><span className="text-slate-200 font-mono">{cfg.physical_ran_subnet}</span></div>
          <div className="flex justify-between text-xs"><span className="text-slate-500">Bridge Mode</span><span className="text-slate-200">{cfg.ran_bridge_mode}</span></div>
          {phys?.ran_interface_detected && (
            <>
              <div className="flex justify-between text-xs pt-1 border-t border-slate-800 mt-1">
                <span className="text-slate-500">Worker VM NIC</span>
                <span className="text-slate-200 font-mono">{phys.ran_interface_detected}</span>
              </div>
              <div className="relative flex justify-between items-center text-xs">
                <span className="text-slate-500">Host PC NIC</span>
                <button
                  type="button"
                  onClick={() => setShowHostNicEdit(true)}
                  className={`font-mono text-right hover:text-indigo-300 transition-colors ${(phys?.host_nic_applied || hostNic).trim() ? "text-slate-200" : "text-slate-500 italic"}`}
                >
                  {(phys?.host_nic_applied || hostNic).trim() || "Set host adapter…"}
                  {phys?.host_nic_applied && (
                    <span className="ml-1.5 text-[10px] font-normal text-emerald-500/90" title="Verified: applied by vagrant reload">✓</span>
                  )}
                </button>
                {showHostNicEdit && (
                  <div className="fixed inset-0 z-50 flex items-center justify-center p-4" onClick={() => setShowHostNicEdit(false)}>
                    <div className="absolute inset-0 bg-black/40" aria-hidden />
                    <div className="relative w-full max-w-sm rounded-xl border border-slate-600 bg-slate-900 p-4 shadow-xl" onClick={(e) => e.stopPropagation()}>
                      <h4 className="text-sm font-medium text-white mb-3">Host PC adapter name</h4>
                      <Input
                        value={hostNic}
                        onChange={setHostNic}
                        placeholder="enx00e04c6817b7"
                        autoFocus
                      />
                      <p className="mt-2 text-[11px] text-slate-500">Run <span className="font-mono text-slate-400">ip link show</span> on your host to find the adapter connected to the gNB network.</p>
                      <div className="mt-3 rounded border border-amber-700/40 bg-amber-950/30 px-3 py-2 text-[11px] text-amber-200/90">
                        Changing this won&apos;t take effect until you run <span className="font-mono text-amber-100">vagrant reload worker</span> with the new <span className="font-mono">PHYSICAL_RAN_BRIDGE</span> value.
                      </div>
                      <div className="flex justify-end gap-2 mt-4">
                        <Btn variant="ghost" onClick={() => setShowHostNicEdit(false)}>Cancel</Btn>
                        <Btn onClick={() => {
                          const nic = hostNic.trim();
                          const workerNic = phys?.ran_interface_detected || null;
                          setHostNicBound(workerNic);
                          localStorage.setItem("physical_ran_host_nic", JSON.stringify({ hostNic: nic, workerNicWhenSet: workerNic }));
                          setShowHostNicEdit(false);
                        }}>Save</Btn>
                      </div>
                    </div>
                  </div>
                )}
              </div>
              {phys?.host_nic_applied && hostNic.trim() && hostNic.trim() !== phys.host_nic_applied && (
                <div className="rounded border border-amber-700/50 bg-amber-950/30 px-3 py-2.5 space-y-2 text-[11px] text-amber-200/90">
                  <p>Stored value differs from applied. Run this on your host to apply the new interface:</p>
                  <div className="flex gap-2 items-stretch">
                    <div className="flex-1 rounded bg-slate-950 border border-slate-700 px-3 py-2 font-mono text-xs text-emerald-300 select-text">
                      <span className="text-slate-500">$</span> PHYSICAL_RAN_ENABLED=true PHYSICAL_RAN_BRIDGE={hostNic.trim()} vagrant reload worker
                    </div>
                    <Btn
                      variant="ghost"
                      className="!py-1.5 !px-3 shrink-0"
                      onClick={() => copyToClipboard(`PHYSICAL_RAN_ENABLED=true PHYSICAL_RAN_BRIDGE=${hostNic.trim()} vagrant reload worker`, setReloadCopyFeedback)}
                    >
                      {reloadCopyFeedback ? "Copied!" : "Copy"}
                    </Btn>
                  </div>
                </div>
              )}
              <div className="text-[10px] text-slate-600 leading-relaxed">
                VirtualBox bridges your host adapter into the worker VM with a different name. The ✓ means the value was verified from the last <span className="font-mono">vagrant reload</span>. Enable/Disable controls OVS + K8s on top — the NIC itself is managed by Vagrant.
              </div>
            </>
          )}
        </div>

        {/* === STALE CONFIG: NIC missing but K8s config remains === */}
        {!phys?.bridge_detected && (phys?.bridge_exists || phys?.nad_exists || phys?.amf_has_physical_ran) && (
          <div className="rounded border border-rose-700/50 bg-rose-950/30 p-4 mb-4 space-y-3">
            <div className="flex items-start gap-2">
              <span className="text-rose-400 text-sm mt-0.5">!</span>
              <div>
                <h4 className="text-sm font-medium text-rose-200">Worker VM has no RAN interface</h4>
                <p className="text-xs text-slate-400 mt-1 leading-relaxed">
                  The VM was started without <span className="font-mono text-slate-300">PHYSICAL_RAN_BRIDGE</span>, but K8s still has config from a previous session
                  ({[
                    phys?.bridge_exists && "br-ran",
                    phys?.nad_exists && "NAD",
                    phys?.amf_has_physical_ran && "AMF annotation",
                  ].filter(Boolean).join(", ")}).
                  This won&apos;t work — the bridge has no physical port.
                </p>
              </div>
            </div>
            <div className="rounded bg-slate-950/60 p-3 text-[11px] text-slate-400 space-y-2">
              <p><strong className="text-slate-300">Option A</strong> — Re-add the NIC and keep the config:</p>
              {(phys?.host_nic_applied || hostNic.trim()) ? (
                <div className="flex gap-2 items-stretch">
                  <div className="flex-1 rounded bg-slate-950 border border-slate-700 px-3 py-2 font-mono text-xs text-emerald-300 select-text">
                    <span className="text-slate-500">$</span> PHYSICAL_RAN_ENABLED=true PHYSICAL_RAN_BRIDGE={phys?.host_nic_applied || hostNic.trim()} vagrant reload worker
                  </div>
                  <Btn
                    variant="ghost"
                    className="!py-1.5 !px-3 shrink-0"
                    onClick={() => copyToClipboard(`PHYSICAL_RAN_ENABLED=true PHYSICAL_RAN_BRIDGE=${phys?.host_nic_applied || hostNic.trim()} vagrant reload worker`, setReloadCopyFeedback)}
                  >
                    {reloadCopyFeedback ? "Copied!" : "Copy"}
                  </Btn>
                </div>
              ) : (
                <p className="text-slate-500 italic">Set your Host PC NIC first (no saved value).</p>
              )}
              <p className="mt-2"><strong className="text-slate-300">Option B</strong> — Clean up the stale config: click <strong className="text-rose-300">Disable</strong> below.</p>
            </div>
          </div>
        )}

        {/* === SETUP: no NIC detected === */}
        {!phys?.bridge_detected && !phys?.bridge_exists && !phys?.nad_exists && !phys?.amf_has_physical_ran && (
          <div className="rounded border border-amber-700/50 bg-amber-950/30 p-4 mb-4 space-y-3">
            <div className="flex items-start gap-2">
              <span className="text-amber-400 text-sm mt-0.5">⚠</span>
              <div>
                <h4 className="text-sm font-medium text-amber-200">Setup required: add RAN network to worker VM</h4>
                <p className="text-xs text-slate-400 mt-1 leading-relaxed">
                  Physical RAN requires a USB dongle or Ethernet adapter on your <strong className="text-slate-300">host PC</strong> connected to the gNB network.
                  Vagrant bridges this host NIC into the worker VM, where it gets a different name.
                </p>
              </div>
            </div>
            <div className="rounded bg-slate-950/60 p-3 space-y-1.5 text-[11px]">
              <div className="flex items-center gap-3">
                <span className="w-24 text-right font-medium text-slate-400 shrink-0">Host PC NIC</span>
                <span className="text-slate-500">e.g. <span className="font-mono text-slate-300">enx00e04c6817b7</span> — your physical adapter, used in the vagrant command</span>
              </div>
              <div className="ml-[6.5rem] text-slate-600 text-[10px]">↓ VirtualBox bridges it into the VM ↓</div>
              <div className="flex items-center gap-3">
                <span className="w-24 text-right font-medium text-slate-400 shrink-0">Worker VM NIC</span>
                <span className="text-slate-500">e.g. <span className="font-mono text-slate-300">enp0s9</span> — auto-detected by OVS via subnet <span className="font-mono text-slate-300">{cfg.physical_ran_subnet || "192.168.6.0/24"}</span></span>
              </div>
            </div>
            <div className="space-y-2">
              <label className="block text-xs font-medium text-slate-400">
                Host PC adapter name <span className="text-slate-600">(run <span className="font-mono">ip link show</span> on your host)</span>
              </label>
              <Input
                value={hostNic}
                onChange={(v) => { setHostNic(v); localStorage.setItem("physical_ran_host_nic", JSON.stringify({ hostNic: v, workerNicWhenSet: null })); }}
                placeholder="enx00e04c6817b7"
              />
            </div>
            {hostNic.trim() && (
              <div className="space-y-1.5">
                <label className="block text-xs font-medium text-slate-400">Run on your <strong className="text-slate-300">host PC</strong> terminal (not inside VM):</label>
                <div className="flex gap-2 items-stretch">
                  <div className="flex-1 rounded bg-slate-950 border border-slate-700 px-3 py-2 font-mono text-xs text-emerald-300 select-text">
                    <span className="text-slate-500">$</span> PHYSICAL_RAN_ENABLED=true PHYSICAL_RAN_BRIDGE={hostNic.trim()} vagrant reload worker
                  </div>
                  <Btn
                    variant="ghost"
                    className="!py-1.5 !px-3 shrink-0"
                    onClick={() => copyToClipboard(`PHYSICAL_RAN_ENABLED=true PHYSICAL_RAN_BRIDGE=${hostNic.trim()} vagrant reload worker`)}
                  >
                    {copyFeedback ? "Copied!" : "Copy"}
                  </Btn>
                </div>
                <p className="text-[10px] text-slate-600">This restarts the worker VM with a bridged NIC. OVS will auto-detect it and create br-ran. This page auto-refreshes every 10s.</p>
              </div>
            )}
          </div>
        )}

        {/* === TEARDOWN HINT: NIC present but disabled === */}
        {phys?.bridge_detected && !phys?.enabled && !phys?.bridge_exists && !phys?.nad_exists && !phys?.amf_has_physical_ran && (
          <div className="rounded border border-slate-600/50 bg-slate-900/50 p-3 mb-4 text-xs text-slate-500 space-y-2">
            <p>
              <strong className="text-slate-400">Physical RAN is disabled</strong> but the worker VM still has the RAN NIC (<span className="font-mono text-slate-300">{phys.ran_interface_detected}</span>).
              This is normal — Enable/Disable controls the OVS bridge and K8s config, not the VM hardware. The NIC is harmless when idle.
            </p>
            <p>
              To fully remove it, run on your host:{" "}
              <button
                type="button"
                onClick={() => copyToClipboard("vagrant reload worker", setTeardownCopyFeedback)}
                title="Click to copy"
                className={`font-mono px-2 py-0.5 rounded border transition-all duration-200 select-text ${
                  teardownCopyFeedback
                    ? "border-emerald-500/70 bg-emerald-500/20 text-emerald-300"
                    : "border-slate-600 text-slate-300 hover:border-slate-500 hover:bg-slate-800/50 cursor-pointer"
                }`}
              >
                {teardownCopyFeedback ? "Copied!" : "vagrant reload worker"}
              </button>{" "}
              (without PHYSICAL_RAN_ENABLED).
            </p>
          </div>
        )}
        <div className="flex gap-3">
          <Btn
            onClick={() => {
              setError("");
              ops.run("ran-enable", "Enabling Physical RAN", enablePhysicalModeStream, (result, err) => {
                if (err) setError(err);
                else if (result?.error) setError(result.error);
                refresh();
              });
            }}
            disabled={ops.busy || busy || !phys?.bridge_detected}
            title={!phys?.bridge_detected ? "Worker VM NIC not detected — re-add with vagrant reload" : undefined}
          >
            {phys?.enabled ? "Reconfigure" : "Enable Physical"}
          </Btn>
          <Btn
            variant="danger"
            onClick={() => {
              setError("");
              ops.run("ran-disable", "Disabling Physical RAN", disablePhysicalModeStream, (result, err) => {
                if (err) setError(err);
                refresh();
              });
            }}
            disabled={ops.busy || busy || !(phys?.enabled || phys?.amf_has_physical_ran || phys?.nad_exists)}
          >
            Disable
          </Btn>
        </div>
        {ops.current && (ops.current.id === "ran-enable" || ops.current.id === "ran-disable") && (
          <div className="mt-4 rounded border border-slate-600 bg-slate-950/50 p-3">
            <div className="flex items-center justify-between mb-2">
              <h4 className="text-xs font-medium text-slate-400">
                {ops.current.id === "ran-disable" ? "Deactivation progress" : "Activation progress"}
              </h4>
              <button type="button" onClick={ops.dismiss} className="text-slate-500 hover:text-slate-300 text-xs disabled:opacity-50" disabled={ops.busy}>Dismiss</button>
            </div>
            {ops.busy && (
              <div className="mb-3 flex items-center gap-4">
                <Loader size="sm" label={ops.current.progress?.step === "starting" ? "Starting…" : undefined} elapsed={ops.elapsed} />
                {ops.current.progress && ops.current.progress.step !== "starting" && (
                  <div className="flex-1 rounded bg-indigo-950/40 border border-indigo-600/30 px-3 py-2 text-xs text-indigo-200">
                    <span className="font-mono text-indigo-300">{ops.current.progress.step}</span>
                    {ops.current.progress.message && <span className="ml-2 text-slate-300">— {ops.current.progress.message}</span>}
                  </div>
                )}
              </div>
            )}
            {ops.current.error && (
              <p className="mb-2 text-xs text-rose-400">{ops.current.error}</p>
            )}
            <ul className="space-y-1.5 text-xs">
              {(ops.current.steps || []).map((s, i) => {
                const ok = s.status?.startsWith("ok");
                const warn = s.status === "warning" || s.status === "skipped";
                return (
                  <li key={i} className="flex flex-col gap-1">
                    <div className="flex items-center gap-2">
                      <span className={ok ? "text-emerald-500" : warn ? "text-amber-400" : "text-slate-500"}>
                        {ok ? "✓" : warn ? "⚠" : "○"}
                      </span>
                      <span className="text-slate-300 font-mono">{s.step}</span>
                      <span className="text-slate-500">({s.status})</span>
                    </div>
                    {s.hint && (
                      <p className="ml-5 text-[11px] text-amber-400/90 leading-relaxed">{s.hint}</p>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        )}
      </div>
      <div className="grid gap-5 lg:grid-cols-2">
        <GnbSideConfigCard subnet={cfg.physical_ran_subnet} amfIp={cfg.amf_physical_ran_ip} />
        <GnbConsoleCard />
      </div>
    </div>
  );

  const ueransimPanel = (
    <div className="flex gap-6">
      <div className="flex-1 space-y-6">
        {/* Status */}
        <div className="rounded-lg border border-slate-700 bg-slate-900 p-4">
          <div className="flex items-center justify-between">
            <h3 className="text-base font-semibold text-white">UERANSIM</h3>
            <Badge ok={sim?.enabled}>{sim?.enabled ? "ACTIVE" : "INACTIVE"}</Badge>
          </div>
          <div className="flex gap-4 mt-2">
            <Badge ok={gnbList.length > 0}>gNBs: {gnbList.length}</Badge>
            <Badge ok={ueList.length > 0}>UEs: {ueList.length}</Badge>
            {!defaults?.has_discovery_token && <Badge ok={false}>Discovery token missing</Badge>}
          </div>
          <div className="flex gap-3 mt-3">
            <Btn onClick={() => action(enableUeransimMode)} disabled={busy}>Enable all</Btn>
            <Btn variant="danger" onClick={() => action(disableUeransimMode)} disabled={busy || !sim?.enabled}>Disable all</Btn>
          </div>
        </div>

        {/* gNB cards */}
        <div>
          <h4 className="text-sm font-semibold text-slate-300 mb-3">gNBs</h4>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
            {gnbList.map((g) => (
              <GnbCard
                key={g.name}
                item={g}
                onClick={(item) => setPanel({ type: "gnb", item })}
                onToggle={(item) => action(() => item.replicas > 0 ? deactivateUeransimGnb(item.name) : activateUeransimGnb(item.name))}
                onDelete={(n) => action(() => deleteUeransimGnb(n))}
                busy={busy}
              />
            ))}
            <AddCard onClick={() => setShowAddGnb(true)} />
          </div>
        </div>

        {/* UE cards */}
        <div>
          <h4 className="text-sm font-semibold text-slate-300 mb-3">UEs</h4>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
            {ueList.map((u) => (
              <UeCard
                key={u.name}
                item={u}
                onClick={(item) => setPanel({ type: "ue", item })}
                onToggle={(item) => action(() => item.replicas > 0 ? deactivateUeransimUe(item.name) : activateUeransimUe(item.name))}
                onDelete={(n) => action(() => deleteUeransimUe(n))}
                busy={busy}
              />
            ))}
            <AddCard onClick={() => setShowAddUe(true)} />
          </div>
        </div>
      </div>

      {/* Side panel */}
      {panel && (
        <div className="w-80 flex-shrink-0 rounded-lg border border-slate-700 bg-slate-900 p-4">
          <div className="flex items-center justify-between mb-4">
            <h4 className="font-mono font-semibold text-white">{panel.item.name}</h4>
            <button type="button" onClick={() => setPanel(null)} className="text-slate-500 hover:text-white">×</button>
          </div>
          {panel.type === "gnb" && (
            <div className="space-y-3 text-xs">
              <div><span className="text-slate-500">Cell</span> {panel.item.labels?.["cell-id"]}</div>
              <div><span className="text-slate-500">Status</span> {panel.item.ready_replicas}/{panel.item.replicas}</div>
              <p className="text-slate-500">Editable parameters (TAC, slices) require recreation.</p>
            </div>
          )}
          {panel.type === "ue" && (
            <div className="space-y-3 text-xs">
              <div><span className="text-slate-500">gNB</span> {panel.item.labels?.gnb || panel.item.labels?.["ue-gnb"] || "?"}</div>
              <div><span className="text-slate-500">Status</span> {panel.item.ready_replicas}/{panel.item.replicas}</div>
              <p className="text-slate-500">Editable parameters (APN, slice) require recreation.</p>
            </div>
          )}
        </div>
      )}

      {/* Add gNB modal */}
      {showAddGnb && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={() => setShowAddGnb(false)}>
          <div className="rounded-xl border border-slate-700 bg-slate-900 p-6 w-full max-w-md" onClick={(e) => e.stopPropagation()}>
            <h4 className="text-base font-semibold text-white mb-4">Add gNB</h4>
            <p className="text-xs text-slate-400 mb-4">Will be created as {defaults?.next_gnb_name} (always on edge)</p>
            <div className="grid gap-3">
              <Field label="Cell ID" hint="Unique per cell">
                <Input type="number" value={gnbForm.cell_id} onChange={(v) => setGnbForm({ ...gnbForm, cell_id: Number(v) })} />
              </Field>
              <Field label="TAC" hint="Tracking Area Code">
                <Input type="number" value={gnbForm.tac} onChange={(v) => setGnbForm({ ...gnbForm, tac: Number(v) })} />
              </Field>
              <Field label="Slice default" hint="SST / SD">
                <div className="flex gap-2">
                  <Input type="number" value={gnbForm.slices[0]?.sst || 1} onChange={(v) => setGnbForm({ ...gnbForm, slices: [{ sst: Number(v), sd: gnbForm.slices[0]?.sd || 1 }] })} placeholder="SST" />
                  <Input type="number" value={gnbForm.slices[0]?.sd || 1} onChange={(v) => setGnbForm({ ...gnbForm, slices: [{ sst: gnbForm.slices[0]?.sst || 1, sd: Number(v) }] })} placeholder="SD" />
                </div>
              </Field>
            </div>
            <div className="flex gap-3 mt-6">
              <Btn onClick={() => action(() => createUeransimGnbForm({ ...gnbForm })).then(() => setShowAddGnb(false))} disabled={busy}>Create</Btn>
              <Btn variant="ghost" onClick={() => setShowAddGnb(false)}>Cancel</Btn>
            </div>
          </div>
        </div>
      )}

      {/* Add UE modal */}
      {showAddUe && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={() => setShowAddUe(false)}>
          <div className="rounded-xl border border-slate-700 bg-slate-900 p-6 w-full max-w-md" onClick={(e) => e.stopPropagation()}>
            <h4 className="text-base font-semibold text-white mb-4">Add UE</h4>
            <p className="text-xs text-slate-400 mb-4">Will be created as {defaults?.next_ue_name} (always on edge)</p>
            <div className="grid gap-3">
              <Field label="gNB" hint="Which gNB to connect to">
                <Select value={ueForm.gnb_name} onChange={(v) => setUeForm({ ...ueForm, gnb_name: v })} options={gnbList.length > 0 ? gnbList.map((g) => ({ value: g.name, label: g.name })) : [{ value: "", label: "Deploy a gNB first" }]} />
              </Field>
              <Field label="APN/DNN">
                <Input value={ueForm.apn} onChange={(v) => setUeForm({ ...ueForm, apn: v })} />
              </Field>
              <Field label="Slice" hint="SST / SD">
                <div className="flex gap-2">
                  <Input type="number" value={ueForm.sst} onChange={(v) => setUeForm({ ...ueForm, sst: Number(v) })} placeholder="SST" />
                  <Input type="number" value={ueForm.sd} onChange={(v) => setUeForm({ ...ueForm, sd: Number(v) })} placeholder="SD" />
                </div>
              </Field>
              <Field label="IMSI suffix" hint={`IMSI: ${defaults?.defaults?.mcc || "001"}${defaults?.defaults?.mnc || "01"}${defaults?.defaults?.imsi_msin_base || "1234567"}XXX`}>
                <Input value={ueForm.imsi_start} onChange={(v) => setUeForm({ ...ueForm, imsi_start: v })} />
              </Field>
            </div>
            <div className="flex gap-3 mt-6">
              <Btn onClick={() => action(() => createUeransimUeForm({ ...ueForm, cell_id: gnbList.find((g) => g.name === ueForm.gnb_name)?.labels?.["cell-id"] || 1 })).then(() => setShowAddUe(false))} disabled={busy || !ueForm.gnb_name}>Create</Btn>
              <Btn variant="ghost" onClick={() => setShowAddUe(false)}>Cancel</Btn>
            </div>
          </div>
        </div>
      )}
    </div>
  );

  return (
    <div className="mx-auto max-w-6xl space-y-4 pb-8">
      {error && <div className="rounded border border-rose-700 bg-rose-950/50 p-3 text-sm text-rose-300">{error}</div>}
      {warnings.includes("coexistence_active") && (
        <div className="rounded border border-amber-700 bg-amber-950/40 p-3 text-sm text-amber-200">
          Physical RAN and UERANSIM both active (coexistence).
        </div>
      )}
      {activeTab === "physical" ? physicalPanel : ueransimPanel}
    </div>
  );
}
