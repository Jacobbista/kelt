import React, { useCallback, useEffect, useState } from "react";
import {
  createSubscriber,
  deleteSubscriber,
  getSubscribers,
  importSubscribers,
  initSubscribers,
  updateSubscriber,
} from "../api";
import Loader from "../components/Loader";

const EMPTY_SUB = {
  imsi: "",
  security: {
    k: "",
    amf: "8000",
    op: "11111111111111111111111111111111",
    opc: null,
  },
  subscribed_rau_tau_timer: 12,
  network_access_mode: 0,
  subscriber_status: 0,
  access_restriction_data: 32,
  msisdn: [],
  __v: 0,
  slice: [
    {
      sst: 1,
      sd: "000001",
      default_indicator: true,
      session: [
        {
          name: "internet",
          type: 3,
          ambr: { uplink: { value: 1000, unit: 2 }, downlink: { value: 1000, unit: 2 } },
          qos: { index: 9, arp: { priority_level: 15, pre_emption_capability: 1, pre_emption_vulnerability: 1 } },
          pcc_rule: [],
        },
      ],
    },
  ],
  ambr: { uplink: { value: 1000, unit: 2 }, downlink: { value: 1000, unit: 2 } },
  schema_version: 1,
};

const AMBR_UNITS = { 0: "bps", 1: "Kbps", 2: "Mbps", 3: "Gbps" };

function ambrStr(ambr) {
  if (!ambr) return "—";
  const unit = AMBR_UNITS[ambr.unit] || "";
  return `${ambr.value} ${unit}`;
}

