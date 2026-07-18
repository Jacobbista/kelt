import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { IconArrowLeft, IconRefresh, IconTrash } from "../components/icons";
import { Panel, Collapsible, Modal, Banner, Tabs, Field, Toggle, inputCls, btn } from "../components/ui";
import { useToast } from "../context/ToastContext";
import { useConfirm } from "../context/ConfirmContext";
import { env } from "../runtime-env";
import LogViewer from "../components/LogViewer";

const TABS = [
  { id: "status", label: "Status" },
  { id: "adapters", label: "Adapters" },
  { id: "assets", label: "Assets" },
];
import {
  getNorthboundServices,
  getNorthboundAdapters,
  getNorthboundContract,
  getNorthboundBindings,
  getNorthboundReadiness,
  getNorthboundServiceConfig,
  applyNorthboundServiceConfig,
  getNorthboundServiceFile,
  applyNorthboundServiceFile,
  unregisterNorthboundAdapter,
  upgradeNorthboundAdapter,
  enableNorthboundPersistence,
  updateAllNorthboundStream,
  getNorthboundVersions,
  deleteNorthboundWorkload,
  deployNorthboundImage,
  setNorthboundFusion,
  rolloutNorthboundManaged,
  getNorthboundAssets,
  setNorthboundAssets,
  getNorthboundDiscoverable,
  getNorthboundDiscoverRaw,
} from "../api";

// Generic adapters published by 5g-northbound that an operator can deploy on
// demand; bring-your-own images use the same form. Two kinds, deliberately
// distinct: a "singleton" is a self-contained source you deploy at most once
// (wifi-positioning); a "template" is the generic rest-adapter, instantiated
// once PER VENDOR (each gets its own name + REST API), so it can be deployed
// many times.
const CATALOG = [
  {
    name: "wifi-positioning",
    // Deploy-time default image; injected from all.yml (northbound_image_tags) via
    // env-config.js. Fallback tracks the same baseline for an un-injected bundle.
    image: env("VITE_NB_WIFI_IMAGE", "ghcr.io/jacobbista/5g-northbound/wifi-positioning:0.8.15"),
    kind: "singleton",
    adapterKind: "wifi",
    blurb: "Wi-Fi RSSI positioning source. Deploy one.",
  },
  {
    name: "rest-adapter",
    image: env("VITE_NB_REST_ADAPTER_IMAGE", "ghcr.io/jacobbista/5g-northbound/rest-adapter:0.8.18"),
    kind: "template",
    adapterKind: "", // per vendor: operator sets it (e.g. uwb for Wittra)
    blurb: "Generic wrapper around a vendor REST API (e.g. Wittra). Deploy one per vendor; name it after the vendor and point it at the vendor API in the env below.",
  },
];
const MANAGED = ["camara-gateway", "positioning-engine", "positioning-demo"];

// Engine registry membership/reachability (from GET /adapters `state`): live =
// heartbeat fresh and polling OK; unreachable = alive but its source/poll fails
// (e.g. vendor cloud down); stale = heartbeat aging, not re-announcing.
const ADAPTER_STATE = {
  live: { cls: "bg-emerald-500/15 text-emerald-300", label: "live" },
  unreachable: { cls: "bg-rose-500/15 text-rose-300", label: "unreachable" },
  stale: { cls: "bg-amber-500/15 text-amber-300", label: "stale" },
};

// Image tag/basename helpers. Drift ("is this behind the current release?") is
// computed by the backend against ghcr (GET /versions), not from the CATALOG pins;
// the CATALOG below is only the deploy-time default image for a catalog adapter.
const imgBasename = (image) => (image || "").split("/").pop().split("@")[0].split(":")[0];
const imgTag = (image) => { const p = (image || "").split(":"); return p.length > 1 ? p[p.length - 1] : ""; };

const PHASE_DOT = {
  Running: "bg-emerald-400",
  Pending: "bg-amber-400 animate-pulse",
  ContainerCreating: "bg-amber-400 animate-pulse",
  Terminating: "bg-slate-500 animate-pulse",
};
const phaseDot = (p) => PHASE_DOT[p] || "bg-rose-400";

// How each surface is served, from the service contract `kind`:
//  api      -> HTTP API (operator routes it by path or subdomain)
//  ui       -> browser UI (its own origin / subdomain)
//  internal -> ClusterIP only, not externally exposed
const KIND_BADGE = {
  api: { cls: "bg-sky-500/15 text-sky-300", label: "api" },
  ui: { cls: "bg-emerald-500/15 text-emerald-300", label: "ui" },
  internal: { cls: "bg-slate-700/60 text-slate-400", label: "internal" },
};

// Per-service label SUFFIX (not the full subdomain). KELT serves every surface at
// <prefix>-<suffix>.<base> (e.g. kelt-camara.<base>); the contract's `subdomain`
// field overrides the suffix when present. See docs/security/external-access.md.
const SUBDOMAIN_SUFFIX = {
  "camara-gateway": "camara",
  "positioning-demo": "demo",
  "placement-editor": "placement",
  "oauth2-proxy-placement": "placement",
};

// Resolve a service's public URL = <prefix>-<suffix>.<base>, matching the front-door
// routes. base + prefix are derived from the dashboard's OWN hostname (it is served
// at <prefix>-dashboard.<base> or <prefix>-dev.<base>), so a single config (the base
// domain) drives every link with no hardcoded origin. Null for internal services or
// when reached by IP/localhost. See docs/security/external-access.md.
function publicUrl(s) {
  if (s.kind !== "ui" && s.kind !== "api") return null;  // internal / no-contract: no public link
  const { protocol, hostname } = window.location;
  if (/^\d{1,3}(\.\d{1,3}){3}$/.test(hostname) || hostname === "localhost") return null;
  const labels = hostname.split(".");
  const base = labels.slice(1).join(".");
  const prefix = labels[0].replace(/-(dashboard|dev)$/, "");  // kelt-dashboard -> kelt
  const suffix = s.subdomain || SUBDOMAIN_SUFFIX[s.name];
  if (!suffix || !base || !prefix) return null;
  const host = `${prefix}-${suffix}.${base}`;
  return { url: `${protocol}//${host}${s.kind === "api" ? "/docs" : "/"}`, label: host };
}

// NOTE: per-service reachability (kind + public origin + open link) will be
// driven by each service's /contract endpoint (kind: ui|api|internal and the
// external_origin var), resolved against the real deploy config. The earlier
// hardcoded subdomain convention was removed because it guessed origins that
// were never routed. See docs/security/external-access.md.

// Asset Identity Map editor (admin). The gateway is the authority (GET/PUT /assets,
// PVC-backed); PUT replaces the store, so we load-all, edit, save-all. The field
// model + enums mirror the upstream schema/asset.schema.json (v2); the gateway
// validates authoritatively. The gateway serves no asset-schema endpoint, so the
// HELP copy below is a hand-kept mirror of that schema's field descriptions.
const ASSET_KINDS = ["uwb-tag", "tool", "pallet", "forklift", "asset", "ue"];
const ASSET_SOURCES = ["wittra", "wifi", "fiveg", "gnss", "mock"];
// org defaults to the testbed's CAMARA tenant (camara_org, injected as VITE_CAMARA_ORG)
// so onboarded assets land in the one tenant instead of starting blank. See docs/security/iam.md.
const EMPTY_ASSET = { asset_id: "", positioning_id: "", kind: "asset", source: "mock", org: env("VITE_CAMARA_ORG", "demo"), label: "", simulated: false, metadata: {} };
const ID_RE = /^[A-Za-z0-9._:-]{1,128}$/;
const ORG_RE = /^[a-z0-9-]{1,64}$/;
// One-line guidance per field, condensed from the upstream asset.schema.json
// descriptions. The assetId -> positioning_id indirection is the part operators trip on.
const HELP = {
  asset_id: "Public CAMARA handle the consumer queries by (device.assetId). A business id like pkg-4471, not a phone number.",
  positioning_id: "The id the chosen adapter fetches this device by: the vendor-native device id (used verbatim in the adapter's API call), or the engine track id for a mock source.",
  kind: "Entity class, surfaced in the CAMARA profile so a consumer knows what it is tracking.",
  source: "Routes the asset: the engine serves it from the registered adapter whose name equals this (?source=). Pick a deployed adapter.",
  org: "Tenant. The gateway matches it against the token org claim, so a consumer sees only its own org's assets.",
  simulated: "Wired to a synthetic/demo source (mock). Shows a MOCK badge; does not change routing.",
};

