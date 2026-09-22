// Asset Identity Map: a standalone route (not a tab switch inside Northbound),
// because this is a full CRUD surface (table, guided add/edit wizard, discover,
// import/export) that deserves its own page rather than living behind a small
// header switcher where it competes for attention with Deploy adapter.
import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { IconArrowLeft } from "../components/icons";
import { Panel, Modal, Field, inputCls, btn } from "../components/ui";
import { useToast } from "../context/ToastContext";
import { env } from "../runtime-env";
import { KEYCLOAK_AUTHORITY } from "../auth/oidc";
import {
  getNorthboundAdapters,
  getNorthboundAssets,
  setNorthboundAssets,
  getNorthboundDiscoverable,
  getClientSecret,
} from "../api";

// Copy-paste retrieval: the CAMARA retrieve call as a ready snippet for the
// operator's OWN terminal (curl / PowerShell / Python), the same pattern as the
// IAM token block. It does NOT fire from the browser; it assumes $TOKEN is
// already set (mint it from Settings -> IAM, camara-api-demo). The gateway URL is
// derived from the dashboard hostname, matching the kelt-camara.<base> route.
// Self-contained: mints a token (camara-api-demo, the tenant-scoped reference
// consumer) AND runs the retrieve in one paste, so the operator does not need a
// token beforehand. "insert secret" inlines the client secret (audit-logged,
// same as the IAM page); without it the snippet keeps the .testbed.secrets
// placeholder so nothing leaks by default.
function RetrieveSnippet({ assets }) {
  const [lang, setLang] = useState("curl");
  const [assetId, setAssetId] = useState("");
  const [maxAge, setMaxAge] = useState("0");
  const [copied, setCopied] = useState(false);
  const [secret, setSecret] = useState(null);
  const [secretErr, setSecretErr] = useState("");

  const { protocol, hostname } = window.location;
  const isIp = /^\d{1,3}(\.\d{1,3}){3}$/.test(hostname) || hostname === "localhost";
  const labels = hostname.split(".");
  const base = labels.slice(1).join(".");
  const prefix = labels[0].replace(/-(dashboard|dev)$/, "");
  const url = (!isIp && base && prefix)
    ? `${protocol}//${prefix}-camara.${base}/location-retrieval/v0.5/retrieve`
    : "https://<prefix>-camara.<base>/location-retrieval/v0.5/retrieve";
  const tokenUrl = KEYCLOAK_AUTHORITY
    ? `${KEYCLOAK_AUTHORITY.replace(/\/$/, "")}/protocol/openid-connect/token`
    : "https://<keycloak>/realms/<realm>/protocol/openid-connect/token";

  const list = Array.isArray(assets) ? assets : [];
  const asset = assetId.trim() || "<assetId>";
  const age = String(maxAge).trim() || "0";
  const bodyJson = `{"device":{"assetId":"${asset}"},"maxAge":${age}}`;
  const secretField = secret || "<paste-from-.testbed.secrets>";

  const snippets = {
    curl: `TOKEN=$(curl -s -X POST ${tokenUrl} \\
  --data-urlencode grant_type=client_credentials \\
  --data-urlencode client_id=camara-api-demo \\
  --data-urlencode 'client_secret=${secretField}' \\
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')
echo "$TOKEN"

curl -s -i -X POST ${url} \\
  -H "Authorization: Bearer $TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '${bodyJson}'`,
    powershell: `$token = (Invoke-RestMethod -Method Post -Uri "${tokenUrl}" -Body @{
  grant_type    = 'client_credentials';
  client_id     = 'camara-api-demo';
  client_secret = '${secretField}'
}).access_token
$token

$r = Invoke-WebRequest -Method Post -Uri "${url}" -Headers @{ Authorization = "Bearer $token" } -ContentType 'application/json' -Body '${bodyJson}'
$r.Headers['x-correlator']; $r.Content`,
    python: `import requests

tok = requests.post("${tokenUrl}", data={
    "grant_type": "client_credentials",
    "client_id": "camara-api-demo",
    "client_secret": "${secretField}",
}).json()["access_token"]
print(tok)

r = requests.post("${url}",
    headers={"Authorization": f"Bearer {tok}"},
    json={"device": {"assetId": "${asset}"}, "maxAge": ${age}})
print(r.status_code, r.headers.get("x-correlator"))
print(r.json())`,
  };
  const snippet = snippets[lang];
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(snippet);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch { /* restrictive clipboard context: user selects manually */ }
  };
  const reveal = async () => {
    setSecretErr("");
    try {
      const res = await getClientSecret("camara-api-demo");
      if (res.found) setSecret(res.secret);
      else setSecretErr(`${res.env_key} is not in .testbed.secrets on the host (realm has the changeme default)`);
    } catch (e) {
      setSecretErr(e?.message || "could not read the secret");
    }
  };

  return (
    <Panel title="Test retrieval" hint="The full CAMARA retrieve, ready to paste into your own terminal: it mints a token (camara-api-demo) and runs the retrieve in one go. Pick an onboarded asset, insert the secret, copy, and run.">
      <div className="flex flex-wrap items-end gap-6">
        <label className="flex flex-col gap-1.5 text-[11px] text-slate-400">
          asset
          <select className={`${inputCls} w-64`} value={assetId} disabled={!list.length}
            onChange={(e) => setAssetId(e.target.value)}>
            <option value="">{assets == null ? "loading…" : list.length ? "select an asset…" : "no assets onboarded"}</option>
            {list.map((a) => (
              <option key={a.assetId} value={a.assetId}>{a.assetId}{a.org ? ` · ${a.org}` : ""}</option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1.5 text-[11px] text-slate-400">
          maxAge (s)
          <input className={`${inputCls} w-24`} value={maxAge} onChange={(e) => setMaxAge(e.target.value)} />
        </label>
      </div>
      <div className="mt-5 rounded-lg border border-slate-800 bg-slate-950 p-3.5 text-[11px] font-mono text-slate-300">
        <div className="mb-2.5 flex gap-1.5">
          {[["curl", "curl"], ["powershell", "PowerShell"], ["python", "Python"]].map(([id, label]) => (
            <button key={id} type="button" onClick={() => setLang(id)}
              className={`rounded px-2.5 py-1 text-[10px] ${lang === id ? "bg-slate-700 text-slate-100" : "bg-slate-900 text-slate-400 hover:bg-slate-800"}`}>
              {label}
            </button>
          ))}
        </div>
        <pre className="whitespace-pre-wrap break-all leading-relaxed">{snippet}</pre>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <button type="button" onClick={copy}
            className="rounded bg-slate-800 px-2.5 py-1 text-[10px] text-slate-300 hover:bg-slate-700">
            {copied ? "copied" : "copy"}
          </button>
          <button type="button" onClick={secret ? () => setSecret(null) : reveal}
            className="rounded bg-slate-800 px-2.5 py-1 text-[10px] text-slate-300 hover:bg-slate-700">
            {secret ? "hide secret" : "insert secret"}
          </button>
          <span className="text-[10px] text-slate-500">maxAge 0 = bypass cache · -i shows x-correlator</span>
          {secretErr && <span className="text-[10px] text-amber-400">{secretErr}</span>}
        </div>
      </div>
    </Panel>
  );
}

const ASSET_KINDS = ["uwb-tag", "tool", "pallet", "forklift", "asset", "ue"];
// Fallback only, used until the live adapter names load. A vendor's own source name
// appears once its adapter is deployed, it is never listed here.
const ASSET_SOURCES = ["wifi", "fiveg", "gnss", "synthetic"];
const EMPTY_CAP = { source: "synthetic", positioningId: "" };
const EMPTY_ASSET = { assetId: "", kind: "asset", org: env("VITE_CAMARA_ORG", "demo"), label: "", metadata: {}, capabilities: [{ ...EMPTY_CAP }] };
const ID_RE = /^[A-Za-z0-9._:-]{1,128}$/;
const ORG_RE = /^[a-z0-9-]{1,64}$/;

// v4 names; a v3 file still says asset_id / positioning_id and a v2 one is flat.
const capId = (c) => (c?.positioningId ?? c?.positioning_id ?? "").trim();
const assetIdOf = (a) => (a?.assetId ?? a?.asset_id ?? "").trim();
function assetCapabilities(a) {
  if (Array.isArray(a?.capabilities) && a.capabilities.length) {
    return a.capabilities.map((c) => ({ source: (c?.source || "").trim(), positioningId: capId(c) }));
  }
  if (a?.source || capId(a)) {
    return [{ source: (a.source || "").trim(), positioningId: capId(a) }];
  }
  return [];
}
function normalizeAsset(a) {
  const { source, positioningId, positioning_id, asset_id, simulated, ...rest } = a || {};
  return { ...rest, assetId: assetIdOf(a), capabilities: assetCapabilities(a) };
}
const isSynthetic = (a) => (a?.capabilities || []).some((c) => c?.source === "synthetic");
const HELP = {
  assetId: "Public CAMARA handle the consumer queries by (device.assetId). A business id like pkg-4471, not a phone number.",
  positioningId: "The id the chosen adapter fetches this device by: the vendor-native device id (used verbatim in the adapter's API call), or the engine track id for a synthetic source.",
  kind: "Entity class, surfaced in the CAMARA profile so a consumer knows what it is tracking.",
  source: "Routes the asset: the engine serves it from the registered adapter whose name equals this (?source=). Pick a deployed adapter.",
  org: "Tenant. The gateway matches it against the token org claim, so a consumer sees only its own org's assets.",
};

// test arrow char below
function AssetModal({ initial, isNew, busy, onSave, onClose }) {
  const [form, setForm] = useState(() => ({ ...EMPTY_ASSET, ...initial }));
  const [meta, setMeta] = useState(() =>
    Object.entries(initial?.metadata || {}).map(([k, v]) => ({ k, v: String(v) })));
  const [step, setStep] = useState(0);
  const [adapters, setAdapters] = useState([]);
  useEffect(() => {
    getNorthboundAdapters().then((a) => setAdapters(Array.isArray(a) ? a : [])).catch(() => {});
  }, []);
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));
  const caps = form.capabilities || [];
  const setCap = (i, patch) => set({ capabilities: caps.map((c, j) => (j === i ? { ...c, ...patch } : c)) });
  const addCap = () => set({ capabilities: [...caps, { ...EMPTY_CAP }] });
  const removeCap = (i) => set({ capabilities: caps.filter((_, j) => j !== i) });

  const liveNames = adapters.map((a) => a.name).filter(Boolean);
  const stateOf = Object.fromEntries(adapters.map((a) => [a.name, a.state]));
  const capSourceOptions = (source) => Array.from(new Set([...(liveNames.length ? liveNames : ASSET_SOURCES), source].filter(Boolean)));
  const capHint = (source) => liveNames.length
    ? (liveNames.includes(source)
        ? `Routes to adapter "${source}"${stateOf[source] && stateOf[source] !== "live" ? ` (${stateOf[source]})` : " live"}.`
        : `No registered adapter named "${source}". Deploy one (Build your own) or pick a live source.`)
    : "No adapters registered yet. Deploy one in Build your own; the values below are known modalities.";

  const idOk = ID_RE.test((form.assetId || "").trim());
  const capValid = (c) => !!(c?.source || "").trim() && ID_RE.test((c?.positioningId || "").trim());
  const capsOk = caps.length >= 1 && caps.every(capValid);
  const orgOk = ORG_RE.test((form.org || "").trim());
  const STEPS = [
    { id: "identity", label: "Identity", valid: idOk },
    { id: "capabilities", label: "Capabilities", valid: capsOk },
    { id: "details", label: "Details", valid: orgOk },
  ];
  const last = STEPS.length - 1;
  const canNext = STEPS[step].valid;
  const allValid = STEPS.every((s) => s.valid);

  const submit = () => {
    const metadata = {};
    for (const { k, v } of meta) { const key = k.trim(); if (key) metadata[key] = v; }
    const capabilities = caps.map((c) => ({
      source: (c.source || "").trim(),
      positioningId: (c.positioningId || "").trim(),
    }));
    onSave({ ...form, capabilities, metadata });
  };

  return (
    <Modal
      title={isNew ? "Add asset" : `Edit ${initial.assetId}`}
      hint="assetId is the CAMARA handle; positioningId is what the adapter fetches by; source = adapter that serves it; org = tenant."
      onClose={onClose}
    >
      <div className="flex flex-col gap-4 text-xs">
        <div className="flex items-center gap-1">
          {STEPS.map((s, i) => {
            const done = i < step;
            const active = i === step;
            const reachable = i <= step;
            return (
              <button
                key={s.id}
                type="button"
                disabled={!reachable}
                onClick={() => reachable && setStep(i)}
                className={`flex items-center gap-1.5 rounded px-2 py-1 transition-colors ${
                  active ? "bg-sky-600/20 text-sky-300" : done ? "text-emerald-400 hover:bg-slate-800" : "text-slate-500"
                }`}
              >
                <span className={`flex h-4 w-4 items-center justify-center rounded-full text-[9px] ${
                  active ? "bg-sky-500 text-white" : done ? "bg-emerald-500 text-white" : "bg-slate-700 text-slate-300"
                }`}>
                  {done ? "\u2713" : i + 1}
                </span>
                {s.label}
              </button>
            );
          })}
        </div>

        {step === 0 && (
          <div className="flex flex-col gap-3">
            <p className="text-[11px] text-slate-500">Who this device is publicly, and what it is.</p>
            <Field label="assetId" hint={HELP.assetId}>
              <input className={inputCls} placeholder="pkg-4471" value={form.assetId}
                disabled={!isNew} onChange={(e) => set({ assetId: e.target.value })} />
              {form.assetId && !idOk && <span className="text-[10px] text-rose-400">letters / digits / . _ : - (1-128)</span>}
            </Field>
            <Field label="kind" hint={HELP.kind}>
              <select className={inputCls} value={form.kind} onChange={(e) => set({ kind: e.target.value })}>
                {ASSET_KINDS.map((k) => <option key={k} value={k}>{k}</option>)}
              </select>
            </Field>
          </div>
        )}

        {step === 1 && (
          <div className="flex flex-col gap-3">
            <p className="text-[11px] text-slate-500">How this asset is located. Add one capability per source it is tracked by (a UWB tag AND a WiFi radio = two rows); the gateway fuses them into one fix.</p>
            {caps.map((c, i) => (
              <div key={i} className="rounded border border-slate-800 bg-slate-950/40 p-2">
                <div className="mb-1.5 flex items-center justify-between">
                  <span className="text-[10px] font-medium uppercase tracking-wide text-slate-500">capability {i + 1}</span>
                  {caps.length > 1 && (
                    <button type="button" className="px-1 text-rose-400 hover:text-rose-300"
                      onClick={() => removeCap(i)} aria-label="Remove capability">{"\u2715"}</button>
                  )}
                </div>
                <Field label="source" hint={HELP.source}>
                  <select className={inputCls} value={c.source} onChange={(e) => setCap(i, { source: e.target.value })}>
                    {capSourceOptions(c.source).map((s) => (
                      <option key={s} value={s}>{s}{stateOf[s] && stateOf[s] !== "live" ? ` (${stateOf[s]})` : ""}</option>
                    ))}
                  </select>
                  <span className={`text-[10px] ${liveNames.includes(c.source) && stateOf[c.source] === "live" ? "text-emerald-500" : "text-amber-500"}`}>{capHint(c.source)}</span>
                </Field>
                <Field label="positioningId" hint={HELP.positioningId}>
                  <div className="flex items-center gap-2">
                    <input className={`${inputCls} flex-1`} placeholder="vendor device id (synthetic: engine track id)"
                      value={c.positioningId} onChange={(e) => setCap(i, { positioningId: e.target.value })} />
                    <button type="button" className={btn.ghost} disabled={!form.assetId}
                      onClick={() => setCap(i, { positioningId: form.assetId })}>= assetId</button>
                  </div>
                  {c.positioningId && !ID_RE.test(c.positioningId.trim()) && <span className="text-[10px] text-rose-400">letters / digits / . _ : - (1-128)</span>}
                </Field>
              </div>
            ))}
            <button type="button" className={btn.ghost} onClick={addCap}>+ capability</button>
          </div>
        )}

        {step === 2 && (
          <div className="flex flex-col gap-3">
            <p className="text-[11px] text-slate-500">Tenant, and optional presentation details.</p>
            <Field label="org" hint={HELP.org}>
              <input className={inputCls} placeholder="acme" value={form.org} onChange={(e) => set({ org: e.target.value })} />
              {form.org && !orgOk && <span className="text-[10px] text-rose-400">lowercase letters / digits / - (1-64)</span>}
            </Field>
            <Field label="label (optional)" hint="Human-readable name shown in UIs.">
              <input className={inputCls} placeholder="Forklift 7 (bay A)" value={form.label} onChange={(e) => set({ label: e.target.value })} />
            </Field>
            <div className="rounded border border-slate-800 bg-slate-950/40 p-2">
              <div className="mb-1 flex items-center justify-between">
                <span className="text-[10px] font-medium uppercase tracking-wide text-slate-500">metadata (optional)</span>
                <button type="button" className={btn.ghost} onClick={() => setMeta((m) => [...m, { k: "", v: "" }])}>+ field</button>
              </div>
              {meta.length === 0 ? (
                <p className="text-[10px] text-slate-600">Free-form per-asset fields (e.g. floor, bay).</p>
              ) : (
                <div className="flex flex-col gap-1.5">
                  {meta.map((row, i) => (
                    <div key={i} className="flex items-center gap-2">
                      <input className={`${inputCls} w-32`} placeholder="floor" value={row.k}
                        onChange={(e) => setMeta((m) => m.map((r, j) => (j === i ? { ...r, k: e.target.value } : r)))} />
                      <input className={`${inputCls} flex-1`} placeholder="3" value={row.v}
                        onChange={(e) => setMeta((m) => m.map((r, j) => (j === i ? { ...r, v: e.target.value } : r)))} />
                      <button type="button" className="px-1 text-rose-400 hover:text-rose-300"
                        onClick={() => setMeta((m) => m.filter((_, j) => j !== i))} aria-label="Remove field">{"\u2715"}</button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        <div className="mt-1 flex items-center justify-between gap-2 border-t border-slate-800 pt-3">
          <button type="button" className={btn.ghost} onClick={onClose}>Cancel</button>
          <div className="flex items-center gap-2">
            {step > 0 && <button type="button" className={btn.ghost} onClick={() => setStep((s) => s - 1)}>Back</button>}
            {step < last ? (
              <button type="button" className={btn.sky} disabled={!canNext} onClick={() => canNext && setStep((s) => s + 1)}>Next</button>
            ) : (
              <button type="button" className={btn.sky} disabled={busy || !allValid} onClick={submit}>{busy ? "Saving…" : "Save asset"}</button>
            )}
          </div>
        </div>
      </div>
    </Modal>
  );
}

// Validate a parsed asset array before a replace-all import: mirrors the per-asset rules
// (assetId/org patterns, at least one capability with a source + valid positioningId)
// plus duplicate detection, and lifts a v3 (snake_case ids) or v2 (flat) entry to the v4
// shape so an older hand-written or exported file still imports. The gateway validates authoritatively on
// PUT; this just catches obvious errors before we replace the whole store.
function validateImportedAssets(arr) {
  const errors = [];
  const seen = new Set();
  const clean = arr.map((a, i) => {
    const id = assetIdOf(a);
    const org = (a?.org || "").trim();
    const where = id || `#${i + 1}`;
    const capabilities = assetCapabilities(a);
    if (!ID_RE.test(id)) errors.push(`${where}: bad assetId`);
    else if (seen.has(id)) errors.push(`${where}: duplicate assetId`);
    if (!ORG_RE.test(org)) errors.push(`${where}: bad org`);
    if (capabilities.length === 0) errors.push(`${where}: no capabilities`);
    else capabilities.forEach((c, j) => {
      if (!c.source) errors.push(`${where}: capability ${j + 1} has no source`);
      if (!ID_RE.test(c.positioningId)) errors.push(`${where}: capability ${j + 1} bad positioningId`);
    });
    if (id) seen.add(id);
    const { source, positioningId, positioning_id, asset_id, simulated, ...rest } = a || {};
    return {
      ...rest, assetId: id, org,
      kind: a?.kind || "asset",
      label: (a?.label || "").trim(),
      capabilities: capabilities.map((c) => ({ source: c.source, positioningId: c.positioningId })),
      metadata: (a && typeof a.metadata === "object" && a.metadata) || {},
    };
  });
  return { errors, clean };
}

// Onboarding: devices the engine sees across live adapters that are not yet mapped
// (GET /assets/discoverable). `origin` distinguishes a vendor inventory entry (stable
// registry, bulk) from an on-air observation (wifi, per-activity). Onboarding is never
// automatic, picking one opens the Add-asset wizard prefilled; the operator confirms
// and commits an explicit PUT /assets.
const ORIGIN_BADGE = {
  inventory: { cls: "bg-sky-500/15 text-sky-300", label: "inventory", title: "vendor registry entry (stable)" },
  observed: { cls: "bg-emerald-500/15 text-emerald-300", label: "observed", title: "seen on air (per-activity)" },
};

// Device role (schema-driven, from the adapter's classify block): "infrastructure" = a
// fixed node (e.g. a UWB anchor) that is never onboarded as an asset; "asset" or absent =
// onboardable. Absent until the adapter's schema declares a classify rule, in which case
// every candidate is treated as onboardable.
const ROLE_BADGE = {
  asset: { cls: "bg-emerald-500/15 text-emerald-300", label: "asset" },
  infrastructure: { cls: "bg-slate-700/60 text-slate-400", label: "infrastructure" },
};

// One discoverable candidate. onOnboard=null renders it read-only (used for the anchors
// group). Shows origin, the vendor-native deviceType, and role badges when present.
function CandidateRow({ c, onOnboard }) {
  const ob = ORIGIN_BADGE[c.origin] || { cls: "bg-slate-700/60 text-slate-400", label: c.origin || "?", title: "" };
  const rb = c.role ? ROLE_BADGE[c.role] : null;
  const sub = c.name || ((c.label && c.label !== c.id) ? c.label : "");
  return (
    <div className="flex items-center gap-3 py-2 text-xs">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono font-semibold text-slate-200">{c.id}</span>
          {c.source && <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[9px] text-slate-400">{c.source}</span>}
          <span className={`rounded px-1.5 py-0.5 text-[9px] ${ob.cls}`} title={ob.title}>{ob.label}</span>
          {c.sourceClass && <span className="rounded bg-indigo-500/15 px-1.5 py-0.5 text-[9px] text-indigo-300" title="normalized source class">{c.sourceClass}</span>}
          {c.deviceType && <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[9px] text-slate-400" title="vendor-native device type">{c.deviceType}</span>}
          {rb && <span className={`rounded px-1.5 py-0.5 text-[9px] ${rb.cls}`}>{rb.label}</span>}
        </div>
        {(sub || c.lastSeen) && (
          <div className="mt-0.5 flex items-center gap-2 text-[10px] text-slate-600">
            {sub && <span>{sub}</span>}
            {c.lastSeen && <span>seen {c.lastSeen}</span>}
          </div>
        )}
      </div>
      {onOnboard && <button type="button" className={btn.sky} onClick={() => onOnboard(c)}>onboard</button>}
    </div>
  );
}

function DiscoverModal({ onOnboard, onClose }) {
  const [rows, setRows] = useState(null); // null = loading
  const [err, setErr] = useState("");
  useEffect(() => {
    getNorthboundDiscoverable()
      .then((d) => setRows(
        Array.isArray(d?.candidates) ? d.candidates
          : Array.isArray(d?.devices) ? d.devices
          : Array.isArray(d) ? d : []
      ))
      .catch((e) => { setErr(e.message || "could not load discoverable devices"); setRows([]); });
  }, []);

  const infra = (rows || []).filter((c) => c.role === "infrastructure");
  const onboardable = (rows || []).filter((c) => c.role !== "infrastructure");

  return (
    <Modal
      title="Discover devices"
      hint="Devices live adapters report but that are not yet onboarded. Pick one to prefill an asset; onboarding stays an explicit save."
      wide
      onClose={onClose}
    >
      {rows === null ? (
        <p className="text-xs text-slate-500">Scanning adapters…</p>
      ) : err ? (
        <div className="rounded bg-amber-500/10 px-3 py-2 text-xs text-amber-300">{err}</div>
      ) : rows.length === 0 ? (
        <p className="text-xs text-slate-500">No new devices. Every device a live adapter reports is already onboarded, or no adapter advertises a device inventory yet.</p>
      ) : (
        <div className="flex flex-col gap-4">
          {onboardable.length === 0 ? (
            <p className="text-xs text-slate-500">Every reported device is fixed infrastructure; nothing to onboard.</p>
          ) : (
            <div className="flex flex-col divide-y divide-slate-800/60">
              {onboardable.map((c) => <CandidateRow key={`${c.source}/${c.id}`} c={c} onOnboard={onOnboard} />)}
            </div>
          )}
          {infra.length > 0 && (
            <div className="rounded border border-slate-800 bg-slate-950/40 p-2">
              <div className="mb-1 text-[10px] font-medium uppercase tracking-wide text-slate-500">
                {infra.length} infrastructure device{infra.length > 1 ? "s" : ""}, fixed, not onboardable
              </div>
              <div className="flex flex-col divide-y divide-slate-800/40 opacity-70">
                {infra.map((c) => <CandidateRow key={`${c.source}/${c.id}`} c={c} onOnboard={null} />)}
              </div>
            </div>
          )}
        </div>
      )}
    </Modal>
  );
}

export default function NorthboundAssetsPage() {
  const toast = useToast();
  const [assets, setAssets] = useState(null); // null = loading
  const [editing, setEditing] = useState(null);
  const [isNew, setIsNew] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [importConfirm, setImportConfirm] = useState(null); // { count, next }
  const [discoverOpen, setDiscoverOpen] = useState(false);
  const fileRef = useRef(null);

  // Onboard a discovered device: open the Add-asset wizard PREFILLED from the candidate as
  // a single capability (source from the candidate, positioningId = the id the adapter
  // fetches by, both editable). The operator confirms org/kind and can add more sources
  // before saving, onboarding is never silent.
  const onboardCandidate = (c) => {
    setDiscoverOpen(false);
    setEditing({
      ...EMPTY_ASSET,
      assetId: c.id || "",
      capabilities: [{
        source: c.source || "synthetic",
        positioningId: c.id || "",
      }],
      label: c.name || c.label || "",
      metadata: {
        ...(c.sourceClass ? { sourceClass: c.sourceClass } : {}),
        ...(c.deviceType ? { deviceType: c.deviceType } : {}),
      },
    });
    setIsNew(true);
  };

  const load = useCallback(() => {
    setErr("");
    getNorthboundAssets()
      .then((d) => setAssets(Array.isArray(d?.assets) ? d.assets.map(normalizeAsset) : []))
      .catch((e) => { setErr(e.message || "could not load /assets"); setAssets([]); });
  }, []);
  useEffect(() => { load(); }, [load]);

  const saveAll = async (next) => {
    setBusy(true);
    try {
      await setNorthboundAssets({ version: 4, assets: next });
      setAssets(next);
      toast.success("Assets saved, gateway store updated");
    } catch (e) { toast.error(`Save failed: ${e.message}`); throw e; }
    finally { setBusy(false); }
  };

  const upsert = async (a) => {
    const id = (a.assetId || "").trim();
    const org = (a.org || "").trim();
    const caps = a.capabilities || [];
    if (!ID_RE.test(id)) return toast.error("assetId: letters/digits/._:- (1-128)");
    if (!ORG_RE.test(org)) return toast.error("org: lowercase letters/digits/- (1-64)");
    if (caps.length === 0) return toast.error("add at least one capability");
    for (const c of caps) {
      if (!(c.source || "").trim()) return toast.error("every capability needs a source");
      if (!ID_RE.test((c.positioningId || "").trim())) return toast.error("positioningId: letters/digits/._:- (1-128)");
    }
    const clean = { ...a, assetId: id, org, label: (a.label || "").trim() };
    const next = [...(assets || []).filter((x) => x.assetId !== id), clean];
    try { await saveAll(next); setEditing(null); } catch { /* toast shown */ }
  };

  const remove = async (id) => {
    try { await saveAll((assets || []).filter((x) => x.assetId !== id)); } catch { /* toast shown */ }
  };

  const doExport = () => {
    const body = JSON.stringify({ version: 4, assets: assets || [] }, null, 2);
    const url = URL.createObjectURL(new Blob([body], { type: "application/json" }));
    const a = document.createElement("a");
    a.href = url; a.download = "assets.json";
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  };

  const onImportFile = async (e) => {
    const file = e.target.files?.[0];
    e.target.value = ""; // let the same file be re-picked after a fix
    if (!file) return;
    let parsed;
    try { parsed = JSON.parse(await file.text()); }
    catch { return toast.error("Import failed: not valid JSON"); }
    const arr = Array.isArray(parsed) ? parsed : Array.isArray(parsed?.assets) ? parsed.assets : null;
    if (!arr) return toast.error("Import failed: expected an array or { assets: [...] }");
    if (arr.length === 0) return toast.error("Import failed: file has no assets");
    const { errors, clean } = validateImportedAssets(arr);
    if (errors.length) {
      return toast.error(`Import rejected: ${errors.slice(0, 3).join("; ")}${errors.length > 3 ? ` (+${errors.length - 3} more)` : ""}`);
    }
    setImportConfirm({ count: clean.length, next: clean });
  };

  const confirmImport = async () => {
    const c = importConfirm; setImportConfirm(null);
    try { await saveAll(c.next); } catch { /* toast shown */ }
  };

  return (
    <div className="svc-fade flex flex-col gap-5 pb-8">
      <header className="flex flex-col gap-2">
        <Link to="/services/northbound" className="inline-flex w-fit items-center gap-1 text-xs text-slate-400 hover:text-slate-200">
          <IconArrowLeft size={14} /> Northbound
        </Link>
        <h2 className="text-lg font-semibold text-slate-100">Asset Identity Map</h2>
        <p className="text-xs text-slate-500">
          CAMARA private-asset profile: assetId maps to a positioning source. The gateway is the authority
          (GET/PUT /assets); the engine broadcasts each device from its adapter's capability, so an onboarded
          asset goes live as soon as its adapter reports it.
        </p>
      </header>

      <div className="flex flex-col gap-4">
      <Panel title="Assets">
        {assets === null ? (
          <p className="text-xs text-slate-500">Loading…</p>
        ) : (
          <div className="flex flex-col gap-3">
            {err && <div className="rounded bg-amber-500/10 px-3 py-2 text-xs text-amber-300">{err}</div>}
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <button type="button" className={btn.ghost} disabled={busy || (assets || []).length === 0}
                  title="download the current map as assets.json" onClick={doExport}>export</button>
                <button type="button" className={btn.ghost} disabled={busy}
                  title="replace the whole map from an assets.json file" onClick={() => fileRef.current?.click()}>import</button>
                <input ref={fileRef} type="file" accept="application/json,.json" className="hidden" onChange={onImportFile} />
              </div>
              <div className="flex items-center gap-2">
                <button type="button" className={btn.ghost} title="devices seen by live adapters, not yet onboarded" onClick={() => setDiscoverOpen(true)}>Discover devices</button>
                <button type="button" className={btn.sky} onClick={() => { setEditing({ ...EMPTY_ASSET }); setIsNew(true); }}>+ Add asset</button>
              </div>
            </div>
            {assets.length === 0 ? (
              <p className="text-xs text-slate-500">No assets yet. Add one to expose it through the CAMARA Location API by <span className="font-mono">assetId</span>.</p>
            ) : (
              <table className="w-full text-xs">
                <thead><tr className="text-left text-slate-400">
                  <th className="py-1">assetId</th><th>kind</th><th>org</th><th>capabilities</th><th></th>
                </tr></thead>
                <tbody>
                  {assets.map((a) => (
                    <tr key={a.assetId} className="border-t border-slate-800 align-top">
                      <td className="py-1 font-mono text-slate-200">{a.assetId}{isSynthetic(a) && <span className="ml-1 rounded bg-amber-500/20 px-1 text-[9px] text-amber-300" title="Synthetic source, not real hardware">SYNTHETIC</span>}</td>
                      <td>{a.kind}</td><td>{a.org}</td>
                      <td>
                        <div className="flex flex-wrap gap-1">
                          {(a.capabilities || []).map((c, i) => (
                            <span key={i} className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px]">
                              <span className="text-slate-300">{c.source}</span>
                              <span className="text-slate-500"> </span>
                              <span className="font-mono text-slate-400">{c.positioningId}</span>
                            </span>
                          ))}
                        </div>
                      </td>
                      <td className="text-right">
                        <button type="button" className="mr-3 text-sky-400 hover:underline" onClick={() => { setEditing({ ...EMPTY_ASSET, ...a }); setIsNew(false); }}>edit</button>
                        <button type="button" className="text-rose-400 hover:underline disabled:opacity-40" disabled={busy} onClick={() => remove(a.assetId)}>delete</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}
        {discoverOpen && (
          <DiscoverModal onOnboard={onboardCandidate} onClose={() => setDiscoverOpen(false)} />
        )}
        {editing && (
          <AssetModal initial={editing} isNew={isNew} busy={busy} onSave={upsert} onClose={() => setEditing(null)} />
        )}
        {importConfirm && (
          <Modal
            title="Replace all assets?"
            hint={`Imports ${importConfirm.count} asset${importConfirm.count > 1 ? "s" : ""} and replaces the current ${(assets || []).length} in the gateway store. This cannot be undone.`}
            onClose={() => setImportConfirm(null)}
          >
            <div className="flex justify-end gap-2">
              <button type="button" className={btn.ghost} onClick={() => setImportConfirm(null)}>Cancel</button>
              <button type="button" className={btn.sky} disabled={busy} onClick={confirmImport}>{busy ? "Importing…" : `Replace with ${importConfirm.count}`}</button>
            </div>
          </Modal>
        )}
      </Panel>
      <RetrieveSnippet assets={assets} />
      </div>
    </div>
  );
}