export default function SubscribersPage() {
  const [subscribers, setSubscribers] = useState([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState(null);
  const [editing, setEditing] = useState(null);
  const [showAdd, setShowAdd] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(null);
  const [showKeys, setShowKeys] = useState({});
  const [initLoading, setInitLoading] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setError("");
      const data = await getSubscribers();
      setSubscribers(data);
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  async function handleDelete(imsi) {
    try {
      await deleteSubscriber(imsi);
      setConfirmDelete(null);
      setExpanded(null);
      refresh();
    } catch (err) {
      setError(String(err.message || err));
    }
  }

  async function handleSave(data, isNew) {
    try {
      setError("");
      if (isNew) {
        await createSubscriber(data);
      } else {
        await updateSubscriber(data.imsi, data);
      }
      setEditing(null);
      setShowAdd(false);
      refresh();
    } catch (err) {
      setError(String(err.message || err));
    }
  }

  async function handleImport(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      const text = await file.text();
      const json = JSON.parse(text);
      await importSubscribers(json);
      refresh();
    } catch (err) {
      setError(String(err.message || err));
    }
    e.target.value = "";
  }

  if (loading) {
    return (
      <div className="flex min-h-[200px] items-center justify-center">
        <Loader size="lg" label="Loading subscribers…" />
      </div>
    );
  }

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h2 className="text-lg font-semibold">Subscribers</h2>
        </div>
        <div className="flex gap-2">
          <label className="cursor-pointer rounded bg-slate-700 px-3 py-1.5 text-xs font-medium text-slate-200 hover:bg-slate-600 transition-colors">
            Import JSON
            <input type="file" accept=".json" onChange={handleImport} className="hidden" />
          </label>
          <button
            type="button"
            onClick={() => { setShowAdd(true); setEditing(structuredClone(EMPTY_SUB)); }}
            className="rounded bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-500 transition-colors"
          >
            Add Subscriber
          </button>
        </div>
      </div>

      {error && (
        <div className="mb-4 flex items-center justify-between rounded border border-rose-700 bg-rose-950/50 p-3 text-sm text-rose-300">
          <span>{error}</span>
          <button type="button" onClick={() => setError("")} className="ml-3 text-rose-400 hover:text-white">&#x2715;</button>
        </div>
      )}

      {(showAdd || editing) && (
        <SubscriberForm
          data={editing || structuredClone(EMPTY_SUB)}
          isNew={showAdd}
          onSave={handleSave}
          onCancel={() => { setEditing(null); setShowAdd(false); }}
        />
      )}

      {subscribers.length === 0 && !showAdd && (
        <div className="rounded-lg border border-slate-700 bg-slate-900 p-5 text-center">
          <p className="text-sm text-slate-400 mb-3">No subscribers found in MongoDB.</p>
          <button
            type="button"
            disabled={initLoading}
            onClick={async () => {
              setInitLoading(true);
              setError("");
              try {
                await initSubscribers();
                await refresh();
              } catch (err) {
                setError(String(err.message || err));
              } finally {
                setInitLoading(false);
              }
            }}
            className="rounded-lg bg-indigo-600 px-5 py-2 text-sm font-medium text-white transition-colors hover:bg-indigo-500 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {initLoading ? "Running subscriber import playbook..." : "Initialize from playbook"}
          </button>
          <p className="mt-2 text-[11px] text-slate-500">
            Runs phase 5 subscriber_import to seed from <code className="text-slate-400">subscribers.json</code>
          </p>
        </div>
      )}

      <div className="flex flex-col gap-2">
        {subscribers.map((sub) => (
          <div key={sub.imsi} className="rounded-lg border border-slate-700 bg-slate-900">
            <button
              type="button"
              onClick={() => setExpanded(expanded === sub.imsi ? null : sub.imsi)}
              className="flex w-full items-center gap-4 px-4 py-3 text-left text-sm"
            >
              <span className="font-mono font-semibold text-white min-w-[160px]">{sub.imsi}</span>
              <div className="flex gap-1.5">
                {(sub.slice || []).map((s, i) => (
                  <span key={i} className="rounded bg-indigo-900/40 px-1.5 py-0.5 text-[10px] text-indigo-300">
                    SST:{s.sst} SD:{s.sd || "—"}
                  </span>
                ))}
              </div>
              <span className="ml-auto text-xs text-slate-500">
                UL: {ambrStr(sub.ambr?.uplink)} / DL: {ambrStr(sub.ambr?.downlink)}
              </span>
              <span className="text-slate-600">{expanded === sub.imsi ? "\u25B2" : "\u25BC"}</span>
            </button>

            {expanded === sub.imsi && (
              <div className="border-t border-slate-800 px-4 py-3 space-y-3">
                <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-xs">
                  <div>
                    <span className="text-slate-500">Key (K)</span>
                    <div className="font-mono text-slate-300 mt-0.5">
                      {showKeys[sub.imsi] ? sub.security?.k : "••••••••••••••••"}
                      <button
                        type="button"
                        onClick={() => setShowKeys((p) => ({ ...p, [sub.imsi]: !p[sub.imsi] }))}
                        className="ml-2 text-indigo-400 hover:text-indigo-300"
                      >
                        {showKeys[sub.imsi] ? "hide" : "show"}
                      </button>
                    </div>
                  </div>
                  <div>
                    <span className="text-slate-500">OP</span>
                    <div className="font-mono text-slate-300 mt-0.5">
                      {showKeys[sub.imsi] ? (sub.security?.op || "—") : "••••••••••••••••"}
                    </div>
                  </div>
                  <div>
                    <span className="text-slate-500">AMF</span>
                    <div className="font-mono text-slate-300 mt-0.5">{sub.security?.amf || "—"}</div>
                  </div>
                  <div>
                    <span className="text-slate-500">OPc</span>
                    <div className="font-mono text-slate-300 mt-0.5">
                      {sub.security?.opc ? (showKeys[sub.imsi] ? sub.security.opc : "••••••••") : "derived"}
                    </div>
                  </div>
                </div>

                {(sub.slice || []).map((slice, si) => (
                  <div key={si} className="rounded bg-slate-950 p-2 text-xs">
                    <div className="flex items-center gap-2 mb-1.5">
                      <span className="font-semibold text-slate-200">Slice SST:{slice.sst}</span>
                      {slice.sd && <span className="text-slate-400">SD:{slice.sd}</span>}
                      {slice.default_indicator && <span className="rounded bg-emerald-900/40 px-1 py-0.5 text-[10px] text-emerald-300">default</span>}
                    </div>
                    {(slice.session || []).map((sess, sei) => (
                      <div key={sei} className="ml-2 mt-1 text-slate-400">
                        <span className="text-slate-300">APN: {sess.name}</span>
                        <span className="ml-3">QCI: {sess.qos?.index}</span>
                        <span className="ml-3">UL: {ambrStr(sess.ambr?.uplink)}</span>
                        <span className="ml-3">DL: {ambrStr(sess.ambr?.downlink)}</span>
                      </div>
                    ))}
                  </div>
                ))}

                <div className="flex gap-2 pt-1">
                  <button
                    type="button"
                    onClick={() => { setEditing(structuredClone(sub)); setShowAdd(false); }}
                    className="rounded bg-amber-600/20 px-3 py-1.5 text-xs font-medium text-amber-300 hover:bg-amber-600/30 transition-colors"
                  >
                    Edit
                  </button>
                  {confirmDelete === sub.imsi ? (
                    <div className="flex items-center gap-2 rounded border border-rose-700/40 bg-rose-950/30 px-3 py-1.5">
                      <span className="text-xs text-rose-300">Delete {sub.imsi}?</span>
                      <button type="button" onClick={() => handleDelete(sub.imsi)} className="rounded bg-rose-600 px-2 py-0.5 text-xs text-white hover:bg-rose-500">Confirm</button>
                      <button type="button" onClick={() => setConfirmDelete(null)} className="text-xs text-slate-400 hover:text-white">Cancel</button>
                    </div>
                  ) : (
                    <button
                      type="button"
                      onClick={() => setConfirmDelete(sub.imsi)}
                      className="rounded bg-rose-600/20 px-3 py-1.5 text-xs font-medium text-rose-300 hover:bg-rose-600/30 transition-colors"
                    >
                      Delete
                    </button>
                  )}
                </div>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function SubscriberForm({ data, isNew, onSave, onCancel }) {
  const [form, setForm] = useState(data);

  function setField(path, value) {
    setForm((prev) => {
      const next = structuredClone(prev);
      const parts = path.split(".");
      let obj = next;
      for (let i = 0; i < parts.length - 1; i++) {
        if (obj[parts[i]] == null) obj[parts[i]] = {};
        obj = obj[parts[i]];
      }
      obj[parts[parts.length - 1]] = value;
      return next;
    });
  }

  function setOp(value) {
    const v = (value ?? "").trim();
    setForm((prev) => ({
      ...prev,
      security: {
        ...(prev.security || {}),
        op: v === "" ? null : v,
        opc: null,
      },
    }));
  }

  function setOpc(value) {
    const v = (value ?? "").trim();
    setForm((prev) => ({
      ...prev,
      security: {
        ...(prev.security || {}),
        opc: v === "" ? null : v,
        op: null,
      },
    }));
  }

  return (
    <div className="mb-4 rounded-lg border border-indigo-700/40 bg-slate-900 p-4">
      <div className="mb-3 font-semibold text-white">{isNew ? "Add Subscriber" : `Edit ${form.imsi}`}</div>
      <div className="grid grid-cols-2 gap-3 text-xs">
        <FormField label="IMSI" value={form.imsi} onChange={(v) => setField("imsi", v)} disabled={!isNew} mono />
        <FormField label="Key (K)" value={form.security?.k || ""} onChange={(v) => setField("security.k", v)} mono />
        <FormField label="OP" value={form.security?.op ?? ""} onChange={setOp} mono />
        <FormField label="OPc (optional)" value={form.security?.opc ?? ""} onChange={setOpc} mono />
        <FormField label="AMF" value={form.security?.amf || ""} onChange={(v) => setField("security.amf", v)} mono />

        <div className="col-span-2 border-t border-slate-800 pt-2 mt-1">
          <div className="text-slate-400 mb-1">Aggregate AMBR</div>
          <div className="grid grid-cols-2 gap-3">
            <FormField label="UL (Mbps)" value={form.ambr?.uplink?.value ?? ""} onChange={(v) => setField("ambr.uplink.value", Number(v))} type="number" />
            <FormField label="DL (Mbps)" value={form.ambr?.downlink?.value ?? ""} onChange={(v) => setField("ambr.downlink.value", Number(v))} type="number" />
          </div>
        </div>

        <div className="col-span-2 border-t border-slate-800 pt-2 mt-1">
          <div className="text-slate-400 mb-1">Default Slice</div>
          <div className="grid grid-cols-3 gap-3">
            <FormField label="SST" value={form.slice?.[0]?.sst ?? 1} onChange={(v) => setField("slice.0.sst", Number(v))} type="number" />
            <FormField label="SD" value={form.slice?.[0]?.sd || ""} onChange={(v) => setField("slice.0.sd", v)} mono />
            <FormField label="APN" value={form.slice?.[0]?.session?.[0]?.name || ""} onChange={(v) => setField("slice.0.session.0.name", v)} />
            <FormField label="QCI" value={form.slice?.[0]?.session?.[0]?.qos?.index ?? 9} onChange={(v) => setField("slice.0.session.0.qos.index", Number(v))} type="number" />
            <FormField label="Session UL (Mbps)" value={form.slice?.[0]?.session?.[0]?.ambr?.uplink?.value ?? 1000} onChange={(v) => setField("slice.0.session.0.ambr.uplink.value", Number(v))} type="number" />
            <FormField label="Session DL (Mbps)" value={form.slice?.[0]?.session?.[0]?.ambr?.downlink?.value ?? 1000} onChange={(v) => setField("slice.0.session.0.ambr.downlink.value", Number(v))} type="number" />
          </div>
        </div>
      </div>

      <div className="mt-4 flex gap-2">
        <button type="button" onClick={() => onSave(form, isNew)} className="rounded bg-indigo-600 px-4 py-1.5 text-xs font-medium text-white hover:bg-indigo-500 transition-colors">
          {isNew ? "Create" : "Save"}
        </button>
        <button type="button" onClick={onCancel} className="rounded bg-slate-700 px-4 py-1.5 text-xs text-slate-300 hover:bg-slate-600 transition-colors">
          Cancel
        </button>
      </div>
    </div>
  );
}

function FormField({ label, value, onChange, disabled, mono, type = "text" }) {
  return (
    <div>
      <label className="block text-slate-500 mb-0.5">{label}</label>
      <input
        type={type}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        className={`w-full rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-slate-200 focus:border-indigo-500 focus:outline-none disabled:opacity-50 ${mono ? "font-mono" : ""}`}
      />
    </div>
  );
}
