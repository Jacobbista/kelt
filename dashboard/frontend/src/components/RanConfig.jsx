import React, { useCallback, useEffect, useState } from "react";
import {
  activateUeransimGnb,
  activateUeransimUe,
  createUeransimGnbForm,
  createUeransimUeForm,
  deactivateUeransimGnb,
  deactivateUeransimUe,
  deleteUeransimGnb,
  deleteUeransimUe,
  disableUeransimMode,
  enableUeransimMode,
  getRanModesStatus,
  getUeransimDefaults,
} from "../api";
// Loader: reusable 5G-style loader. Usage: <Loader size="sm" label="…" elapsed={sec} />
import Loader from "./Loader";
import useResource from "../data/useResource";

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

// UERANSIM management (gNBs and UEs), shown by the RAN page when workloads exist.
export default function RanConfig() {
  // The RAN status comes from the cache the RAN page polls: no second poller.
  const modesRes = useResource("ran-modes", getRanModesStatus);
  const modes = modesRes.data;
  const [defaults, setDefaults] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [panel, setPanel] = useState(null);
  const [showAddGnb, setShowAddGnb] = useState(false);
  const [showAddUe, setShowAddUe] = useState(false);

  const [gnbForm, setGnbForm] = useState({ cell_id: 1, tac: 1, slices: [{ sst: 1, sd: 1 }] });

  const [ueForm, setUeForm] = useState({ gnb_name: "", apn: "internet", sst: 1, sd: 1, imsi_start: "895" });

  const refresh = useCallback(async () => {
    try {
      setError("");
      const [, d] = await Promise.all([modesRes.refresh({ after: true }), getUeransimDefaults()]);
      setDefaults(d);
      setUeForm((f) => ({ ...f, gnb_name: f.gnb_name || d.gnbs?.[0]?.name || "" }));
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setLoading(false);
    }
  }, [modesRes.refresh]);

  useEffect(() => { refresh(); }, [refresh]);

  useEffect(() => {
    if (busy) return;
    // Only the UERANSIM defaults poll here; the status is the page's poll.
    const iv = setInterval(() => getUeransimDefaults().then(setDefaults).catch(() => {}), 10_000);
    return () => clearInterval(iv);
  }, [busy, refresh]);

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

  const sim = modes?.ueransim || {};
  const warnings = modes?.warnings || [];
  const gnbList = defaults?.gnbs || sim?.gnbs || [];
  const ueList = defaults?.ues || sim?.ues || [];

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
      {ueransimPanel}
    </div>
  );
}
