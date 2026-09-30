import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { clearGnbConsole, setGnbConsole } from "../../api";
import { btn, inputCls } from "../ui";
import { ConfirmAction } from "../Card";
import Num from "../Num";

const CHEVRON = <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M3 4.5l3 3 3-3" strokeLinecap="round" strokeLinejoin="round" /></svg>;

// A row that opens a drawer inside the card (t-acc, child selectors).
function Drawer({ title, hint, children }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="t-acc" data-open={open ? "true" : "false"}>
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open}
        className="flex w-full items-center gap-2 py-2 text-left text-xs text-slate-300 transition-colors hover:text-white">
        <span className="min-w-0 flex-1">{title}</span>
        {hint && <span className="truncate font-mono text-[10px] text-slate-500">{hint}</span>}
        <span className="t-acc-chevron text-slate-500">{CHEVRON}</span>
      </button>
      <div className="t-acc-panel"><div className="t-acc-panel-inner"><div className="flex flex-col gap-2 pb-3">{children}</div></div></div>
    </div>
  );
}

// Where the console is published; the port defaults on the backend when empty.
function ConsoleForm({ con, onChanged }) {
  const [host, setHost] = useState(() => (con?.origin ? String(con.origin).split(":")[0] : ""));
  const [port, setPort] = useState(() => (con?.origin ? String(con.origin).split(":")[1] || "" : ""));
  const [saving, setSaving] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [err, setErr] = useState("");
  const save = async () => {
    setSaving(true); setErr("");
    try { await setGnbConsole(host.trim(), port.trim() ? parseInt(port, 10) : undefined); await onChanged(); }
    catch (e) { setErr(String(e?.message || e)); }
    finally { setSaving(false); }
  };
  const remove = async () => {
    setRemoving(true); setErr("");
    try { await clearGnbConsole(); setHost(""); setPort(""); await onChanged(); }
    catch (e) { setErr(String(e?.message || e)); }
    finally { setRemoving(false); }
  };
  return (
    <>
      <p className="text-[11px] text-slate-500">The gNB's management address, reachable from the worker (not its RAN address).</p>
      <div className="flex gap-2">
        <input value={host} onChange={(e) => setHost(e.target.value)} placeholder="management IP" className={`${inputCls} min-w-0 flex-1 font-mono`} />
        <input value={port} onChange={(e) => setPort(e.target.value)} placeholder="port" className={`${inputCls} w-16 font-mono`} />
      </div>
      <div className="flex flex-wrap items-start gap-2">
        <button type="button" onClick={save} disabled={saving || removing || !host.trim()} className={btn.indigo}>
          {saving ? "Checking…" : con?.configured ? "Update" : "Publish"}
        </button>
        {con?.configured && (
          <ConfirmAction label="Remove…" variant="quiet" tier="disrupt" confirmLabel="Remove the console's address"
            rows={[["Changes", "Removes the console's address from the front door."], ["Stops", "Opening the gNB console through KELT, until it is published again. The gNB keeps running."]]}
            running={removing} runningLabel="Removing…" disabledReason={saving ? "Saving" : null} onRun={remove} />
        )}
      </div>
      {err && <div className="rounded border border-rose-700/50 bg-rose-950/30 px-2 py-1.5 text-[11px] text-rose-300">{err}</div>}
    </>
  );
}

// The physical gNB: its state, its numbers, and its own console, which is how
// the gNB itself is monitored and configured.
export default function GnbCard({ phys, console: con, onConsoleChanged }) {
  const navigate = useNavigate();
  const g = phys?.gnb;
  const up = !!g?.connected && phys?.nic_state === "up";
  const cfg = phys?.config || {};
  const ngapPort = g?.ngap_port; // from the running AMF; null while it is not running
  const settings = [
    ["RAN interface IP", <>a free address in <span className="font-mono">{cfg.physical_ran_subnet}</span></>],
    ["Default gateway", <span className="font-mono">{cfg.physical_ran_gateway}</span>],
    ["AMF (NGAP, SCTP)", <span className="font-mono">{cfg.amf_physical_ran_ip}{ngapPort ? `:${ngapPort}` : ""}</span>],
    ["User-plane route", <span className="font-mono">{cfg.n3_subnet} via {cfg.physical_ran_gateway}</span>],
  ];
  return (
    <section className={`h-fit rounded-lg border p-4 transition-colors ${!phys || up ? "border-slate-700 bg-slate-900" : "border-rose-700/50 bg-rose-950/30"}`}>
      <div className="flex items-center gap-2.5">
        <span className={`h-2.5 w-2.5 rounded-full transition-colors ${!phys ? "bg-slate-500" : up ? "bg-emerald-400" : "bg-rose-400"}`} />
        <span className="text-sm font-medium text-white">gNB</span>
        <span className="ml-auto rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-400">physical</span>
      </div>
      <div className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs text-slate-400">
        <span>Address</span><span className="text-right font-mono text-slate-300">{g?.ip || "—"}</span>
        <span>NG Setup</span><span className={`text-right ${!phys ? "text-slate-500" : up ? "text-emerald-400" : "text-rose-400"}`}>{!phys ? "—" : up ? "accepted" : "none"}</span>
        <span>AMF</span><span className="text-right font-mono text-slate-300">{cfg.amf_physical_ran_ip ? `${cfg.amf_physical_ran_ip}${ngapPort ? `:${ngapPort}` : ""}` : "—"}</span>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-3 border-t border-slate-800 pt-3">
        <div><Num value={phys ? phys.counts?.ues ?? 0 : null} className="text-2xl font-bold text-white" /><div className="mt-0.5 text-xs text-slate-400">UEs</div></div>
        <div><Num value={phys ? phys.counts?.pdu ?? 0 : null} className="text-2xl font-bold text-white" /><div className="mt-0.5 text-xs text-slate-400">PDU sessions</div></div>
      </div>

      <div className="mt-3 border-t border-slate-800 pt-3">
        {con?.url ? (
          <a href={con.url} target="_blank" rel="noreferrer"
            className="flex items-center justify-between rounded bg-slate-700/60 px-3 py-2 text-xs font-medium text-slate-200 transition-colors hover:bg-slate-700">
            Open the gNB console <span aria-hidden="true">↗</span>
          </a>
        ) : (
          <p className="text-xs text-slate-500">{con ? "The gNB's console is not published yet." : "Reading the console…"}</p>
        )}
        {con?.configured && con.reachable === false && (
          <p className="mt-1.5 text-[11px] text-rose-300">Published, but the worker cannot reach it: check its address.</p>
        )}
      </div>

      <div className="mt-2 divide-y divide-slate-800 border-t border-slate-800">
        <Drawer title="Console address" hint={con?.origin || "not set"}>
          <ConsoleForm key={con?.origin || "none"} con={con} onChanged={onConsoleChanged} />
        </Drawer>
        <Drawer title="Settings for the gNB">
          <p className="text-[11px] text-slate-500">Set these on the gNB's RAN interface. They come from KELT's own configuration.</p>
          <dl className="flex flex-col gap-1.5 text-xs">
            {settings.map(([k, v]) => (
              <div key={k}><dt className="text-[10px] uppercase tracking-wide text-slate-500">{k}</dt><dd className="text-slate-300">{v}</dd></div>
            ))}
          </dl>
        </Drawer>
      </div>

      <div className="mt-2 text-right text-[10px] font-medium">
        <button type="button" onClick={() => navigate("/ue-monitor")} className="text-indigo-400 hover:text-indigo-300">UE Monitor →</button>
      </div>
    </section>
  );
}