// Guided editor for one asset. Self-contained form state (including free-form
// metadata rows) so the parent only handles the assembled asset on save. Built from
// the upstream asset.schema.json field descriptions (see HELP above).
function AssetModal({ initial, isNew, busy, onSave, onClose }) {
  const [form, setForm] = useState(() => ({ ...EMPTY_ASSET, ...initial }));
  const [meta, setMeta] = useState(() =>
    Object.entries(initial?.metadata || {}).map(([k, v]) => ({ k, v: String(v) })));
  const [step, setStep] = useState(0);
  // Live adapter registry: `source` ROUTES (v0.8.0 the engine serves the asset from
  // the adapter whose ADAPTER_NAME == source), so the picker offers the names of
  // registered adapters, not a free modality string. See docs/architecture/positioning-adapters.md.
  const [adapters, setAdapters] = useState([]);
  useEffect(() => {
    getNorthboundAdapters().then((a) => setAdapters(Array.isArray(a) ? a : [])).catch(() => {});
  }, []);
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));

  // A mock adapter feeds synthetic data; pre-tick simulated (still editable) so the
  // MOCK badge matches reality without a second manual step.
  const onSource = (source) => set({ source, simulated: source.startsWith("mock") ? true : form.simulated });

  // Options = live adapter names (the routing key). Keep the current value selectable
  // even if its adapter is offline, and fall back to the known modalities when nothing
  // is registered yet so the form stays usable in a cold stack.
  const liveNames = adapters.map((a) => a.name).filter(Boolean);
  const stateOf = Object.fromEntries(adapters.map((a) => [a.name, a.state]));
  const sourceOptions = Array.from(new Set([...(liveNames.length ? liveNames : ASSET_SOURCES), form.source].filter(Boolean)));
  const sourceRouted = liveNames.includes(form.source) && stateOf[form.source] === "live";
  const sourceHint = liveNames.length
    ? (liveNames.includes(form.source)
        ? `Routes to adapter "${form.source}"${stateOf[form.source] && stateOf[form.source] !== "live" ? ` (${stateOf[form.source]})` : " · live"}.`
        : `No registered adapter named "${form.source}". Deploy one (Build your own) or pick a live source.`)
    : "No adapters registered yet. Deploy one in Build your own; the values below are known modalities.";

  // Progressive steps: identity → routing → tenant/details. Each step gates the next
  // (Next disabled until valid) so the operator cannot skip the assetId→positioning_id
  // indirection or commit a malformed org; the whole thing is a guided flow, not one
  // long field dump.
  const idOk = ID_RE.test((form.asset_id || "").trim());
  const pidOk = ID_RE.test((form.positioning_id || "").trim());
  const orgOk = ORG_RE.test((form.org || "").trim());
  const STEPS = [
    { id: "identity", label: "Identity", valid: idOk && pidOk },
    { id: "routing", label: "Routing", valid: true },
    { id: "details", label: "Details", valid: orgOk },
  ];
  const last = STEPS.length - 1;
  const canNext = STEPS[step].valid;
  const allValid = STEPS.every((s) => s.valid);

  const submit = () => {
    const metadata = {};
    for (const { k, v } of meta) { const key = k.trim(); if (key) metadata[key] = v; }
    onSave({ ...form, metadata });
  };

  return (
    <Modal
      title={isNew ? "Add asset" : `Edit ${initial.asset_id}`}
      hint="device.assetId → positioning_id (vendor device id) → source = adapter that serves it · org (tenant)"
      onClose={onClose}
    >
      <div className="flex flex-col gap-4 text-xs">
        {/* Stepper: current highlighted, done steps checked and clickable to go back;
            forward is only reachable through a validated Next. */}
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
                  {done ? "✓" : i + 1}
                </span>
                {s.label}
              </button>
            );
          })}
        </div>

        {step === 0 && (
          <div className="flex flex-col gap-3">
            <p className="text-[11px] text-slate-500">Who this device is publicly, and the id its source fetches it by.</p>
            <Field label="assetId" hint={HELP.asset_id}>
              <input className={inputCls} placeholder="pkg-4471" value={form.asset_id}
                disabled={!isNew} onChange={(e) => set({ asset_id: e.target.value })} />
              {form.asset_id && !idOk && <span className="text-[10px] text-rose-400">letters / digits / . _ : - (1-128)</span>}
            </Field>
            <Field label="positioning_id" hint={HELP.positioning_id}>
              <div className="flex items-center gap-2">
                <input className={`${inputCls} flex-1`} placeholder="vendor device id (mock: engine track id)"
                  value={form.positioning_id} onChange={(e) => set({ positioning_id: e.target.value })} />
                <button type="button" className={btn.ghost} disabled={!form.asset_id}
                  onClick={() => set({ positioning_id: form.asset_id })}>= assetId</button>
              </div>
              {form.positioning_id && !pidOk && <span className="text-[10px] text-rose-400">letters / digits / . _ : - (1-128)</span>}
            </Field>
          </div>
        )}

        {step === 1 && (
          <div className="flex flex-col gap-3">
            <p className="text-[11px] text-slate-500">Which deployed adapter serves this asset, and what it is.</p>
            <Field label="source" hint={HELP.source}>
              <select className={inputCls} value={form.source} onChange={(e) => onSource(e.target.value)}>
                {sourceOptions.map((s) => (
                  <option key={s} value={s}>{s}{stateOf[s] && stateOf[s] !== "live" ? ` (${stateOf[s]})` : ""}</option>
                ))}
              </select>
              <span className={`text-[10px] ${sourceRouted ? "text-emerald-500" : "text-amber-500"}`}>{sourceHint}</span>
            </Field>
            <Field label="kind" hint={HELP.kind}>
              <select className={inputCls} value={form.kind} onChange={(e) => set({ kind: e.target.value })}>
                {ASSET_KINDS.map((k) => <option key={k} value={k}>{k}</option>)}
              </select>
            </Field>
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
            <Toggle checked={!!form.simulated} onChange={(v) => set({ simulated: v })}
              label="simulated" hint={HELP.simulated} />
            {/* Advanced: free-form metadata (schema additionalProperties), e.g. floor, bay. */}
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
                        onClick={() => setMeta((m) => m.filter((_, j) => j !== i))} aria-label="Remove field">✕</button>
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

// Validate a parsed asset array before a replace-all import: mirrors the per-asset
// rules (assetId/positioning_id/org patterns) plus duplicate detection, and normalises
// optional fields to defaults so a terse hand-written file still imports. The gateway
// validates authoritatively on PUT; this just catches obvious errors before we replace
// the whole store.
function validateImportedAssets(arr) {
  const errors = [];
  const seen = new Set();
  const clean = arr.map((a, i) => {
    const id = (a?.asset_id || "").trim();
    const pid = (a?.positioning_id || "").trim();
    const org = (a?.org || "").trim();
    const where = id || `#${i + 1}`;
    if (!ID_RE.test(id)) errors.push(`${where}: bad asset_id`);
    else if (seen.has(id)) errors.push(`${where}: duplicate asset_id`);
    if (!ID_RE.test(pid)) errors.push(`${where}: bad positioning_id`);
    if (!ORG_RE.test(org)) errors.push(`${where}: bad org`);
    if (id) seen.add(id);
    return {
      ...a, asset_id: id, positioning_id: pid, org,
      kind: a?.kind || "asset",
      source: a?.source || "mock",
      label: (a?.label || "").trim(),
      simulated: !!a?.simulated,
      metadata: (a && typeof a.metadata === "object" && a.metadata) || {},
    };
  });
  return { errors, clean };
}

// Onboarding: devices the engine sees across live adapters that are not yet mapped
// (GET /assets/discoverable). `origin` distinguishes a vendor inventory entry (stable
// registry, bulk) from an on-air observation (wifi, per-activity). Onboarding is never
// automatic — picking one opens the Add-asset wizard prefilled; the operator confirms
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
// group). Shows origin, the vendor-native device_type, and role badges when present.
function CandidateRow({ c, onOnboard }) {
  const ob = ORIGIN_BADGE[c.origin] || { cls: "bg-slate-700/60 text-slate-400", label: c.origin || "?", title: "" };
  const rb = c.role ? ROLE_BADGE[c.role] : null;
  const sub = (c.label && c.label !== c.id) ? c.label : "";
  return (
    <div className="flex items-center gap-3 py-2 text-xs">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono font-semibold text-slate-200">{c.id}</span>
          {c.source && <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[9px] text-slate-400">{c.source}</span>}
          <span className={`rounded px-1.5 py-0.5 text-[9px] ${ob.cls}`} title={ob.title}>{ob.label}</span>
          {c.source_class && <span className="rounded bg-indigo-500/15 px-1.5 py-0.5 text-[9px] text-indigo-300" title="normalized source class">{c.source_class}</span>}
          {c.device_type && <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[9px] text-slate-400" title="vendor-native device type">{c.device_type}</span>}
          {rb && <span className={`rounded px-1.5 py-0.5 text-[9px] ${rb.cls}`}>{rb.label}</span>}
        </div>
        {(sub || c.last_seen) && (
          <div className="mt-0.5 flex items-center gap-2 text-[10px] text-slate-600">
            {sub && <span>{sub}</span>}
            {c.last_seen && <span>· seen {c.last_seen}</span>}
          </div>
        )}
      </div>
      {onOnboard && <button type="button" className={btn.sky} onClick={() => onOnboard(c)}>onboard →</button>}
    </div>
  );
}

function DiscoverModal({ onOnboard, onClose }) {
  const [rows, setRows] = useState(null); // null = loading
  const [err, setErr] = useState("");
  useEffect(() => {
    getNorthboundDiscoverable()
      // The gateway returns { candidates: [{ id, source, origin, role?, source_class?,
      // device_type?, label?, last_seen? }] }. Fall back to devices / a bare array so a
      // contract tweak does not blank the list.
      .then((d) => setRows(
        Array.isArray(d?.candidates) ? d.candidates
          : Array.isArray(d?.devices) ? d.devices
          : Array.isArray(d) ? d : []
      ))
      .catch((e) => { setErr(e.message || "could not load discoverable devices"); setRows([]); });
  }, []);

  // The gateway MARKS infrastructure (role) rather than excluding it, so KELT separates it:
  // onboardable = asset or unclassified; infrastructure is shown muted and cannot be onboarded.
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
                {infra.length} infrastructure device{infra.length > 1 ? "s" : ""} · fixed, not onboardable
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

function AssetsTab({ toast }) {
  const [assets, setAssets] = useState(null); // null = loading
  const [editing, setEditing] = useState(null);
  const [isNew, setIsNew] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [importConfirm, setImportConfirm] = useState(null); // { count, next }
  const [discoverOpen, setDiscoverOpen] = useState(false);
  const fileRef = useRef(null);

  // Onboard a discovered device: open the Add-asset wizard PREFILLED from the candidate
  // (asset_id defaults to the device id but stays editable, positioning_id = the id the
  // adapter fetches by, source from the candidate). The operator confirms org/kind and
  // saves — onboarding is never silent.
  const onboardCandidate = (c) => {
    setDiscoverOpen(false);
    setEditing({
      ...EMPTY_ASSET,
      asset_id: c.id || "",
      positioning_id: c.id || "",
      source: c.source || "mock",
      simulated: (c.source || "").startsWith("mock"),
      label: c.label || "",
      // Carry the classification as provenance (no hardcoded source_class/device_type →
      // asset-kind mapping; the operator picks kind in the wizard). No-op until present.
      metadata: {
        ...(c.source_class ? { source_class: c.source_class } : {}),
        ...(c.device_type ? { device_type: c.device_type } : {}),
      },
    });
    setIsNew(true);
  };

  const load = useCallback(() => {
    setErr("");
    getNorthboundAssets()
      .then((d) => setAssets(Array.isArray(d?.assets) ? d.assets : []))
      .catch((e) => { setErr(e.message || "could not load /assets"); setAssets([]); });
  }, []);
  useEffect(() => { load(); }, [load]);

  const saveAll = async (next) => {
    setBusy(true);
    try {
      await setNorthboundAssets({ version: 2, assets: next });
      setAssets(next);
      toast.success("Assets saved — gateway store updated");
    } catch (e) { toast.error(`Save failed: ${e.message}`); throw e; }
    finally { setBusy(false); }
  };

  const upsert = async (a) => {
    const id = (a.asset_id || "").trim();
    const pid = (a.positioning_id || "").trim();
    const org = (a.org || "").trim();
    if (!ID_RE.test(id)) return toast.error("asset_id: letters/digits/._:- (1-128)");
    if (!ID_RE.test(pid)) return toast.error("positioning_id: letters/digits/._:- (1-128)");
    if (!ORG_RE.test(org)) return toast.error("org: lowercase letters/digits/- (1-64)");
    const clean = { ...a, asset_id: id, positioning_id: pid, org, label: (a.label || "").trim() };
    const next = [...(assets || []).filter((x) => x.asset_id !== id), clean];
    try { await saveAll(next); setEditing(null); } catch { /* toast shown */ }
  };

  const remove = async (id) => {
    try { await saveAll((assets || []).filter((x) => x.asset_id !== id)); } catch { /* toast shown */ }
  };

  // Export the current map as assets.json (same shape as the companion seed file, so
  // it doubles as a backup and a seed template). Client-side blob, no round-trip.
  const doExport = () => {
    const body = JSON.stringify({ version: 2, assets: assets || [] }, null, 2);
    const url = URL.createObjectURL(new Blob([body], { type: "application/json" }));
    const a = document.createElement("a");
    a.href = url; a.download = "assets.json";
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  };

  // Import a JSON file and REPLACE the whole map (PUT /assets is replace-all). Accept a
  // bare array or { assets: [...] }; validate before offering the confirm so a bad file
  // never silently wipes the store.
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
    <Panel title="Asset Identity Map" hint="CAMARA private-asset profile: assetId → positioning source. The gateway is the authority (GET/PUT /assets).">
      {assets === null ? (
        <p className="text-xs text-slate-500">Loading…</p>
      ) : (
        <div className="flex flex-col gap-3">
          {err && <div className="rounded bg-amber-500/10 px-3 py-2 text-xs text-amber-300">{err}</div>}
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <button type="button" className={btn.ghost} disabled={busy || (assets || []).length === 0}
                title="download the current map as assets.json" onClick={doExport}>⇩ export</button>
              <button type="button" className={btn.ghost} disabled={busy}
                title="replace the whole map from an assets.json file" onClick={() => fileRef.current?.click()}>⇪ import</button>
              <input ref={fileRef} type="file" accept="application/json,.json" className="hidden" onChange={onImportFile} />
            </div>
            <div className="flex items-center gap-2">
              <button type="button" className={btn.ghost} title="devices seen by live adapters, not yet onboarded" onClick={() => setDiscoverOpen(true)}>⌕ Discover devices</button>
              <button type="button" className={btn.sky} onClick={() => { setEditing({ ...EMPTY_ASSET }); setIsNew(true); }}>+ Add asset</button>
            </div>
          </div>
          {assets.length === 0 ? (
            <p className="text-xs text-slate-500">No assets yet. Add one to expose it through the CAMARA Location API by <span className="font-mono">assetId</span>.</p>
          ) : (
            <table className="w-full text-xs">
              <thead><tr className="text-left text-slate-400">
                <th className="py-1">assetId</th><th>kind</th><th>source</th><th>org</th><th>positioning_id</th><th></th>
              </tr></thead>
              <tbody>
                {assets.map((a) => (
                  <tr key={a.asset_id} className="border-t border-slate-800">
                    <td className="py-1 font-mono text-slate-200">{a.asset_id}{a.simulated && <span className="ml-1 rounded bg-amber-500/20 px-1 text-[9px] text-amber-300">MOCK</span>}</td>
                    <td>{a.kind}</td><td>{a.source}</td><td>{a.org}</td>
                    <td className="font-mono text-slate-400">{a.positioning_id}</td>
                    <td className="text-right">
                      <button type="button" className="mr-3 text-sky-400 hover:underline" onClick={() => { setEditing({ ...EMPTY_ASSET, ...a }); setIsNew(false); }}>edit</button>
                      <button type="button" className="text-rose-400 hover:underline disabled:opacity-40" disabled={busy} onClick={() => remove(a.asset_id)}>delete</button>
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
  );
}

// Update-all: roll every companion service to the current 5g-northbound release
// (latest on ghcr). The backend persists the release tag, re-runs phase 10, then
// patches the catalog adapters (wifi/vendor REST) it does not own — all in one
// streamed % + ETA. Opens with a persistence panel: the rollout reuses PVCs
// (blueprint/registry/asset map/wifi calibration) and keeps ConfigMap/Secret
// config, so nothing is lost.
function UpdateAllModal({ count, onClose, onDone, toast }) {
  const [phase, setPhase] = useState("ready"); // ready|running|done|error
  const [pct, setPct] = useState(null);
  const [line, setLine] = useState("");
  const [eta, setEta] = useState("");
  const [err, setErr] = useState("");
  const startRef = useRef(0);
  const busy = phase === "running";

  const run = async () => {
    setPhase("running"); setErr(""); setLine(""); setPct(null); startRef.current = Date.now();
    try {
      await updateAllNorthboundStream((ev) => {
        if (ev.line) setLine(ev.line);
        if (typeof ev.pct === "number") {
          setPct(ev.pct);
          const el = (Date.now() - startRef.current) / 1000;
          setEta(ev.pct > 3 && ev.pct < 100 ? `~${Math.max(1, Math.round(el * (100 - ev.pct) / ev.pct))}s left` : "");
        }
      });
      setPhase("done"); setEta("");
      toast.success("Northbound update complete");
      onDone?.();
    } catch (e) {
      setPhase("error"); setErr(String(e?.message || e));
    }
  };

  return (
    <Modal
      title="Update all northbound services"
      hint={count ? `Roll ${count} behind service${count > 1 ? "s" : ""} to its latest release` : "Roll behind companion services to their latest release"}
      onClose={busy ? () => {} : onClose}
    >
      <div className="flex flex-col gap-3 text-xs">
        {phase === "ready" && (
          <>
            <div className="rounded border border-slate-800 bg-slate-950/40 p-3">
              <p className="mb-1.5 font-medium text-slate-300">Your data is preserved</p>
              <ul className="space-y-1 text-slate-400">
                <li>Blueprint, adapter registry, asset map: on PVCs, reused by the rollout</li>
                <li>Adapter schema / config / secrets: kept (image patch, not recreate)</li>
                <li>WiFi calibration: persisted (PVC-backed)</li>
                <li>Custom adapters keep their config</li>
              </ul>
            </div>
            <p className="text-slate-500">
              Re-runs phase 10-northbound for the behind managed services, then patches the
              behind catalog adapters (wifi, vendor REST). Each image moves to its own latest
              tag on ghcr. Takes a couple of minutes.
            </p>
            <div className="flex justify-end gap-2">
              <button type="button" className={btn.ghost} onClick={onClose}>Cancel</button>
              <button type="button" className={btn.sky} onClick={run}>Start update</button>
            </div>
          </>
        )}
        {(busy || phase === "done") && (
          <>
            <div className="h-2 w-full overflow-hidden rounded bg-slate-800">
              <div className="h-full bg-sky-500 transition-all duration-300"
                style={{ width: `${pct ?? (phase === "done" ? 100 : 8)}%` }} />
            </div>
            <div className="flex justify-between text-[11px] text-slate-500">
              <span>{phase === "done" ? "complete" : (pct != null ? `${pct}%` : "working…")}</span>
              <span>{eta}</span>
            </div>
            {line && <p className="truncate font-mono text-[10px] text-slate-600">{line}</p>}
            {phase === "done" && (
              <div className="flex justify-end"><button type="button" className={btn.sky} onClick={onClose}>Close</button></div>
            )}
          </>
        )}
        {phase === "error" && (
          <>
            <div className="whitespace-pre-wrap rounded border border-rose-700/50 bg-rose-950/30 px-3 py-2 text-rose-300">{err}</div>
            <div className="flex justify-end gap-2">
              <button type="button" className={btn.ghost} onClick={onClose}>Close</button>
              <button type="button" className={btn.sky} onClick={run}>Retry</button>
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}

export default function NorthboundPage() {
  const auth = useAuth();
  const isAdmin = auth.roles.includes("dashboard-admin");

  const [services, setServices] = useState([]);
  const [adapters, setAdapters] = useState([]);
  const [contract, setContract] = useState(null);
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState("status");
  const [configuring, setConfiguring] = useState(null);
  const [deployOpen, setDeployOpen] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [showUpdateAll, setShowUpdateAll] = useState(false);
  const [confirm, setConfirm] = useState(null); // { title, body, label, runLabel, action }
  const [logTarget, setLogTarget] = useState(null); // { namespace, pod, deployment } for the log overlay
  const [bindings, setBindings] = useState([]);
  const [readiness, setReadiness] = useState({}); // { service: { needs_config, missing[] } }
  const [versions, setVersions] = useState({ services: [], behind_count: 0 }); // per-image deployed-vs-ghcr drift
  const autoBound = useRef(new Set()); // (consumer:field) already auto-bound this session
  const toast = useToast();

  // A workload this console deployed (deletable), vs an externally-registered
  // URL (only unregisterable). Matched by name + the managed-by label.
  const deployedNames = new Set(
    services
      .filter((s) => (s.labels || {})["app.kubernetes.io/managed-by"] === "dashboard-northbound")
      .map((s) => s.name)
  );

  // Deployed-vs-pinned drift from the backend (covers phase-managed + catalog).
  // Keyed by service name; drives the per-row ↑ chip and the Update all count.
  const verMap = Object.fromEntries((versions.services || []).map((v) => [v.name, v]));
  const behindCount = versions.behind_count || 0;

  // Silent loader, used by the 5s auto-poll and after every action so the
  // button does not flicker every poll.
  // Throws on failure; the 5s auto-poll swallows it (a transient 500 must not
  // spam a toast every tick), while the manual refresh surfaces it once.
  const refresh = useCallback(async () => {
    const [svc, ad] = await Promise.all([getNorthboundServices(), getNorthboundAdapters()]);
    setServices(svc.services || []);
    setAdapters(ad || []);
    // Version drift (chip + "updates available" banner) polls on the same 5s tick so
    // it always reflects live deployed-vs-ghcr, self-healing after ANY change (an
    // Update-all error path, a phase re-run, an external rollout) rather than only on
    // mount/action/onDone. Best-effort: its own catch, so a ghcr blip never blanks the
    // service list. The backend caches ghcr per-repo (300s), so the poll is cheap.
    getNorthboundVersions().then((r) => setVersions(r || { services: [], behind_count: 0 })).catch(() => {});
  }, []);

  // Adapter bindings are heavier (per-consumer config read), so load them on
  // mount and after actions, not on the 5s poll.
  const loadBindings = useCallback(() => {
    getNorthboundBindings().then((r) => setBindings(r.bindings || [])).catch(() => {});
    getNorthboundReadiness().then((r) => setReadiness(r.readiness || {})).catch(() => {});
  }, []);

  // Manual refresh shows a spinner so the click has visible feedback.
  const manualRefresh = async () => {
    setRefreshing(true);
    try { await refresh(); loadBindings(); }
    catch (e) { toast.error(`Load failed: ${e.message}`); }
    finally { setRefreshing(false); }
  };

  useEffect(() => {
    refresh().catch(() => {});
    getNorthboundContract().then(setContract).catch(() => {});
    loadBindings();
    const id = setInterval(() => refresh().catch(() => {}), 5000);
    return () => clearInterval(id);
  }, [refresh, loadBindings]);

  const run = async (label, fn) => {
    setBusy(true);
    try { await fn(); toast.success(`${label} ok`); await refresh().catch(() => {}); loadBindings(); }
    catch (e) { toast.error(`${label} failed: ${e.message}`); }
    finally { setBusy(false); }
  };

  // Bind a consumer's adapter field to a chosen upstream (reuses PUT /config).
  const bindAdapter = (b, url) =>
    run(`bind ${b.consumer} ${b.field}`, () => applyNorthboundServiceConfig(b.consumer, { [b.field]: url }));

  // Single-adapter auto-bind (semi-automatic, the operator's chosen behavior):
  // when exactly one adapter of a kind is deployed and the consumer is not wired
  // to it, bind it once and notify. >1 is left to the switcher (a choice).
  useEffect(() => {
    if (!isAdmin) return;
    // Batch all auto-bindable fields of a consumer into ONE apply, so the two
    // fields don't race to create the same ConfigMap (409).
    const byConsumer = {};
    for (const b of bindings) {
      const key = `${b.consumer}:${b.field}`;
      if (b.auto && b.candidates.length === 1 && !autoBound.current.has(key)) {
        autoBound.current.add(key);
        (byConsumer[b.consumer] ||= { values: {}, names: [] });
        byConsumer[b.consumer].values[b.field] = b.candidates[0].url;
        byConsumer[b.consumer].names.push(b.candidates[0].name);
      }
    }
    for (const [consumer, { values, names }] of Object.entries(byConsumer)) {
      toast.success(`Detected ${names.join(", ")} — binding ${consumer}`);
      run(`bind ${consumer}`, () => applyNorthboundServiceConfig(consumer, values));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bindings, isAdmin]);

  return (
    <div className="svc-fade flex flex-col gap-5 pb-8">
      <header className="flex flex-col gap-2">
        <Link to="/services" className="inline-flex w-fit items-center gap-1 text-xs text-slate-400 hover:text-slate-200">
          <IconArrowLeft size={14} /> Services
        </Link>
        <div className="flex items-center justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold text-slate-100">Northbound</h2>
            <p className="text-xs text-slate-500">
              Positioning engine adapters and the CAMARA Location stack.
              {isAdmin ? "" : " Read-only (dashboard-admin required for changes)."}
            </p>
          </div>
          {/* Update all lives in the in-content banner (only when behind); the header
              keeps just refresh to avoid a redundant second CTA. */}
          <button type="button" onClick={manualRefresh} disabled={refreshing} className={`inline-flex items-center gap-1 ${btn.ghost} disabled:opacity-60`}>
            <IconRefresh size={14} className={refreshing ? "animate-spin" : ""} /> {refreshing ? "refreshing…" : "refresh"}
          </button>
        </div>
      </header>

      <Tabs tabs={isAdmin ? TABS : TABS.filter((t) => t.id !== "assets")} active={tab} onChange={setTab} />

      {/* key=tab remounts the pane on switch so it fades/rises in (see .tab-pane). */}
      <div key={tab} className="tab-pane">
      {tab === "status" && (
        <div className="flex flex-col gap-4">
        {isAdmin && behindCount > 0 && (
          <div className="flex items-center justify-between gap-3 rounded-lg border border-sky-800/50 bg-sky-950/30 px-4 py-2.5">
            <span className="text-xs text-sky-200">
              <span className="font-semibold">{behindCount} update{behindCount > 1 ? "s" : ""} available</span>
              {" "}— newer 5g-northbound image{behindCount > 1 ? "s are" : " is"} on ghcr.
            </span>
            <button type="button" onClick={() => setShowUpdateAll(true)} className={btn.sky}>↑ Update all</button>
          </div>
        )}
        <Panel title="Services" hint="Positioning and CAMARA services. “managed” roll via Update all; catalog adapters (wifi, vendor REST) upgrade individually.">
          {services.length === 0 ? (
            <p className="text-xs text-slate-500">No northbound services found. Enable the feature with <span className="font-mono">testbed northbound on</span>.</p>
          ) : (
            <div className="flex flex-col divide-y divide-slate-800/60">
              {services.map((s) => {
                const phase = s.pods && s.pods[0] ? s.pods[0].phase : "Unknown";
                const kind = KIND_BADGE[s.kind] || KIND_BADGE.internal;
                const ver = verMap[s.name];                    // backend drift vs ghcr release
                const latestTag = ver?.latest;                 // shown on the ↑ chip when behind
                const behind = !!ver?.behind;
                const tag = imgTag(s.image);
                // Catalog adapters (not phase-managed) are patchable individually; managed
                // ones roll only via Update all.
                const upImage = (behind && ver && !ver.managed && latestTag)
                  ? `ghcr.io/jacobbista/5g-northbound/${imgBasename(s.image)}:${latestTag}` : null;
                const ep = publicUrl(s);
                const starting = (s.ready_replicas || 0) < (s.replicas || 0);
                return (
                  <div key={`${s.namespace}/${s.name}`} className="flex items-center gap-3 py-2.5">
                    <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${phaseDot(phase)}`} title={phase} />
                    {/* Primary: name + classifying/state chips on line 1, the de-emphasized
                        image + public link on line 2. Keeps rows aligned regardless of which
                        optional chips a service has. */}
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-sm font-semibold text-slate-100">{s.name}</span>
                        {s.kind && <span className={`rounded px-1.5 py-0.5 font-mono text-[10px] ${kind.cls}`} title="how this surface is served">{kind.label}</span>}
                        {tag && <span className="font-mono text-[10px] text-slate-400" title={s.image || ""}>{tag}</span>}
                        {/* Version-drift chip. On a catalog adapter (not phase-managed) it IS the
                            upgrade affordance: click to patch just this one to its latest tag. On a
                            managed service it is a read-only indicator (those roll via Update all). */}
                        {behind && latestTag && (
                          upImage && isAdmin ? (
                            <button
                              type="button"
                              disabled={busy}
                              title={`upgrade ${s.name} to ${latestTag}`}
                              onClick={() => setConfirm({
                                title: `Upgrade ${s.name} to ${latestTag}?`,
                                body: `Patches the image to ${upImage} (its config is preserved). The pod restarts and the adapter re-registers with the engine.`,
                                label: "Upgrade",
                                runLabel: `upgrade ${s.name}`,
                                action: () => upgradeNorthboundAdapter(s.name, upImage),
                              })}
                              className="rounded bg-amber-950/40 px-1.5 py-0.5 text-[10px] text-amber-300 transition-colors hover:bg-amber-900/60 hover:text-amber-200 disabled:opacity-40"
                            >
                              ↑ {latestTag}
                            </button>
                          ) : (
                            <span
                              className="rounded bg-amber-950/40 px-1.5 py-0.5 text-[10px] text-amber-300"
                              title={ver?.managed
                                ? `deployed ${tag}; latest ${latestTag} — rolls via Update all`
                                : `deployed ${tag}; latest release is ${latestTag}`}
                            >
                              ↑ {latestTag}
                            </span>
                          )
                        )}
                        {readiness[s.name]?.needs_config && (
                          <span className="rounded bg-rose-950/40 px-1.5 py-0.5 text-[10px] text-rose-300" title={`needs config: ${(readiness[s.name].missing || []).join(", ")}`}>⚠ needs config</span>
                        )}
                        {(readiness[s.name]?.ephemeral || []).length > 0 && (
                          <span className="rounded bg-amber-950/40 px-1.5 py-0.5 text-[10px] text-amber-300" title={`loaded but not persisted (lost on restart): ${readiness[s.name].ephemeral.join(", ")} — persist via Configure`}>⟳ ephemeral</span>
                        )}
                        {s.stateful && s.persistent === false && (
                          <span className="rounded bg-amber-950/40 px-1.5 py-0.5 text-[10px] text-amber-300" title="calibration is written in this service's own UI but is NOT PVC-backed yet — it would be lost on restart. Click “enable persistence”.">⟳ not persisted</span>
                        )}
                      </div>
                      <div className="mt-0.5 flex items-center gap-2 text-[11px] text-slate-500">
                        <span className="uppercase tracking-wide">{s.namespace}</span>
                        {s.managed && <span title="managed by Ansible — rolls via Update all / phase 10">· managed</span>}
                        {ep ? (
                          <a href={ep.url} target="_blank" rel="noreferrer" title={`open ${ep.url}`} className="inline-flex shrink-0 items-center gap-1 font-mono text-sky-400 hover:text-sky-300">{ep.label} <span aria-hidden="true">↗</span></a>
                        ) : s.node_port ? (
                          <span className="shrink-0 font-mono text-[10px]" title="LAN NodePort">:{s.node_port}</span>
                        ) : null}
                      </div>
                    </div>
                    {/* Right cluster: status + actions, aligned across every row. */}
                    <div className="flex shrink-0 items-center gap-2">
                      <span className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 font-mono text-[10px] ${starting ? "bg-amber-950/40 text-amber-300" : "bg-slate-800 text-slate-300"}`} title={starting ? "pods starting" : "ready"}>
                        {starting && <IconRefresh size={10} className="animate-spin" />}
                        {s.ready_replicas}/{s.replicas}
                      </span>
                      {s.pods && s.pods[0] && (
                        <button
                          type="button"
                          title={`stream logs (${s.pods[0].name})`}
                          onClick={() => setLogTarget({ namespace: s.namespace, pod: s.pods[0].name, deployment: s.name })}
                          className="rounded bg-indigo-600/20 px-2 py-1 text-[11px] font-medium text-indigo-300 transition-colors hover:bg-indigo-600/30"
                        >
                          logs
                        </button>
                      )}
                      {isAdmin && s.stateful && s.persistent === false && (
                        <button
                          type="button"
                          disabled={busy}
                          className={btn.amber}
                          onClick={() => setConfirm({
                            title: `Enable persistence for ${s.name}?`,
                            body: "Attaches a PVC mounted at the calibration file, so a calibration set in this service's own UI survives restart and upgrade. The pod restarts once.",
                            label: "Enable",
                            runLabel: `enable persistence ${s.name}`,
                            action: () => enableNorthboundPersistence(s.name),
                          })}
                        >
                          enable persistence
                        </button>
                      )}
                      {isAdmin && s.configurable && (
                        <button type="button" onClick={() => setConfiguring(s.name)} className={btn.ghost}>configure</button>
                      )}
                      {isAdmin && deployedNames.has(s.name) && (
                        <button
                          type="button"
                          disabled={busy}
                          title="delete workload"
                          onClick={() => setConfirm({
                            title: `Delete ${s.name}?`,
                            body: "Removes the Deployment and Service and unregisters it from the engine.",
                            label: "Delete",
                            runLabel: `delete ${s.name}`,
                            action: () => deleteNorthboundWorkload(s.name),
                          })}
                          className="text-rose-400 hover:text-rose-300 disabled:opacity-40"
                        >
                          <IconTrash size={13} />
                        </button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </Panel>
        {bindings.length > 0 && (
          <Panel title="Vendor bindings" hint="Which deployed adapter each consumer points at. One adapter of a kind binds automatically; more than one shows a switcher.">
            <div className="flex flex-col divide-y divide-slate-800/60">
              {bindings.map((b) => (
                <div key={`${b.consumer}:${b.field}`} className="flex items-center gap-3 py-2 text-xs">
                  <span className="min-w-[150px] font-semibold text-slate-100">{b.consumer}</span>
                  <span className="font-mono text-[10px] text-slate-500">{b.field}</span>
                  <span className="text-slate-600" aria-hidden="true">→</span>
                  <span className="flex-1">
                    {b.candidates.length === 0 ? (
                      <span className="text-slate-500">no {b.kind} deployed</span>
                    ) : b.bound_to ? (
                      <span className="inline-flex items-center gap-1 font-mono text-emerald-400">{b.bound_to} <span aria-hidden="true">✓</span></span>
                    ) : b.candidates.length === 1 ? (
                      <span className="inline-flex items-center gap-1 text-amber-300"><IconRefresh size={11} className="animate-spin" /> binding {b.candidates[0].name}…</span>
                    ) : (
                      <span className="text-amber-300">{b.candidates.length} {b.kind}s — pick one</span>
                    )}
                  </span>
                  {isAdmin && b.candidates.length > 1 && (
                    <select
                      className={inputCls}
                      value={(b.candidates.find((c) => c.name === b.bound_to) || {}).url || ""}
                      onChange={(e) => { if (e.target.value) bindAdapter(b, e.target.value); }}
                      disabled={busy}
                    >
                      <option value="">switch…</option>
                      {b.candidates.map((c) => <option key={c.url} value={c.url}>{c.name}</option>)}
                    </select>
                  )}
                </div>
              ))}
            </div>
          </Panel>
        )}
        {configuring && (
          <ConfigureService
            service={configuring}
            services={services}
            toast={toast}
            onClose={() => setConfiguring(null)}
            onApplied={() => { refresh(); loadBindings(); }}
          />
        )}
        </div>
      )}

      {tab === "adapters" && (
        <div className="flex flex-col gap-4">
          <Panel
            title="Adapter registry"
            hint="Live registry from the engine. Adapters self-register and heartbeat; the engine evicts dead ones. Deploy an adapter and it announces itself, no manual step."
            right={isAdmin && <button type="button" onClick={() => setDeployOpen(true)} className={btn.sky}>Deploy adapter</button>}
          >
            {adapters.length === 0 && <p className="text-xs text-slate-500">No adapters registered; the engine uses its embedded mock fallback until one self-registers.</p>}
            <div className="flex flex-col gap-1.5">
              {adapters.map((a) => {
                const st = ADAPTER_STATE[a.state] || { cls: "bg-slate-700/60 text-slate-400", label: a.state || "?" };
                const dep = deployedNames.has(a.name);
                // Delete a dashboard-deployed workload (it then deregisters); else
                // offer force-remove only for a dead entry (a live self-registered
                // adapter would just re-announce, so removing it is meaningless).
                const canForce = !dep && a.state !== "live";
                return (
                  <div key={a.name} className="flex items-center gap-3 rounded border border-slate-800 bg-slate-950 px-3 py-2 text-xs">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-mono font-semibold text-slate-200">{a.name}</span>
                        {a.kind && <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[9px] text-slate-400">{a.kind}</span>}
                        <span className={`rounded px-1.5 py-0.5 text-[9px] ${st.cls}`}>{st.label}</span>
                        {a.in_cooldown && <span className="text-[9px] text-rose-400">cooldown {Math.round(a.cooldown_seconds_remaining || 0)}s</span>}
                      </div>
                      <div className="mt-0.5 flex flex-wrap items-center gap-2 text-[10px] text-slate-600">
                        {a.registered_via && <span>via {a.registered_via}</span>}
                        {typeof a.last_seen_s_ago === "number" && <span>· seen {Math.round(a.last_seen_s_ago)}s ago</span>}
                        <span className="truncate font-mono">{a.base_url}</span>
                      </div>
                    </div>
                    {isAdmin && (dep || canForce) && (
                      <button
                        type="button"
                        disabled={busy}
                        title={dep ? "delete workload" : "force-remove stale entry"}
                        onClick={() => setConfirm({
                          title: dep ? `Delete ${a.name}?` : `Force-remove ${a.name}?`,
                          body: dep
                            ? "Removes the Deployment and Service; the adapter deregisters from the engine on shutdown."
                            : "Clears this stale entry from the engine registry. A live adapter would re-announce on its next heartbeat.",
                          label: dep ? "Delete" : "Force-remove",
                          runLabel: dep ? `delete ${a.name}` : `force-remove ${a.name}`,
                          action: () => (dep ? deleteNorthboundWorkload(a.name) : unregisterNorthboundAdapter(a.name)),
                        })}
                        className="shrink-0 text-rose-400 hover:text-rose-300 disabled:opacity-40"
                      >
                        <IconTrash size={13} />
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
            {contract?.docs && (
              <p className="mt-3 border-t border-slate-800 pt-3 text-[11px] text-slate-500">
                Build your own positioning source:{" "}
                <a className="text-sky-400 hover:underline" href={contract.docs.adapter_contract} target="_blank" rel="noreferrer">adapter contract</a>
                {" · "}
                <a className="text-sky-400 hover:underline" href={contract.docs.rest_adapter} target="_blank" rel="noreferrer">vendor REST</a>
                {" · "}
                <a className="text-sky-400 hover:underline" href={contract.docs.env_contract} target="_blank" rel="noreferrer">env contract</a>
              </p>
            )}
          </Panel>
          {/* Expert controls, rarely touched: folded away so the registry stays the focus. */}
          {isAdmin && (
            <Collapsible title="Advanced tuning" hint="Fusion strategy and manual image rollout. Not needed for normal operation.">
              <div className="flex flex-col gap-4">
                <div>
                  <div className="mb-1.5 text-xs font-medium text-slate-300">Fusion</div>
                  <p className="mb-2 text-[11px] text-slate-500">Classical estimators (no ML). Applies to positioning-engine and restarts it.</p>
                  <FusionForm busy={busy} onSubmit={(body) => run("fusion update", () => setNorthboundFusion(body))} />
                </div>
                <div className="border-t border-slate-800 pt-4">
                  <div className="mb-1.5 text-xs font-medium text-slate-300">Managed image rollout</div>
                  <p className="mb-2 text-[11px] text-slate-500">Retargets a running deployment. The durable image lives in all.yml; re-run the phase to reconcile.</p>
                  <ManagedForm busy={busy} onSubmit={(dep, image) => run(`rollout ${dep}`, () => rolloutNorthboundManaged(dep, image))} />
                </div>
              </div>
            </Collapsible>
          )}
          {deployOpen && (
            <Modal
              title="Deploy adapter"
              hint="Pick a catalog adapter (wifi, vendor REST) or any image. Creates a Deployment + Service in the positioning namespace; the adapter self-registers with the engine (no manual step)."
              wide
              onClose={() => setDeployOpen(false)}
            >
              <DeployForm busy={busy} onSubmit={async (body) => { await run(`deploy ${body.name}`, () => deployNorthboundImage(body)); setDeployOpen(false); }} />
            </Modal>
          )}
        </div>
      )}

      {tab === "assets" && isAdmin && <AssetsTab toast={toast} />}
      </div>

      {showUpdateAll && (
        <UpdateAllModal
          count={behindCount}
          toast={toast}
          onClose={() => setShowUpdateAll(false)}
          onDone={() => { refresh().catch(() => {}); loadBindings(); }}
        />
      )}

      {logTarget && (
        <LogViewer
          namespace={logTarget.namespace}
          pod={logTarget.pod}
          deployment={logTarget.deployment}
          onClose={() => setLogTarget(null)}
        />
      )}

      {confirm && (
        <Modal title={confirm.title} hint={confirm.body} onClose={() => setConfirm(null)}>
          <div className="flex justify-end gap-2">
            <button type="button" className={btn.ghost} onClick={() => setConfirm(null)}>Cancel</button>
            <button
              type="button"
              disabled={busy}
              className="rounded bg-rose-600/20 px-3 py-1.5 text-xs font-medium text-rose-300 transition-colors hover:bg-rose-600/30 disabled:opacity-40"
              onClick={async () => { const c = confirm; setConfirm(null); await run(c.runLabel, c.action); }}
            >
              {confirm.label}
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}

function DeployForm({ busy, onSubmit }) {
  const [name, setName] = useState("");
  const [image, setImage] = useState("");
  const [port, setPort] = useState(8080);
  const [pullSecret, setPullSecret] = useState("");
  const [adapterKind, setAdapterKind] = useState(""); // ADAPTER_KIND (modality); empty = image default
  const [env, setEnv] = useState([]);
  const [kind, setKind] = useState(null); // catalog deploy kind: "singleton" | "template" | null

  // Singleton prefills its fixed name; a template prefills only the image and
  // leaves the name blank so the operator names the instance per vendor.
  const pickCatalog = (c) => {
    setImage(c.image);
    setKind(c.kind);
    setAdapterKind(c.adapterKind || "");
    setName(c.kind === "singleton" ? c.name : "");
  };
  const singletons = CATALOG.filter((c) => c.kind === "singleton");
  const templates = CATALOG.filter((c) => c.kind === "template");

  const addEnv = () => setEnv((e) => [...e, { name: "", value: "", sensitive: false }]);
  const setEnvAt = (i, k, v) => setEnv((e) => e.map((row, j) => (j === i ? { ...row, [k]: v } : row)));
  const rmEnv = (i) => setEnv((e) => e.filter((_, j) => j !== i));

  const submit = (e) => {
    e.preventDefault();
    if (!name || !image) return;
    onSubmit({
      name: name.trim(),
      image: image.trim(),
      port: Number(port) || 8080,
      image_pull_secret: pullSecret.trim() || null,
      kind: adapterKind.trim(),
      env: env.filter((r) => r.name).map((r) => ({ name: r.name.trim(), value: r.value, sensitive: !!r.sensitive })),
    });
  };

  return (
    <form className="flex flex-col gap-2 text-xs" onSubmit={submit}>
      <div className="flex flex-col gap-2 rounded border border-slate-800 bg-slate-950/40 p-2">
        <div className="flex flex-wrap items-center gap-1">
          <span className="w-32 text-[10px] uppercase tracking-wide text-slate-500">Adapter (one)</span>
          {singletons.map((c) => (
            <button key={c.name} type="button" title={c.blurb} onClick={() => pickCatalog(c)} className={btn.ghost}>{c.name}</button>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-1">
          <span className="w-32 text-[10px] uppercase tracking-wide text-slate-500">Vendor template</span>
          {templates.map((c) => (
            <button key={c.name} type="button" title={c.blurb} onClick={() => pickCatalog(c)} className={btn.ghost}>{c.name} (per vendor)</button>
          ))}
        </div>
        {kind === "template" && (
          <p className="text-[11px] text-amber-300/80">
            Name this instance after the vendor (e.g. <span className="font-mono">wittra</span>) and point it at the vendor API in the env below. Deploy one per vendor.
          </p>
        )}
        {kind === "singleton" && (
          <p className="text-[11px] text-slate-500">Self-contained source; deploy at most one.</p>
        )}
      </div>
      <div className="flex flex-wrap gap-2">
        <input className={inputCls} placeholder={kind === "template" ? "vendor name (e.g. wittra)" : "name"} value={name} onChange={(e) => setName(e.target.value)} />
        <input className={`${inputCls} min-w-[24rem] flex-1`} placeholder="image:tag" value={image} onChange={(e) => setImage(e.target.value)} />
        <input className={`${inputCls} w-20`} type="number" placeholder="port" value={port} onChange={(e) => setPort(e.target.value)} />
        <input className={`${inputCls} w-28`} placeholder="kind (e.g. uwb)" title="ADAPTER_KIND shown in the registry/demo; leave blank to keep the image default" value={adapterKind} onChange={(e) => setAdapterKind(e.target.value)} />
        <input className={inputCls} placeholder="imagePullSecret (optional)" value={pullSecret} onChange={(e) => setPullSecret(e.target.value)} />
      </div>
      <div className="flex flex-col gap-1">
        {env.map((row, i) => (
          <div key={i} className="flex flex-wrap items-center gap-2">
            <input className={inputCls} placeholder="ENV_NAME" value={row.name} onChange={(e) => setEnvAt(i, "name", e.target.value)} />
            <input className={`${inputCls} min-w-[16rem] flex-1`} placeholder="value" value={row.value} onChange={(e) => setEnvAt(i, "value", e.target.value)} />
            <label className="flex items-center gap-1 text-[10px] text-slate-400">
              <input type="checkbox" checked={row.sensitive} onChange={(e) => setEnvAt(i, "sensitive", e.target.checked)} /> secret
            </label>
            <button type="button" onClick={() => rmEnv(i)} className={btn.ghost}>x</button>
          </div>
        ))}
        <button type="button" onClick={addEnv} className={`${btn.ghost} self-start`}>+ env var</button>
      </div>
      <p className="text-[10px] text-slate-500">The adapter self-registers with the engine on boot and heartbeats; it appears in the registry above within a few seconds.</p>
      <button type="submit" disabled={busy} className={`${btn.sky} self-start`}>deploy</button>
    </form>
  );
}

function FusionForm({ busy, onSubmit }) {
  const [strategy, setStrategy] = useState("");
  const [compare, setCompare] = useState("");
  const [deviceMap, setDeviceMap] = useState("");
  return (
    <form
      className="flex flex-wrap items-end gap-2 text-xs"
      onSubmit={(e) => {
        e.preventDefault();
        const body = {};
        if (strategy) body.strategy = strategy.trim();
        if (compare !== "") body.compare = compare.trim();
        if (deviceMap !== "") body.device_map = deviceMap.trim();
        if (Object.keys(body).length) onSubmit(body);
      }}
    >
      <label className="flex flex-col gap-1">
        <span className="text-[10px] text-slate-500">FUSION_STRATEGY</span>
        <input className={inputCls} placeholder="weighted_avg" value={strategy} onChange={(e) => setStrategy(e.target.value)} />
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-[10px] text-slate-500">FUSION_COMPARE (csv)</span>
        <input className={inputCls} value={compare} onChange={(e) => setCompare(e.target.value)} />
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-[10px] text-slate-500">DEVICE_MAP (id=adapter,csv)</span>
        <input className={inputCls} value={deviceMap} onChange={(e) => setDeviceMap(e.target.value)} />
      </label>
      <button type="submit" disabled={busy} className={btn.indigo}>apply</button>
    </form>
  );
}

function ManagedForm({ busy, onSubmit }) {
  const [dep, setDep] = useState(MANAGED[0]);
  const [image, setImage] = useState("");
  return (
    <form className="flex flex-wrap items-center gap-2 text-xs" onSubmit={(e) => { e.preventDefault(); if (image) onSubmit(dep, image.trim()); }}>
      <select className={inputCls} value={dep} onChange={(e) => setDep(e.target.value)}>
        {MANAGED.map((d) => <option key={d} value={d}>{d}</option>)}
      </select>
      <input className={`${inputCls} min-w-[24rem] flex-1`} placeholder="image:tag" value={image} onChange={(e) => setImage(e.target.value)} />
      <button type="submit" disabled={busy} className={btn.amber}>roll out</button>
    </form>
  );
}

// Generic read-only JSON renderer (no deps): objects as key: value, arrays as a
// numbered list, nesting indented. Lets the operator read a document's entries
// (e.g. the device registry) without parsing raw text.
function JsonView({ value, depth = 0 }) {
  if (value === null) return <span className="text-slate-500">null</span>;
  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="text-slate-500">[]</span>;
    return (
      <div className={depth ? "border-l border-slate-800 pl-3" : ""}>
        {value.map((v, i) => (
          <div key={i} className="flex gap-2 py-0.5">
            <span className="select-none text-slate-600">{i}</span>
            <div><JsonView value={v} depth={depth + 1} /></div>
          </div>
        ))}
      </div>
    );
  }
  if (typeof value === "object") {
    return (
      <div className={depth ? "border-l border-slate-800 pl-3" : ""}>
        {Object.entries(value).map(([k, v]) => (
          <div key={k} className="flex flex-wrap gap-2 py-0.5">
            <span className="text-sky-300">{k}:</span>
            {v !== null && typeof v === "object"
              ? <div className="w-full"><JsonView value={v} depth={depth + 1} /></div>
              : <span className="text-slate-200">{typeof v === "string" ? v : String(v)}</span>}
          </div>
        ))}
      </div>
    );
  }
  return <span className="text-emerald-300">{String(value)}</span>;
}

// Guided builder for a discover.classify block. Fetches raw vendor records
// (admin-only GET /discover?raw=1 through the backend proxy) so the operator points
// the ROLE rule at the vendor's OWN field names instead of hand-writing the predicate
// JSON, and shows its live effect on the real sample. Authors role only: source_class
// (the radio) is deliberately NOT set here, because the vendor device list does not
// report the per-device radio, so guessing it from a device TYPE would be wrong (see
// the note in the body). Apply merges discover.mapping.device_type + discover.classify
// back into the schema and preserves any existing source_class config; the existing
// Save & restart persists it (ConfigMap + rollout). Structural, not vendor-specific:
// it only assumes a `discover` block exists.
function ClassifyBuilder({ service, schema, onApply }) {
  const [raw, setRaw] = useState(null);
  const [vendor, setVendor] = useState("");
  const [err, setErr] = useState(null);
  const [loading, setLoading] = useState(true);

  const cls = schema?.discover?.classify || {};
  const [typeField, setTypeField] = useState(schema?.discover?.mapping?.device_type?.path || "deviceType");
  const [assetValue, setAssetValue] = useState(cls?.asset_when?.equals ?? "");
  const hasSourceClass = !!cls.source_class_default || (Array.isArray(cls.source_class_rules) && cls.source_class_rules.length > 0);

  useEffect(() => {
    let alive = true;
    setLoading(true); setErr(null);
    getNorthboundDiscoverRaw(service)
      .then((d) => { if (!alive) return; setRaw(Array.isArray(d?.raw) ? d.raw : []); setVendor(d?.vendor || ""); })
      .catch((e) => { if (alive) setErr(e.message); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [service]);

  // Candidate fields: top-level keys that are scalar in at least one record.
  const candidateFields = (() => {
    const set = new Set();
    for (const r of raw || []) {
      if (r && typeof r === "object") {
        for (const [k, v] of Object.entries(r)) if (v === null || typeof v !== "object") set.add(k);
      }
    }
    return [...set].sort();
  })();
  // Distinct values the chosen field takes across the sample (asset-value picker).
  const distinctValues = (() => {
    const set = new Set();
    for (const r of raw || []) {
      const v = r?.[typeField];
      if (v !== undefined && v !== null && typeof v !== "object") set.add(String(v));
    }
    return [...set].sort();
  })();
  // Live effect of the rule on the real sample.
  const counts = (() => {
    let asset = 0, infra = 0;
    for (const r of raw || []) {
      if (assetValue !== "" && String(r?.[typeField]) === String(assetValue)) asset++;
      else infra++;
    }
    return { asset, infra };
  })();

  const apply = () => {
    const next = JSON.parse(JSON.stringify(schema));
    next.discover = next.discover || {};
    next.discover.mapping = next.discover.mapping || {};
    next.discover.mapping.device_type = { path: typeField };
    // Author role only. Preserve any operator-authored source_class config, never add one.
    const keepSC = {};
    if (cls.source_class_default) keepSC.source_class_default = cls.source_class_default;
    if (Array.isArray(cls.source_class_rules) && cls.source_class_rules.length) keepSC.source_class_rules = cls.source_class_rules;
    next.discover.classify = { asset_when: { path: typeField, equals: assetValue }, ...keepSC };
    onApply(next);
  };

  if (loading) return <div className="py-6 text-center text-[11px] text-slate-500">Loading vendor sample…</div>;
  if (err) return (
    <div className="rounded border border-rose-800/50 bg-rose-950/30 p-3 text-[11px] text-rose-300">
      Could not load raw devices from {service}: {err}
      <div className="mt-1 text-slate-500">Edit the classify block by hand in the JSON instead.</div>
    </div>
  );
  if (!raw || raw.length === 0) return (
    <div className="rounded border border-slate-800 bg-slate-950 p-3 text-[11px] text-slate-400">
      The adapter returned no devices — nothing to sample. Check the vendor connection, or edit the JSON by hand.
    </div>
  );

  const sample = raw[0];
  const canApply = !!typeField && assetValue !== "";
  return (
    <div className="flex flex-col gap-3 text-xs">
      <p className="text-[11px] text-slate-500">
        {vendor ? <><span className="text-slate-300">{vendor}</span> · </> : null}
        {raw.length} device{raw.length === 1 ? "" : "s"} sampled from the live vendor API. Nothing is saved until you Apply, then Save &amp; restart.
      </p>
      <Field label="Device-type field" hint="The vendor field that names the kind of device.">
        <select className={inputCls} value={typeField} onChange={(e) => setTypeField(e.target.value)}>
          {candidateFields.map((f) => (
            <option key={f} value={f}>{f}{sample?.[f] !== undefined && typeof sample[f] !== "object" ? `  (e.g. ${String(sample[f])})` : ""}</option>
          ))}
        </select>
      </Field>
      <Field label="Mark as ASSET when" hint="Every other device is treated as fixed infrastructure (not onboarded).">
        <div className="flex items-center gap-2">
          <span className="rounded bg-slate-800 px-2 py-1 font-mono text-[11px] text-slate-300">{typeField}</span>
          <span className="text-slate-500">equals</span>
          <select className={inputCls} value={assetValue} onChange={(e) => setAssetValue(e.target.value)}>
            <option value="">— pick a value —</option>
            {distinctValues.map((v) => <option key={v} value={v}>{v}</option>)}
          </select>
        </div>
      </Field>
      {assetValue !== "" && (
        <div className="rounded border border-slate-800 bg-slate-950 px-3 py-2 text-[11px]">
          On this sample: <span className="text-emerald-300">{counts.asset} asset{counts.asset === 1 ? "" : "s"}</span>
          {" · "}<span className="text-slate-300">{counts.infra} infrastructure</span>
          {counts.asset === 0 && <span className="ml-2 text-amber-400">no device matches — check the value</span>}
        </div>
      )}
      <div className="rounded border border-slate-800 bg-slate-950/60 px-3 py-2 text-[11px] text-slate-400">
        <span className="text-slate-300">Source class (radio)</span> is not set here. The vendor list does not report the per-device radio, so KELT does not guess it from the device type. Add <span className="font-mono text-slate-300">source_class_rules</span> by hand in the JSON only from a real signal (a positioning join, or site knowledge).
        {hasSourceClass && <span className="text-emerald-400"> Existing source_class config is preserved.</span>}
      </div>
      <details className="rounded border border-slate-800 bg-slate-950">
        <summary className="cursor-pointer px-3 py-2 text-[11px] text-slate-400 hover:text-slate-200">Show a raw vendor record</summary>
        <div className="max-h-56 overflow-auto border-t border-slate-800 px-3 py-2 font-mono text-[11px]">
          <JsonView value={sample} />
        </div>
      </details>
      <div className="flex items-center justify-between gap-2 border-t border-slate-800 pt-3">
        <p className="text-[10px] text-slate-500">Apply merges <span className="font-mono">device_type</span> + <span className="font-mono">classify</span> into the schema below.</p>
        <button type="button" onClick={apply} disabled={!canApply} className={btn.sky}>Apply to schema</button>
      </div>
    </div>
  );
}

// Focused viewer/editor for a file-backed document (a *_FILE the dashboard owns).
// Default Preview parses the JSON and renders its entries (no raw text); Edit is
// the textarea. Replace-from-file (with confirm), validate-on-save, then store it
// in the service's files ConfigMap and roll the pod. Rendered above the config
// modal (z-60 + capture-phase Escape so Escape closes only this one).
function FileDocModal({ service, entry, path, initial, onClose, onSaved }) {
  const confirm = useConfirm();
  const isJson = path.endsWith(".json");
  const [draft, setDraft] = useState(initial ?? "");
  const [view, setView] = useState(isJson ? "preview" : "edit");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const dirty = (draft ?? "") !== (initial ?? "");

  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") { e.stopImmediatePropagation(); onClose(); } };
    window.addEventListener("keydown", onKey, true); // capture: beats the parent Modal's Escape
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose]);

  const parsed = (() => {
    if (!isJson || !draft.trim()) return { ok: false, value: null, error: null };
    try { return { ok: true, value: JSON.parse(draft), error: null }; }
    catch (e) { return { ok: false, value: null, error: e.message }; }
  })();
  const jsonError = isJson && draft.trim() && !parsed.ok ? parsed.error : null;
  // Offer the guided Classify builder only for a schema that declares a discover
  // block (structural gate, not vendor-specific).
  const hasDiscover = isJson && parsed.ok && parsed.value && typeof parsed.value === "object" && !!parsed.value.discover;

  const pickFile = async (f) => {
    if (!f) return;
    if ((dirty || draft.trim()) && !(await confirm({ title: "Replace document?", body: `Load “${f.name}” over the current content.`, confirmLabel: "Replace" }))) return;
    f.text().then((t) => { setDraft(t); setErr(null); });
  };

  // Pretty-print before editing so the textarea is readable; harmless if invalid.
  const editView = () => {
    if (isJson && parsed.ok) setDraft(JSON.stringify(parsed.value, null, 2));
    setView("edit");
  };

  const save = async () => {
    if (jsonError) { setErr(`Invalid JSON: ${jsonError}`); return; }
    setBusy(true); setErr(null);
    try {
      await applyNorthboundServiceFile(service, path, draft ?? "");
      onSaved?.(draft ?? ""); // parent toasts, refreshes (rollout shows), and closes
    } catch (e) { setErr(e.message); setBusy(false); }
  };

  const bytes = new Blob([draft ?? ""]).size;
  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="flex max-h-[90vh] w-full max-w-5xl flex-col rounded-xl border border-slate-700 bg-slate-900 shadow-2xl">
        <div className="flex items-start justify-between gap-3 border-b border-slate-800 px-5 py-3">
          <div>
            <h3 className="text-sm font-semibold text-slate-100">{entry.name}</h3>
            <p className="mt-0.5 font-mono text-[11px] text-slate-500">{path}</p>
          </div>
          <button type="button" onClick={onClose} aria-label="Close" className="shrink-0 rounded p-1 text-slate-400 hover:bg-slate-800 hover:text-slate-200">✕</button>
        </div>
        <div className="flex flex-col gap-2 overflow-y-auto px-5 py-4">
          <div className="flex flex-wrap items-center gap-2 text-[11px]">
            {isJson && (
              <div className="inline-flex overflow-hidden rounded border border-slate-700">
                <button type="button" onClick={() => setView("preview")} className={`px-2 py-1 ${view === "preview" ? "bg-slate-700 text-slate-100" : "bg-slate-800/60 text-slate-400"}`}>Preview</button>
                <button type="button" onClick={editView} className={`px-2 py-1 ${view === "edit" ? "bg-slate-700 text-slate-100" : "bg-slate-800/60 text-slate-400"}`}>Edit</button>
                {hasDiscover && (
                  <button type="button" onClick={() => setView("classify")} className={`px-2 py-1 ${view === "classify" ? "bg-slate-700 text-slate-100" : "bg-slate-800/60 text-slate-400"}`}>Classify</button>
                )}
              </div>
            )}
            <label className="inline-flex cursor-pointer items-center gap-1 rounded bg-slate-700/60 px-2 py-1 text-slate-300 hover:bg-slate-700">
              ↑ Replace from file
              <input type="file" accept=".json,application/json,.yaml,.yml,text/*" className="hidden"
                onChange={(e) => { pickFile(e.target.files?.[0]); e.target.value = ""; }} />
            </label>
            <span className="text-slate-600">{bytes} B</span>
            {isJson && (jsonError
              ? <span className="rounded bg-rose-950/60 px-1.5 py-0.5 text-rose-300">invalid JSON</span>
              : draft.trim() && <span className="rounded bg-emerald-950/50 px-1.5 py-0.5 text-emerald-300">valid JSON</span>)}
            {dirty && <span className="text-amber-300">● unsaved</span>}
          </div>
          {isJson && view === "classify" && parsed.ok ? (
            <div className="h-[60vh] overflow-auto rounded border border-slate-800 bg-slate-900/40 px-4 py-3">
              <ClassifyBuilder
                service={service}
                schema={parsed.value}
                onApply={(obj) => { setDraft(JSON.stringify(obj, null, 2)); setView("preview"); }}
              />
            </div>
          ) : isJson && view === "preview" ? (
            <div className="h-[60vh] overflow-auto rounded border border-slate-800 bg-slate-950 px-3 py-2 font-mono text-[12px]">
              {parsed.ok
                ? <JsonView value={parsed.value} />
                : <span className="text-rose-300">{draft.trim() ? "Invalid JSON — switch to Edit to fix it." : "Empty document."}</span>}
            </div>
          ) : (
            <textarea
              className={`${inputCls} h-[60vh] font-mono text-[12px]`}
              value={draft}
              spellCheck={false}
              placeholder="document content"
              onChange={(e) => setDraft(e.target.value)}
            />
          )}
          {err && <p className="text-[11px] text-rose-300">{err}</p>}
        </div>
        <div className="flex items-center justify-between gap-3 border-t border-slate-800 px-5 py-3">
          <p className="text-[10px] text-slate-500">Saving writes the document and rolls {service} to pick it up.</p>
          <div className="flex gap-2">
            <button type="button" onClick={onClose} className={btn.ghost}>Cancel</button>
            <button type="button" onClick={save} disabled={busy || !!jsonError || !dirty} className={btn.sky}>
              {busy ? "Saving…" : "Save & restart"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// File-backed contract field (a *_FILE path, e.g. the rest-adapter's SCHEMA_FILE):
// shows the current document as a chip; clicking opens FileDocModal to view/edit/
// replace it. No paste-into-the-form textarea. Generic, driven only by the field
// being a *_FILE — no service-specific code.
function FileFieldEditor({ service, entry, toast, onApplied, shadowedBy }) {
  const path = entry.file_path || entry.value || entry.default || "";
  const fname = path.split("/").pop() || "document";
  const [content, setContent] = useState(null); // null while loading
  const [ephemeral, setEphemeral] = useState(false);
  const [open, setOpen] = useState(false);
  const [shadowCleared, setShadowCleared] = useState(false);
  const [clearing, setClearing] = useState(false);
  useEffect(() => {
    let alive = true;
    getNorthboundServiceFile(service, path).then((r) => {
      if (!alive) return;
      setContent(r.content || "");        // pre-fill (incl. a runtime/ephemeral copy)
      setEphemeral(!!r.ephemeral);
    }).catch(() => alive && setContent(""));
    return () => { alive = false; };
  }, [service, path]);
  const hasDoc = !!(content && content.trim());
  // Make the file the ACTIVE source, not just a mounted file: point its *_FILE env
  // at the path (the service reads the env, not the bare file) and clear any inline
  // twin that would override it. Only touches what is not already right (avoids a
  // needless rollout). Generic for any *_FILE field.
  const activate = async () => {
    const updates = {};
    if (((entry.value ?? "").toString()) !== path) updates[entry.name] = path;
    if (shadowedBy && !shadowCleared) updates[shadowedBy] = null; // null = unset
    if (Object.keys(updates).length) await applyNorthboundServiceConfig(service, updates);
  };
  const onSaved = async (saved) => {
    setContent(saved);
    setEphemeral(false);
    setOpen(false);
    try { await activate(); setShadowCleared(true); toast.success(`${entry.name} saved & active — rolling out ${service}`); }
    catch (e) { toast.success(`${entry.name} saved`); toast.error(`Could not activate the file: ${e.message}`); }
    onApplied?.(); // refresh the page so the rollout shows in the status list
  };
  // One-click resolve of the shadow: point the env at the file + unset the inline twin.
  const useThisFile = async () => {
    setClearing(true);
    try {
      await activate();
      setShadowCleared(true);
      toast.success(`Using ${fname} — rolling out ${service}`);
      onApplied?.();
    } catch (e) { toast.error(`Could not activate ${fname}: ${e.message}`); }
    finally { setClearing(false); }
  };
  const showShadow = shadowedBy && !shadowCleared;
  return (
    <div className="flex flex-col gap-1 py-1.5">
      <label className="flex items-center gap-2 font-mono text-[11px] text-slate-200">
        {entry.name}
        <span className="text-[9px] text-slate-600">document → {path}</span>
      </label>
      {entry.description && <p className="text-[10px] leading-snug text-slate-500">{entry.description}</p>}
      {ephemeral && (
        <p className="text-[10px] text-amber-300">⟳ Loaded at runtime but not persisted (lost on restart). Open it and save to store it declaratively.</p>
      )}
      {showShadow && (
        <div className="flex flex-wrap items-center gap-2 rounded border border-amber-900/50 bg-amber-950/30 px-2 py-1.5">
          <p className="text-[10px] text-amber-300">⚠ {shadowedBy} is set inline and overrides this document — editing the file has no effect until {shadowedBy} is cleared.</p>
          <button type="button" disabled={clearing} onClick={useThisFile} className={`${btn.amber} text-[10px]`}>
            {clearing ? "clearing…" : `Clear ${shadowedBy} & use this file`}
          </button>
        </div>
      )}
      <div className="flex items-center gap-2">
        {content === null ? (
          <span className="text-[11px] text-slate-500">loading…</span>
        ) : hasDoc ? (
          <button type="button" onClick={() => setOpen(true)}
            className="inline-flex items-center gap-2 rounded border border-slate-700 bg-slate-800/60 px-2.5 py-1.5 text-[11px] text-slate-200 hover:bg-slate-800">
            <span>📄 {fname}</span>
            <span className="text-slate-500">{new Blob([content]).size} B</span>
            <span className="text-sky-300">view / edit</span>
          </button>
        ) : (
          <button type="button" onClick={() => setOpen(true)}
            className="inline-flex items-center gap-1 rounded border border-dashed border-slate-600 px-2.5 py-1.5 text-[11px] text-slate-400 hover:border-slate-400 hover:text-slate-200">
            + Add {fname}
          </button>
        )}
        {ephemeral && hasDoc && <span className="rounded bg-amber-950/50 px-1.5 py-0.5 text-[9px] text-amber-300">not persisted</span>}
      </div>
      {open && (
        <FileDocModal service={service} entry={entry} path={path} initial={content}
          onClose={() => setOpen(false)} onSaved={onSaved} />
      )}
    </div>
  );
}

// One field of the guided setup. Non-sensitive shows the current value (editable);
// sensitive shows a password input with a "set" hint and never the value.
function ConfigField({ entry, required, value, onChange, upstreams, service, toast, onApplied, shadowedBy }) {
  // A file field (file_state set by the backend for path-valued *_FILE/*_PATH)
  // the dashboard owns is a document editor. When "external" (a PVC the service
  // writes itself, e.g. wifi-positioning's bindings/calibration), hands off: plain field.
  if (entry.file_state && service && entry.file_state !== "external") {
    return <FileFieldEditor service={service} entry={entry} toast={toast} onApplied={onApplied} shadowedBy={shadowedBy} />;
  }
  const placeholder = entry.sensitive
    ? (entry.set ? "•••• set — leave blank to keep" : (entry.example || ""))
    : (entry.value ?? entry.default ?? entry.example ?? "");
  const shown = value !== undefined ? value : (entry.sensitive ? "" : (entry.value ?? ""));
  // A *_URL field whose value is a cluster service points at another deployed
  // service the dashboard already knows. Offer a picker of those services so the
  // operator selects (e.g.) wittra instead of hand-typing the FQDN; "Custom URL"
  // falls back to free text for off-cluster upstreams.
  const isServiceUrl = /_URL$/.test(entry.name) && !entry.sensitive && (upstreams || []).length > 0;
  const [custom, setCustom] = useState(false);
  const useTextInput = !isServiceUrl || custom;
  return (
    <div className="flex flex-col gap-0.5 py-1.5">
      <label className="flex items-center gap-2 font-mono text-[11px] text-slate-200">
        {entry.name}
        {required && <span className="text-rose-400" title="required">*</span>}
        {entry.sensitive && <span className="rounded bg-slate-800 px-1 text-[9px] text-amber-300">secret</span>}
        {!required && !entry.set && <span className="text-[9px] text-slate-600">optional</span>}
      </label>
      {entry.description && <p className="text-[10px] leading-snug text-slate-500">{entry.description}</p>}
      {useTextInput ? (
        <input
          className={inputCls}
          type={entry.sensitive ? "password" : "text"}
          placeholder={placeholder}
          value={shown}
          onChange={(e) => onChange(entry.name, e.target.value)}
        />
      ) : (
        <select
          className={inputCls}
          value={shown}
          onChange={(e) => { if (e.target.value === "__custom__") { setCustom(true); } else { onChange(entry.name, e.target.value); } }}
        >
          <option value="">— use default ({entry.default || "unset"}) —</option>
          {upstreams.map((u) => (
            <option key={u.url} value={u.url}>{u.name} — {u.url}</option>
          ))}
          <option value="__custom__">Custom URL…</option>
        </select>
      )}
      {isServiceUrl && custom && (
        <button type="button" onClick={() => setCustom(false)} className="self-start text-[10px] text-slate-500 hover:text-slate-300">← pick a deployed service</button>
      )}
    </div>
  );
}

// Guided, contract-driven setup for one service. Reads /config (schema + current
// state), renders required -> recommended -> optional in order, applies via the
// single-mechanism backend (Secret vs ConfigMap by `sensitive`), then rolls out.
function ConfigureService({ service, services, toast, onClose, onApplied }) {
  const [cfg, setCfg] = useState(null);
  const [err, setErr] = useState(null);
  const [vals, setVals] = useState({});
  const [busy, setBusy] = useState(false);
  const [showAll, setShowAll] = useState(false);

  // Deployed services as pickable upstreams for *_URL fields (so the operator
  // selects e.g. wittra instead of typing the FQDN). Exclude the service itself.
  const upstreams = (services || [])
    .filter((s) => s.name !== service)
    .map((s) => ({ name: s.name, image: s.image, url: `http://${s.name}.${s.namespace}.svc.cluster.local:8080` }));
  const imageBase = (img) => (img || "").split("/").pop().split("@")[0].split(":")[0];

  useEffect(() => {
    let alive = true;
    setCfg(null); setErr(null); setVals({}); setShowAll(false);
    getNorthboundServiceConfig(service)
      .then((c) => {
        if (!alive) return;
        setCfg(c);
        if (!c.available) { setErr(c.error || "no contract"); return; }
        // The system fills what it can: seed the required fields the deployment
        // has NOT set with their contract default (else example) so they are
        // pre-filled and applied unless the operator overrides. Sensitive fields
        // are never auto-seeded (only a human supplies a token/key).
        const env = c.env || {};
        const seed = {};
        for (const f of (env.required || [])) {
          if (!f.set && !f.sensitive) {
            const sug = f.default ?? f.example;
            if (sug !== undefined && sug !== null) seed[f.name] = String(sug);
          }
        }
        // Semi-automatic URL fields: if a *_URL field's expected adapter (its
        // default host, e.g. "rest-adapter") matches exactly one deployed service
        // by image, pre-select that service. Ambiguous (>1) is left to the picker.
        for (const f of [...(env.recommended || []), ...(env.optional || [])]) {
          if (f.set || f.sensitive || !/_URL$/.test(f.name)) continue;
          const host = String(f.default || "").replace(/^https?:\/\//, "").split(":")[0];
          if (!host) continue;
          const match = (services || []).filter((s) => imageBase(s.image) === host);
          if (match.length === 1) seed[f.name] = `http://${match[0].name}.${match[0].namespace}.svc.cluster.local:8080`;
        }
        if (Object.keys(seed).length) setVals(seed);
      })
      .catch((e) => alive && setErr(e.message));
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [service]);

  const setVal = (name, v) => setVals((s) => ({ ...s, [name]: v }));
  const env = cfg?.env || {};
  const all = [
    ...(env.required || []).map((f) => ({ ...f, _req: true })),
    ...(env.recommended || []).map((f) => ({ ...f, _req: false })),
    ...(env.optional || []).map((f) => ({ ...f, _req: false })),
  ];
  // "Needs your input" = the human-only fields not yet provided: required not set
  // by the deployment, sensitive not set (a token/key only a human supplies), or a
  // *_FILE document field (significant config, shown prominently with its editor).
  // The rest (deploy-set, derivable, plain optional) is system-managed: collapsed.
  const isFile = (f) => !!f.file_state;
  // A file field is a "need" when the dashboard can provide it and it is missing
  // ("absent") or only runtime-loaded ("ephemeral", persist it). "managed"/"external"
  // are not needs. Non-file: required-or-sensitive and unset.
  const isNeed = (f) => isFile(f) ? (f.file_state === "absent" || f.file_state === "ephemeral") : (!f.set && (f._req || f.sensitive));
  // Three groups instead of one flat "configured/optional" dump: human-only scalars
  // (tokens/keys) up top, the file-backed documents (the vendor schema) in their own
  // prominent section, and everything else (deploy-set, plain optional) folded away.
  const fileFields = all.filter(isFile);
  const needScalars = all.filter((f) => !isFile(f) && isNeed(f));
  const restScalars = all.filter((f) => !isFile(f) && !isNeed(f));
  // A *_FILE document is silently ignored when its inline twin (same name without
  // _FILE, e.g. DEVICE_REGISTRY for DEVICE_REGISTRY_FILE) carries a value: many
  // services prefer the inline scalar over the file. Surface the twin so editing
  // the document is not a no-op. Generic: any FILE field with a set/entered twin.
  const byName = Object.fromEntries(all.map((f) => [f.name, f]));
  const fileShadowedBy = (entry) => {
    if (!entry.name.endsWith("_FILE")) return null;
    const twin = entry.name.replace(/_FILE$/, "");
    const t = byName[twin];
    if (!t) return null;
    const entered = (vals[twin] ?? "").toString().trim();
    const effective = entered || (t.set ? String(t.value ?? "set") : "");
    return effective ? twin : null;
  };
  // Only an unfilled REQUIRED scalar blocks Apply; optional secrets and *_FILE
  // documents (saved via their own editor button) do not.
  const unfilled = needScalars.filter((f) => f._req && !((vals[f.name] ?? "").toString().trim()));

  const submit = async () => {
    // Build the apply payload: a non-empty value sets the var; emptying a var that
    // the deployment currently HAS sends null (unset, deletes the key) so e.g. an
    // inline override can be cleared; emptying a never-set var is a no-op (skip).
    const payload = {};
    for (const [k, v] of Object.entries(vals)) {
      const f = byName[k];
      const wasSet = !!(f && (f.set || (f.value ?? "") !== ""));
      if (v !== "") payload[k] = v;
      else if (wasSet) payload[k] = null;
    }
    if (!Object.keys(payload).length) { toast.error("Nothing changed to apply"); return; }
    setBusy(true);
    try {
      await applyNorthboundServiceConfig(service, payload);
      toast.success(`${service}: config applied, rolling out`);
      onApplied?.();
      onClose();
    } catch (e) { toast.error(`Apply failed: ${e.message}`); }
    finally { setBusy(false); }
  };

  return (
    <Modal
      title={`Configure ${service}`}
      hint="The system fills what it can from the deployment; you supply only what only you have (tokens, keys). Sensitive values go to a Secret, the rest to a ConfigMap; the pod rolls to pick them up."
      onClose={onClose}
    >
      {!cfg && !err && <p className="text-xs text-slate-500">Loading contract…</p>}
      {err && (
        <Banner msg={{ text: `This service cannot be configured through the guided setup: ${err}.` }} />
      )}
      {cfg?.available && (
        <div className="flex flex-col gap-4 text-xs">
          {cfg.description && <p className="text-[11px] leading-snug text-slate-500">{cfg.description}</p>}

          {/* Human-only scalars: tokens/keys and required values the deployment can't fill. */}
          <div>
            <p className="mb-1.5 text-[10px] font-medium uppercase tracking-wide text-rose-400">Needs your input</p>
            {needScalars.length === 0 ? (
              <p className="text-[11px] text-emerald-400/80">No tokens or keys required, the deployment fills the rest.</p>
            ) : (
              <div className="flex flex-col divide-y divide-slate-800/60">
                {needScalars.map((f) => <ConfigField key={f.name} entry={f} required={f._req} value={vals[f.name]} onChange={setVal} upstreams={upstreams} service={service} toast={toast} onApplied={onApplied} shadowedBy={fileShadowedBy(f)} />)}
              </div>
            )}
          </div>

          {/* Documents: file-backed config (the vendor schema). Prominent, not buried. */}
          {fileFields.length > 0 && (
            <div>
              <p className="mb-1.5 text-[10px] font-medium uppercase tracking-wide text-sky-400">Documents</p>
              <div className="flex flex-col divide-y divide-slate-800/60">
                {fileFields.map((f) => <ConfigField key={f.name} entry={f} required={f._req} value={vals[f.name]} onChange={setVal} upstreams={upstreams} service={service} toast={toast} onApplied={onApplied} shadowedBy={fileShadowedBy(f)} />)}
              </div>
            </div>
          )}

          {/* Everything else (deploy-set, plain optional): folded away. */}
          {restScalars.length > 0 && (
            <div>
              <button type="button" onClick={() => setShowAll((v) => !v)} className="text-[10px] font-medium uppercase tracking-wide text-slate-500 transition-colors hover:text-slate-300">
                {showAll ? "▾" : "▸"} Optional settings ({restScalars.length})
              </button>
              {showAll && (
                <div className="mt-1 flex flex-col divide-y divide-slate-800/60">
                  {restScalars.map((f) => <ConfigField key={f.name} entry={f} value={vals[f.name]} onChange={setVal} upstreams={upstreams} service={service} toast={toast} onApplied={onApplied} shadowedBy={fileShadowedBy(f)} />)}
                </div>
              )}
            </div>
          )}

          <div className="flex items-center gap-3 border-t border-slate-800 pt-3">
            <button type="button" disabled={busy || unfilled.length > 0} onClick={submit} className={btn.sky}>
              {busy ? "Applying…" : "Apply & restart"}
            </button>
            {unfilled.length > 0 && (
              <span className="text-[11px] text-rose-400">fill: {unfilled.map((f) => f.name).join(", ")}</span>
            )}
          </div>
        </div>
      )}
    </Modal>
  );
}
