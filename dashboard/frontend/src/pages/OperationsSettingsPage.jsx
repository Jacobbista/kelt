import React, { useEffect, useState } from "react";
import { getRetention, setRetention } from "../api";
import useResource from "../data/useResource";
import { Card } from "../components/Card";
import Num from "../components/Num";
import { btn, inputCls } from "../components/ui";
import { RETENTION_BOUNDS, retentionError } from "../lib/operations";

const mb = (b) => (b == null ? null : Math.round((b / 1048576) * 10) / 10);
const day = (iso) => {
  const d = iso ? new Date(iso) : null;
  return d && !Number.isNaN(d.getTime()) ? d.toLocaleDateString([], { year: "numeric", month: "2-digit", day: "2-digit" }) : "—";
};

// How long the operations record is kept (Foundations spec, Retention): two
// limits, whichever is reached first; a running operation is never deleted;
// new values apply at the next run.
export default function OperationsSettingsPage() {
  const r = useResource("ops-retention", getRetention);
  const [days, setDays] = useState("");
  const [size, setSize] = useState("");
  const [msg, setMsg] = useState(null);
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    if (r.data) { setDays(String(r.data.max_age_days)); setSize(String(r.data.max_mb)); }
  }, [r.data?.max_age_days, r.data?.max_mb]); // eslint-disable-line react-hooks/exhaustive-deps
  const changed = !!r.data && (days !== String(r.data.max_age_days) || size !== String(r.data.max_mb));
  const invalid = changed ? retentionError(days, size) : null;
  const save = async () => {
    setSaving(true); setMsg(null);
    try {
      await setRetention(Number(days), Number(size));
      await r.refresh({ after: true });
      setMsg({ ok: true, text: "Saved. The limits apply at the next run." });
    } catch (e) { setMsg({ ok: false, text: String(e?.message || e) }); }
    finally { setSaving(false); }
  };
  const [dLo, dHi] = RETENTION_BOUNDS.days;
  const [mLo, mHi] = RETENTION_BOUNDS.mb;
  return (
    <div className="grid max-w-3xl items-start gap-3">
      <Card title="What the record holds now" updatedAt={r.updatedAt} busy={!r.data && !r.error}
        error={r.error ? String(r.error.message || r.error) : null}>
        <div className="grid grid-cols-3 gap-3 p-4">
          <div><Num value={r.data ? r.data.runs : null} className="text-2xl font-bold text-white" /><div className="mt-0.5 text-xs text-slate-400">runs</div></div>
          <div><Num value={r.data ? mb(r.data.bytes) : null} unit="MB" className="text-2xl font-bold text-white" /><div className="mt-0.5 text-xs text-slate-400">on disk</div></div>
          <div><div className="font-mono text-sm tabular-nums text-slate-200">{r.data ? (r.data.oldest ? day(r.data.oldest) : "no runs yet") : "…"}</div><div className="mt-1 text-xs text-slate-400">oldest run</div></div>
        </div>
      </Card>
      <Card title="How long it is kept" sub="Whichever limit is reached first. A running operation is never deleted.">
        <div className="flex flex-col gap-3 p-4">
          <div className="flex flex-wrap items-end gap-4 text-xs text-slate-400">
            <label className="flex flex-col gap-1">Days ({dLo}–{dHi})
              <input value={days} onChange={(e) => setDays(e.target.value)} inputMode="numeric" disabled={!r.data} className={`${inputCls} w-24 font-mono`} />
            </label>
            <label className="flex flex-col gap-1">Total size, MB ({mLo}–{mHi})
              <input value={size} onChange={(e) => setSize(e.target.value)} inputMode="numeric" disabled={!r.data} className={`${inputCls} w-24 font-mono`} />
            </label>
            <button type="button" onClick={save} disabled={!changed || !!invalid || saving} className={btn.indigo}>{saving ? "Saving…" : "Save"}</button>
          </div>
          {invalid && <p className="text-xs text-amber-300">{invalid}</p>}
          <p className="text-[11px] text-slate-500">
            Stored in <span className="font-mono">.testbed.env</span> (<span className="font-mono">KELT_OPS_MAX_AGE_DAYS</span>, <span className="font-mono">KELT_OPS_MAX_MB</span>), read by the runner after each run.
          </p>
          {msg && <p className={`text-xs ${msg.ok ? "text-emerald-300" : "text-rose-300"}`}>{msg.text}</p>}
        </div>
      </Card>
    </div>
  );
}
