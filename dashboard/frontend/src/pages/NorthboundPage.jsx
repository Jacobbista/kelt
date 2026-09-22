import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { IconArrowLeft, IconRefresh, IconTrash } from "../components/icons";
import { Panel, Modal, Banner, Field, inputCls, btn } from "../components/ui";
import { useToast } from "../context/ToastContext";
import { useConfirm } from "../context/ConfirmContext";
import { env } from "../runtime-env";
import LogViewer from "../components/LogViewer";
import MappingStudio from "../components/MappingStudio";
import {
  getNorthboundServices,
  getNorthboundAdapters,
  getNorthboundLogHealth,
  getNorthboundContract,
  getNorthboundServiceContract,
  getNorthboundAccuracyClassVocabulary,
  getNorthboundBindings,
  setNorthboundServiceBinding,
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
  getNorthboundDiscoverRaw,
  restartDeployment,
} from "../api";

// Generic adapters published by 5g-northbound that an operator can deploy on
// demand; bring-your-own images use the same form. Two kinds, deliberately
// distinct: a "singleton" is a self-contained source you deploy at most once
// (wifi-adapter); a "template" is the generic vendor-adapter, instantiated
// once PER VENDOR (each gets its own name + REST API), so it can be deployed
// many times.
const CATALOG = [
  {
    name: "wifi-adapter",
    // Deploy-time default image; injected from all.yml (northbound_image_tags) via
    // env-config.js. Fallback tracks the same baseline for an un-injected bundle.
    image: env("VITE_NB_WIFI_IMAGE", "ghcr.io/jacobbista/5g-northbound/wifi-adapter:0.9.0"),
    kind: "singleton",
    blurb: "Wi-Fi RSSI positioning source. Deploy one.",
  },
  {
    name: "vendor-adapter",
    image: env("VITE_NB_REST_ADAPTER_IMAGE", "ghcr.io/jacobbista/5g-northbound/vendor-adapter:0.9.0"),
    kind: "template",
    // What it is and how it reaches the vendor are both in the schema loaded after
    // deploy (its `transport`: rest today, mqtt/webhook declared upstream). Nothing
    // about the vendor is decided here beyond the instance name.
    blurb: "Generic bridge to a vendor cloud. Deploy one per vendor, named after the vendor; the schema you load next describes the vendor's API and how it is reached (REST today).",
  },
];

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

// Each service has a data-path role: southbound adapters ingest, the engine fuses,
// northbound serves. Rendered north -> core -> south (compass/telco convention, north up).
// `role`/`lane` come from the kelt.io/role deploy-time label, with a heuristic fallback
// until every workload carries it.
// LANE_HEX applies inline so the same hex value drives chip text, dot, header, and rail.
const LANE_HEX = { north: "#38bdf8", core: "#a78bfa", south: "#2dd4bf" };
const LANE_ORDER = [
  ["north", "Northbound", "serve consumers"],
  ["core", "Core", "fusion"],
  ["south", "Southbound", "ingest from vendors & sensors"],
];
// Caption rendered between two lanes: what rises from the lower lane into the one above.
const LANE_CONNECT = { north: "positions served to consumers", core: "adapter fixes rise to the engine" };
// Renders a front-door proxy (oauth2-proxy-<gate>) nested under the service it fronts.
function orderLane(svcs) {
  const proxies = svcs.filter((s) => s.role === "proxy");
  const rest = svcs.filter((s) => s.role !== "proxy");
  const stemOf = (p) => (p.name || "").replace(/^oauth2-proxy-/, "");
  const out = [];
  for (const s of rest) {
    out.push({ svc: s, sub: false });
    for (const p of proxies) if (s.name.startsWith(stemOf(p))) out.push({ svc: p, sub: true });
  }
  for (const p of proxies) if (!rest.some((s) => s.name.startsWith(stemOf(p)))) out.push({ svc: p, sub: true });
  return out;
}

// Per-service label SUFFIX (not the full subdomain). KELT serves every surface at
// <prefix>-<suffix>.<base> (e.g. kelt-camara.<base>); the contract's `subdomain`
// field overrides the suffix when present. See docs/security/external-access.md.
const SUBDOMAIN_SUFFIX = {
  "camara-gateway": "camara",
  "location-app": "demo",
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

// Update-all: roll every companion service to the current 5g-northbound release
// (latest on ghcr). The backend persists the release tag, re-runs phase 10, then
// patches the catalog adapters (wifi/vendor REST) it does not own, all in one
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
  const [logHealth, setLogHealth] = useState({}); // deployment name -> {has_errors, sample}
  const [contract, setContract] = useState(null);
  const [busy, setBusy] = useState(false);
  const [configuring, setConfiguring] = useState(null);
  const [infoFor, setInfoFor] = useState(null);
  const [mappingSvc, setMappingSvc] = useState(null);
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

  // Matches an engine-registry entry to its Deployment by baseUrl host, not by name.
  // A self-registering adapter's ADAPTER_NAME (registry identity) can differ from its
  // Deployment name (e.g. synthetic-adapter registers as "synthetic"), but its baseUrl
  // always embeds the Deployment/Service DNS name. An entry with no matching Deployment
  // is registry-only: self-registered from outside this console (seeded, or an
  // off-cluster source), with no pod, logs, or configure action.
  const hostOfUrl = (url) => { try { return new URL(url).hostname.split(".")[0]; } catch { return null; } };
  const adapterMap = Object.fromEntries(
    adapters.map((a) => [hostOfUrl(a.baseUrl) || a.name, a])
  );
  const registryOnlyAdapters = adapters.filter(
    (a) => !services.some((s) => s.name === (hostOfUrl(a.baseUrl) || a.name))
  );

  // Vendor bindings, grouped by consumer, for display inline in each consumer's row.
  const bindingsByConsumer = {};
  for (const b of bindings) (bindingsByConsumer[b.consumer] ||= []).push(b);

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

  // How each adapter reaches its source (contract `transport`, 0.17.1+). Read once
  // per adapter image, not on the 5s poll: it is a property of the image plus the
  // loaded schema, and each read is a proxied call into the pod.
  const [transportOf, setTransportOf] = useState({});
  const adapterImages = services.filter((s) => s.role === "adapter").map((s) => `${s.name}@${s.image}`).join("|");
  useEffect(() => {
    let alive = true;
    const names = adapterImages ? adapterImages.split("|").map((x) => x.split("@")[0]) : [];
    Promise.all(names.map((n) =>
      getNorthboundServiceContract(n)
        .then((c) => [n, c?.available ? { transport: c.contract?.transport || null, transports: c.contract?.transports || null } : null])
        .catch(() => [n, null])
    )).then((pairs) => { if (alive) setTransportOf(Object.fromEntries(pairs)); });
    return () => { alive = false; };
  }, [adapterImages]);

  // Log health on its own, slower poll: it reads every pod's actual log tail
  // (real per-pod calls, not a status read), so it does not belong on the 5s
  // tick. 30s is plenty for a "go look" indicator, not a live metric.
  useEffect(() => {
    const load = () => getNorthboundLogHealth().then(setLogHealth).catch(() => {});
    load();
    const id = setInterval(load, 30000);
    return () => clearInterval(id);
  }, []);

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
      toast.success(`Detected ${names.join(", ")}, binding ${consumer}`);
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
          <div className="flex items-center gap-2">
            <button type="button" onClick={manualRefresh} disabled={refreshing} className={`inline-flex items-center gap-1 ${btn.ghost} disabled:opacity-60`}>
              <IconRefresh size={14} className={refreshing ? "animate-spin" : ""} /> {refreshing ? "refreshing…" : "refresh"}
            </button>
          </div>
        </div>
      </header>

      <div className="flex flex-col gap-4">
        {isAdmin && behindCount > 0 && (
          <div className="flex items-center justify-between gap-3 rounded-lg border border-sky-800/50 bg-sky-950/30 px-4 py-2.5">
            <span className="text-xs text-sky-200">
              <span className="font-semibold">{behindCount} update{behindCount > 1 ? "s" : ""} available</span>
              {", "}newer 5g-northbound image{behindCount > 1 ? "s are" : " is"} on ghcr.
            </span>
            <button type="button" onClick={() => setShowUpdateAll(true)} className={btn.sky}>↑ Update all</button>
          </div>
        )}
        <Panel
          title="Services"
          hint="Positioning and CAMARA services. “managed” roll via Update all; catalog adapters (wifi, vendor REST) upgrade individually."
        >
          {services.length === 0 ? (
            <p className="text-xs text-slate-500">No northbound services found. Enable the feature with <span className="font-mono">testbed northbound on</span>.</p>
          ) : (
            <div className="flex flex-col gap-2">
              <div className="mb-1 flex flex-wrap items-center gap-2 rounded-lg border border-slate-800 bg-slate-950/40 px-3 py-2 text-[11px] text-slate-500">
                <span>data rises</span><span className="text-slate-600">↑</span>
                <span className="rounded border border-slate-800 px-2 py-0.5" style={{ color: LANE_HEX.north }}>gateway + apps</span><span className="text-slate-600">↑</span>
                <span className="rounded border border-slate-800 px-2 py-0.5" style={{ color: LANE_HEX.core }}>engine · fusion</span><span className="text-slate-600">↑</span>
                <span className="rounded border border-slate-800 px-2 py-0.5" style={{ color: LANE_HEX.south }}>southbound adapters</span><span className="text-slate-600">↑</span>
                <span>vendors &amp; sensors</span>
              </div>
              {LANE_ORDER.map(([lane, laneTag, laneSub]) => {
                const laneSvcs = services.filter((s) => (s.lane || "north") === lane);
                if (!laneSvcs.length) return null;
                const hue = LANE_HEX[lane];
                return (
                  <div key={lane}>
                    <div className="border-l-2 pl-3" style={{ borderColor: hue }}>
                      <div className="mb-1 flex items-center justify-between gap-2">
                        <div className="flex items-center gap-2">
                          <span className="text-[10.5px] font-semibold uppercase tracking-wider" style={{ color: hue }}>{laneTag}</span>
                          <span className="text-[11px] text-slate-500">{laneSub}</span>
                        </div>
                        {/* Each lane's own action, accented with that lane's hue. */}
                        <div className="flex items-center gap-1.5">
                          {lane === "north" && isAdmin && (
                            <Link to="/services/northbound/assets" className="rounded border px-2 py-1 text-[11px] font-medium transition-colors hover:bg-white/5" style={{ borderColor: hue, color: hue }}>Assets</Link>
                          )}
                          {lane === "south" && isAdmin && (
                            <button type="button" onClick={() => setDeployOpen(true)} className="rounded border px-2 py-1 text-[11px] font-medium transition-colors hover:bg-white/5" style={{ borderColor: hue, color: hue }}>Deploy adapter</button>
                          )}
                        </div>
                      </div>
                      <div className="flex flex-col divide-y divide-slate-800/60">
                        {orderLane(laneSvcs).map(({ svc: s, sub: isSub }) => {
                        const phase = s.pods && s.pods[0] ? s.pods[0].phase : "Unknown";
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
                // The backend reports `rollout` and the image a ready pod ACTUALLY runs,
                // which can lag the spec while the new pod crashes and the old one keeps serving.
                const degraded = s.rollout === "degraded";
                const runTag = s.running_image ? imgTag(s.running_image) : null;
                const imgMismatch = !!(runTag && tag && runTag !== tag);
                const badPod = (s.pods || []).find((p) => p.waiting_reason || (!p.ready && (p.restarts || 0) > 0));
                const rolloutTitle = degraded
                  ? `rollout not complete: ${badPod ? `${badPod.name}, ${badPod.waiting_reason || "not ready"} (${badPod.restarts} restarts)` : "pods unavailable"}${imgMismatch ? ` · serving ${runTag}, spec is ${tag}` : ""}`
                  : "";
                // Ready/rollout misses a pod that is up and serving most requests fine
                // while silently 500ing on one (a caught exception, not a crash) - the
                // engine's ZeroDivisionError on a zero-accuracy fusion (2026-09-11) looked
                // perfectly healthy by every other signal on this row.
                const logErr = logHealth[s.name];
                // Engine-registry state for a south-lane adapter: membership/reachability
                // the engine itself reports, distinct from the pod phase above (a pod can be
                // Running while its heartbeat to the engine goes stale or unreachable).
                const reg = s.role === "adapter" ? adapterMap[s.name] : null;
                // What an adapter is, from live facts rather than a label: the image's
                // family (registry `kind`, declared by the image itself since 0.17.1) and
                // the transport it reaches its source over (contract). The source it
                // speaks for is its own chip, because a missing one is the operator's
                // signal that ADAPTER_CAPABILITIES is not set yet.
                const tr = s.role === "adapter" ? transportOf[s.name] : null;
                const adapterSubtitle = reg
                  ? [reg.kind, tr?.transport ? tr.transport.toUpperCase() : null].filter(Boolean).join(" · ")
                  : null;
                const subtitle = adapterSubtitle ?? s.subtitle;
                const declaredSource = reg?.capabilities?.source || null;
                const regState = reg && (ADAPTER_STATE[reg.state] || { cls: "bg-slate-700/60 text-slate-400", label: reg.state || "?" });
                // This consumer's own adapter bindings (e.g. placement-editor -> vendor-adapter).
                const myBindings = bindingsByConsumer[s.name] || [];
                return (
                  <div key={`${s.namespace}/${s.name}`} className={isSub ? "pl-6 opacity-70" : ""}>
                  <div className="flex items-center gap-3 py-2.5">
                    <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${degraded ? "bg-rose-500" : phaseDot(phase)}`} title={degraded ? rolloutTitle : phase} />
                    {/* Primary: name + classifying/state chips on line 1, the de-emphasized
                        image + public link on line 2. Keeps rows aligned regardless of which
                        optional chips a service has. */}
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-sm font-semibold text-slate-100">{s.name}</span>
                        {s.role && (
                          <span className="inline-flex items-center gap-1.5 rounded border border-slate-800 bg-slate-900 px-1.5 py-0.5 text-[10px] font-semibold" style={{ color: LANE_HEX[s.lane] || "#94a3b8" }} title="role in the data path">
                            <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: LANE_HEX[s.lane] || "#94a3b8" }} />{s.role}
                            {subtitle && <span className="font-normal text-slate-500"> · {subtitle}</span>}
                          </span>
                        )}
                        {reg && (declaredSource ? (
                          <span className="rounded border border-slate-800 bg-slate-900 px-1.5 py-0.5 text-[9.5px] text-slate-400" title="the source name its fixes carry (ADAPTER_CAPABILITIES)">source <span className="font-mono text-slate-200">{declaredSource}</span></span>
                        ) : (
                          <span className="rounded bg-amber-950/50 px-1.5 py-0.5 text-[9.5px] text-amber-300" title="No source declared: the engine drops its fixes. Set ADAPTER_CAPABILITIES in Configure.">no source declared</span>
                        ))}
                        {regState && (
                          <span
                            className={`rounded px-1.5 py-0.5 text-[9px] ${regState.cls}`}
                            title={`engine registry: ${regState.label}${reg.registeredVia ? ` · via ${reg.registeredVia}` : ""}${typeof reg.lastSeenSAgo === "number" ? ` · seen ${Math.round(reg.lastSeenSAgo)}s ago` : ""}`}
                          >
                            {regState.label}
                          </span>
                        )}
                        {reg?.inCooldown && <span className="text-[9px] text-rose-400">cooldown {Math.round(reg.cooldownSecondsRemaining || 0)}s</span>}
                        {tag && <span className={`font-mono text-[10px] ${degraded && imgMismatch ? "text-slate-500 line-through" : "text-slate-400"}`} title={s.image || ""}>{tag}</span>}
                        {degraded && imgMismatch && <span className="rounded bg-rose-950/50 px-1.5 py-0.5 font-mono text-[10px] text-rose-300" title={`ready pod runs ${s.running_image}, deployment spec is ${s.image}`}>serving {runTag}</span>}
                        {degraded && <span className="rounded bg-rose-950/50 px-1.5 py-0.5 text-[10px] text-rose-300" title={rolloutTitle}>⚠ rollout failed</span>}
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
                                ? `deployed ${tag}; latest ${latestTag}, rolls via Update all`
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
                          <span className="rounded bg-amber-950/40 px-1.5 py-0.5 text-[10px] text-amber-300" title={`loaded but not persisted (lost on restart): ${readiness[s.name].ephemeral.join(", ")}, persist via Configure`}>⟳ ephemeral</span>
                        )}
                        {s.stateful && s.persistent === false && (
                          <span className="rounded bg-amber-950/40 px-1.5 py-0.5 text-[10px] text-amber-300" title="calibration is written in this service's own UI but is not PVC-backed yet, it would be lost on restart. Click “enable persistence”.">⟳ not persisted</span>
                        )}
                      </div>
                      <div className="mt-0.5 flex flex-wrap items-center gap-2 text-[11px] text-slate-500">
                        <span className="uppercase tracking-wide">{s.namespace}</span>
                        {ep ? (
                          <a href={ep.url} target="_blank" rel="noreferrer" title={`open ${ep.url}`} className="inline-flex shrink-0 items-center gap-1 font-mono text-sky-400 hover:text-sky-300">{ep.label.split(".")[0]} <span aria-hidden="true">↗</span></a>
                        ) : s.node_port ? (
                          <span className="shrink-0 font-mono text-[10px]" title="LAN NodePort">:{s.node_port}</span>
                        ) : null}
                        {/* Push adapter: attached to the n6m 5G data network so an edge
                            scanner can POST to it. Shows the reserved ingest address. */}
                        {s.n6m_ip && (
                          <span
                            className="shrink-0 rounded bg-teal-950/40 px-1.5 py-0.5 font-mono text-[10px] text-teal-300"
                            title={`attached to the 5G data network (n6m); edge scanners POST to http://${s.n6m_ip}:8080`}
                          >
                            n6m {s.n6m_ip}
                          </span>
                        )}
                        {s.description && <span className="text-slate-500">{s.description}</span>}
                        {/* Which adapter this consumer is bound to. */}
                        {myBindings.map((b) => (
                          <span key={b.field} className="inline-flex items-center gap-1 rounded bg-slate-800/60 px-1.5 py-0.5 text-[10px]">
                            <span className="text-slate-500">→</span>
                            {b.candidates.length === 0 ? (
                              <span className="text-slate-500">no {b.kind} deployed</span>
                            ) : b.bound_to ? (
                              <span className="font-mono text-emerald-400">{b.bound_to} ✓</span>
                            ) : b.candidates.length === 1 ? (
                              <span className="inline-flex items-center gap-1 text-amber-300"><IconRefresh size={9} className="animate-spin" /> binding {b.candidates[0].name}…</span>
                            ) : isAdmin ? (
                              <select
                                className="rounded border border-slate-700 bg-slate-950 px-1 py-0.5 text-[10px] text-slate-200"
                                value={(b.candidates.find((c) => c.name === b.bound_to) || {}).url || ""}
                                onChange={(e) => { if (e.target.value) bindAdapter(b, e.target.value); }}
                                disabled={busy}
                              >
                                <option value="">{b.candidates.length} {b.kind}s, pick one</option>
                                {b.candidates.map((c) => <option key={c.url} value={c.url}>{c.name}</option>)}
                              </select>
                            ) : (
                              <span className="text-amber-300">{b.candidates.length} {b.kind}s, pick one</span>
                            )}
                          </span>
                        ))}
                      </div>
                    </div>
                    {/* Right cluster: status + actions, aligned across every row. */}
                    <div className="flex shrink-0 items-center gap-2">
                      <span className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 font-mono text-[10px] ${degraded ? "bg-rose-950/40 text-rose-300" : starting ? "bg-amber-950/40 text-amber-300" : "bg-slate-800 text-slate-300"}`} title={degraded ? rolloutTitle : starting ? "pods starting" : "ready"}>
                        {starting && !degraded && <IconRefresh size={10} className="animate-spin" />}
                        {s.ready_replicas}/{s.replicas}
                      </span>
                      {s.pods && s.pods[0] && (() => {
                        // Prefers the crashing pod over the healthy old one when degraded.
                        const logPod = badPod || s.pods[0];
                        return (
                          <button
                            type="button"
                            title={degraded ? `stream logs (${logPod.name})` : logErr ? `stream logs (${logPod.name}) — recent error: ${logErr.sample}` : `stream logs (${logPod.name})`}
                            onClick={() => setLogTarget({ namespace: s.namespace, pod: logPod.name, deployment: s.name })}
                            className={`rounded px-2 py-1 text-[11px] font-medium transition-colors ${degraded ? "bg-rose-600/20 text-rose-300 hover:bg-rose-600/30"
                              : logErr ? "bg-amber-600/20 text-amber-300 ring-1 ring-amber-500/40 hover:bg-amber-600/30"
                              : "bg-indigo-600/20 text-indigo-300 hover:bg-indigo-600/30"}`}
                          >
                            logs
                          </button>
                        );
                      })()}
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
                      {/* Read-only view for viewers; an admin reads the same rows inside Configure. */}
                      {!isAdmin && s.configurable && (
                        <button type="button" onClick={() => setInfoFor(s.name)} className={btn.ghost} title="what this service reads and who provides it">info</button>
                      )}
                      {isAdmin && s.configurable && (
                        <button type="button" onClick={() => setConfiguring(s.name)} className={btn.ghost}>configure</button>
                      )}
                      {/* A plain restart, no config or image change: the same generic
                          action the 5G Core page offers per NF, wired here too so an
                          operator does not have to go through Upgrade or a document
                          save just to bounce a pod (e.g. to pick up a file this session
                          wrote directly, outside any Configure flow). */}
                      {isAdmin && (
                        <button
                          type="button"
                          disabled={busy}
                          title={`restart ${s.name}`}
                          onClick={() => setConfirm({
                            title: `Restart ${s.name}?`,
                            body: "Rolls the pod. Config and image are unchanged.",
                            label: "Restart",
                            runLabel: `restart ${s.name}`,
                            action: () => restartDeployment(s.namespace, s.name),
                          })}
                          className={btn.ghost}
                        >
                          restart
                        </button>
                      )}
                      {isAdmin && s.has_mapping && (
                        <button type="button" onClick={() => setMappingSvc(s.name)} className="rounded border border-sky-500/40 bg-sky-500/15 px-2 py-1 text-[11px] font-medium text-sky-300 hover:bg-sky-500/25">◎ mapping</button>
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
                  </div>
                );
                      })}
                      </div>
                    </div>
                    {LANE_CONNECT[lane] && (
                      <div className="flex items-center gap-2 py-1.5 pl-3 text-[10.5px] text-slate-600">
                        <span className="h-px flex-1 bg-gradient-to-r from-slate-700 to-transparent" />
                        ↑ {LANE_CONNECT[lane]} ↑
                        <span className="h-px flex-1 bg-gradient-to-l from-slate-700 to-transparent" />
                      </div>
                    )}
                  </div>
                );
              })}
              {/* Registry-only adapters have no matching Deployment (seeded or
                  registered from an off-cluster source), so no pod/logs/configure. */}
              {registryOnlyAdapters.length > 0 && (
                <div className="border-l-2 pl-3" style={{ borderColor: LANE_HEX.south }}>
                  <div className="flex flex-col divide-y divide-slate-800/60">
                    {registryOnlyAdapters.map((a) => {
                      const st = ADAPTER_STATE[a.state] || { cls: "bg-slate-700/60 text-slate-400", label: a.state || "?" };
                      const canForce = a.state !== "live";
                      return (
                        <div key={a.name} className="flex items-center gap-3 py-2.5 text-xs">
                          <div className="min-w-0 flex-1">
                            <div className="flex flex-wrap items-center gap-2">
                              <span className="font-mono font-semibold text-slate-200">{a.name}</span>
                              {a.kind && <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[9px] text-slate-400">{a.kind}</span>}
                              <span className={`rounded px-1.5 py-0.5 text-[9px] ${st.cls}`}>{st.label}</span>
                              {a.inCooldown && <span className="text-[9px] text-rose-400">cooldown {Math.round(a.cooldownSecondsRemaining || 0)}s</span>}
                            </div>
                            <div className="mt-0.5 flex flex-wrap items-center gap-2 text-[10px] text-slate-600">
                              {a.registeredVia && <span>via {a.registeredVia}</span>}
                              {typeof a.lastSeenSAgo === "number" && <span>· seen {Math.round(a.lastSeenSAgo)}s ago</span>}
                              <span className="truncate font-mono">{a.baseUrl}</span>
                            </div>
                            {a.n6m_ip && (
                              <div className="mt-0.5 text-[10px] text-teal-400">
                                5G ingest: <span className="font-mono">http://{a.n6m_ip}:8080</span> <span className="text-slate-600">(over n6m)</span>
                              </div>
                            )}
                          </div>
                          {isAdmin && canForce && (
                            <button
                              type="button"
                              disabled={busy}
                              title="force-remove stale entry"
                              onClick={() => setConfirm({
                                title: `Force-remove ${a.name}?`,
                                body: "Clears this stale entry from the engine registry. A live adapter would re-announce on its next heartbeat.",
                                label: "Force-remove",
                                runLabel: `force-remove ${a.name}`,
                                action: () => unregisterNorthboundAdapter(a.name),
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
                </div>
              )}
            </div>
          )}
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
        {deployOpen && (
          <Modal
            title="Deploy adapter"
            hint="Creates a Deployment and Service in the positioning namespace. The adapter self-registers with the engine, then you give it its vendor settings from Configure."
            onClose={() => setDeployOpen(false)}
          >
            <DeployForm
              busy={busy}
              existing={new Set(services.map((s) => s.name))}
              onSubmit={async (body) => { await run(`deploy ${body.name}`, () => deployNorthboundImage(body)); setDeployOpen(false); }}
            />
          </Modal>
        )}
        {configuring && (
          <ConfigureService
            service={configuring}
            services={services}
            bindings={bindingsByConsumer[configuring] || []}
            toast={toast}
            onClose={() => setConfiguring(null)}
            onApplied={() => { refresh(); loadBindings(); }}
          />
        )}
        {infoFor && (
          <ServiceInfo service={infoFor} services={services} onClose={() => setInfoFor(null)} />
        )}
        {mappingSvc && (
          <MappingStudio service={mappingSvc} onClose={() => setMappingSvc(null)} onSaved={() => refresh()} />
        )}
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

// Deploy settles the adapter's IDENTITY: what image to run and what to call it.
// Vendor settings are deliberately not collected here. The adapter publishes its
// own /contract, and Configure renders it afterwards with the real descriptions and
// Secret routing, which a pair of blank ENV_NAME/value boxes cannot do.
function DeployForm({ busy, existing, onSubmit }) {
  const [choice, setChoice] = useState(null); // a CATALOG entry, or "custom"
  const [name, setName] = useState("");
  const [image, setImage] = useState("");
  const [port, setPort] = useState(8080);
  const [pullSecret, setPullSecret] = useState("");
  const [advOpen, setAdvOpen] = useState(false);

  // A singleton carries its own fixed name; a template and a custom image are named
  // by the operator, so the name starts blank and is the one thing left to decide.
  const pick = (c) => {
    setChoice(c);
    if (c === "custom") { setImage(""); setName(""); return; }
    setImage(c.image);
    setName(c.kind === "singleton" ? c.name : "");
  };
  const isSingleton = choice && choice !== "custom" && choice.kind === "singleton";
  const isCustom = choice === "custom";
  const ready = !!choice && !!name.trim() && !!image.trim();

  const submit = (e) => {
    e.preventDefault();
    if (!ready) return;
    onSubmit({
      name: name.trim(),
      image: image.trim(),
      port: Number(port) || 8080,
      image_pull_secret: pullSecret.trim() || null,
      env: [],
    });
  };

  const card = (on) => `rounded border px-3 py-2 text-left transition-colors ${on ? "border-sky-500/40 bg-sky-500/15" : "border-slate-800 bg-slate-950/40 hover:bg-white/5"}`;

  return (
    <form className="flex flex-col gap-4 text-xs" onSubmit={submit}>
      <div className="flex flex-col gap-1.5">
        {CATALOG.map((c) => {
          // A singleton is one per cluster, so once it is running there is nothing
          // to deploy: show it as present rather than offering a second copy.
          const taken = c.kind === "singleton" && !!existing?.has(c.name);
          return (
            <button key={c.name} type="button" disabled={taken} onClick={() => pick(c)} className={`${card(choice === c)} ${taken ? "opacity-50" : ""}`}>
              <span className="flex items-center gap-2">
                <span className="text-xs font-semibold text-slate-100">{c.name}</span>
                <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[9px] text-slate-400">
                  {taken ? "already deployed" : c.kind === "singleton" ? "deploy one" : "one per vendor"}
                </span>
              </span>
              <span className="mt-0.5 block text-[10px] leading-snug text-slate-500">{c.blurb}</span>
            </button>
          );
        })}
        <button type="button" onClick={() => pick("custom")} className={card(isCustom)}>
          <span className="text-xs font-semibold text-slate-100">Custom image</span>
          <span className="mt-0.5 block text-[10px] leading-snug text-slate-500">Any image that speaks the adapter contract.</span>
        </button>
      </div>

      {choice && (
        <div className="flex flex-col gap-3 border-t border-slate-800 pt-3">
          {isSingleton ? (
            <p className="text-[11px] text-slate-400">
              Deploys as <span className="font-mono text-slate-200">{name}</span> from <span className="font-mono text-slate-500">{`${imgBasename(image)}:${imgTag(image)}`}</span>.
            </p>
          ) : (
            <>
              {isCustom && (
                <FormField label="Image" help="Repository and tag, for example ghcr.io/your-org/your-adapter:1.0.0.">
                  <input className={inputCls} placeholder="image:tag" value={image} onChange={(e) => setImage(e.target.value)} />
                </FormField>
              )}
              <FormField label="Instance name" help="The name it registers under in the engine. For a vendor adapter, name it after the vendor.">
                <input className={inputCls} placeholder={isCustom ? "adapter name" : "vendor name"} value={name} onChange={(e) => setName(e.target.value)} />
              </FormField>
            </>
          )}

          <div>
            <button type="button" onClick={() => setAdvOpen((v) => !v)} className="text-[10px] font-medium uppercase tracking-wide text-slate-500 transition-colors hover:text-slate-300">
              {advOpen ? "▾" : "▸"} Advanced
            </button>
            {advOpen && (
              <div className="mt-2 flex flex-col gap-3">
                <FormField label="Container port" help="The port the adapter listens on. Almost always 8080.">
                  <input className={`${inputCls} w-24`} type="number" value={port} onChange={(e) => setPort(e.target.value)} />
                </FormField>
                <FormField label="Image pull secret" help="Only for an image in a private registry.">
                  <input className={inputCls} placeholder="none" value={pullSecret} onChange={(e) => setPullSecret(e.target.value)} />
                </FormField>
              </div>
            )}
          </div>
        </div>
      )}

      <div className="flex items-center gap-3 border-t border-slate-800 pt-3">
        <button type="submit" disabled={busy || !ready} className={btn.sky}>Deploy</button>
        <span className="text-[10px] leading-snug text-slate-500">
          {choice ? "It registers with the engine on boot, then Configure asks for its vendor settings." : "Pick what to deploy."}
        </span>
      </div>
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
// JSON, and shows its live effect on the real sample. Authors role only: sourceClass
// (the radio) is deliberately NOT set here, because the vendor device list does not
// report the per-device radio, so guessing it from a device TYPE would be wrong (see
// the note in the body). Apply merges discover.mapping.deviceType + discover.classify
// back into the schema and preserves any existing sourceClass config; the existing
// Save & restart persists it (ConfigMap + rollout). Structural, not vendor-specific:
// it only assumes a `discover` block exists.
function ClassifyBuilder({ service, schema, onApply }) {
  const [raw, setRaw] = useState(null);
  const [vendor, setVendor] = useState("");
  const [err, setErr] = useState(null);
  const [loading, setLoading] = useState(true);

  const cls = schema?.discover?.classify || {};
  const [typeField, setTypeField] = useState(schema?.discover?.mapping?.deviceType?.path || "deviceType");
  const [assetValue, setAssetValue] = useState(cls?.assetWhen?.equals ?? "");
  const hasSourceClass = !!cls.sourceClassDefault || (Array.isArray(cls.sourceClassRules) && cls.sourceClassRules.length > 0);

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
    next.discover.mapping.deviceType = { path: typeField };
    // Author role only. Preserve any operator-authored sourceClass config, never add one.
    const keepSC = {};
    if (cls.sourceClassDefault) keepSC.sourceClassDefault = cls.sourceClassDefault;
    if (Array.isArray(cls.sourceClassRules) && cls.sourceClassRules.length) keepSC.sourceClassRules = cls.sourceClassRules;
    next.discover.classify = { assetWhen: { path: typeField, equals: assetValue }, ...keepSC };
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
      The adapter returned no devices, nothing to sample. Check the vendor connection, or edit the JSON by hand.
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
            <option value="">(pick a value)</option>
            {distinctValues.map((v) => <option key={v} value={v}>{v}</option>)}
          </select>
        </div>
      </Field>
      {assetValue !== "" && (
        <div className="rounded border border-slate-800 bg-slate-950 px-3 py-2 text-[11px]">
          On this sample: <span className="text-emerald-300">{counts.asset} asset{counts.asset === 1 ? "" : "s"}</span>
          {" · "}<span className="text-slate-300">{counts.infra} infrastructure</span>
          {counts.asset === 0 && <span className="ml-2 text-amber-400">no device matches, check the value</span>}
        </div>
      )}
      <div className="rounded border border-slate-800 bg-slate-950/60 px-3 py-2 text-[11px] text-slate-400">
        <span className="text-slate-300">Source class (radio)</span> is not set here. The vendor list does not report the per-device radio, so KELT does not guess it from the device type. Add <span className="font-mono text-slate-300">sourceClassRules</span> by hand in the JSON only from a real signal (a positioning join, or site knowledge).
        {hasSourceClass && <span className="text-emerald-400"> Existing sourceClass config is preserved.</span>}
      </div>
      <details className="rounded border border-slate-800 bg-slate-950">
        <summary className="cursor-pointer px-3 py-2 text-[11px] text-slate-400 hover:text-slate-200">Show a raw vendor record</summary>
        <div className="max-h-56 overflow-auto border-t border-slate-800 px-3 py-2 font-mono text-[11px]">
          <JsonView value={sample} />
        </div>
      </details>
      <div className="flex items-center justify-between gap-2 border-t border-slate-800 pt-3">
        <p className="text-[10px] text-slate-500">Apply merges <span className="font-mono">deviceType</span> + <span className="font-mono">classify</span> into the schema below.</p>
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
                : <span className="text-rose-300">{draft.trim() ? "Invalid JSON, switch to Edit to fix it." : "Empty document."}</span>}
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

// A contract description is written for a README (the vendor-adapter's SCHEMA_FILE
// runs to a paragraph on ConfigMap mounts and PUT semantics). A form field carries
// the first sentence and keeps the rest on hover, so the env var's own name stays
// the primary label instead of prose filling the row.
function FieldHelp({ text }) {
  const s = String(text || "").trim();
  if (!s) return null;
  const m = s.match(/^[\s\S]*?[.!?](?=\s|$)/);
  const head = (m ? m[0] : s).trim();
  const truncated = head.length < s.length;
  return (
    <p className="mt-0.5 text-[11px] leading-snug text-slate-500" title={truncated ? s : undefined}>
      {head}{truncated && <span className="text-slate-600"> …</span>}
    </p>
  );
}

// A Configure section: a colored rail marks what kind of field this is (Connection,
// Field mapping, Options), same hue family as the service's own lane chip elsewhere
// on the page.
const SECTION_HUE = { rose: "#fb7185", sky: "#38bdf8", neutral: "#94a3b8" };
function ConfigSection({ tone, title, children }) {
  const hue = SECTION_HUE[tone] || SECTION_HUE.neutral;
  return (
    <div className="flex flex-col gap-3 border-b border-slate-800 py-4 first:pt-0 last:border-b-0 last:pb-0">
      <div className="flex w-full items-center gap-2">
        <span style={{ width: 3, height: 13, borderRadius: 2, backgroundColor: hue }} />
        <span className="text-[10px] font-bold uppercase tracking-wide" style={{ color: hue }}>{title}</span>
      </div>
      <div className="flex flex-col gap-3">{children}</div>
    </div>
  );
}

// A two-column field row: label + description on the left, the control fixed-width
// on the right. Keeps a whole section scannable as a column of controls instead of
// each field stacking its own full-width block.
function FieldRow({ name, badges, desc, children, alert }) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="font-mono text-[12px] font-semibold text-slate-100">{name}</span>
            {badges}
          </div>
          {desc}
        </div>
        <div className="flex w-56 shrink-0 flex-col items-end gap-1">{children}</div>
      </div>
      {alert}
    </div>
  );
}

// A document the dashboard can own (edit, seed, persist): a *_FILE the backend
// reports as managed / absent / ephemeral. "external" is a PVC the service writes
// itself, "internal" a path that is the service's own (image or runtime file).
const isOwnedDocument = (f) => !!f.file_state && !["external", "internal"].includes(f.file_state);
// What Configure offers: values the operator owns. Deployment wiring (all.yml,
// inline env), KELT's own registration env and storage paths are not settings,
// they are read in the service's info panel. A document is the one exception to
// the owner check: who set the PATH string (often the deployment, e.g. once a
// stateful doc has been redirected onto its PVC) is separate from who owns the
// FILE's content at that path, which is the operator whenever the backend calls
// it "managed" (its own apply_service_file mechanism can seed/restore it).
const isOperatorSetting = (f) =>
  !f.managed && (isOwnedDocument(f) || (f.owner !== "deployment" && !f.file_state));

// What a field is actually used for, folded up from the contract's `declared_at`
// (the exact schema locations that reference the variable). Answers "what breaks if
// I leave this empty" in a few words, where printing four dotted paths would just be
// more prose. The paths stay available on hover.
const USED_FOR = [
  [/^auth\b/, "auth"],
  [/^discover\b/, "discovery"],
  [/^diagnostics\b/, "diagnostics"],
  [/^(baseUrl|pathVars|path)\b/, "position fetch"],
];
function usedFor(declaredAt) {
  const out = [];
  for (const loc of declaredAt || []) {
    const hit = USED_FOR.find(([re]) => re.test(loc));
    const label = hit ? hit[1] : String(loc).split(".")[0];
    if (!out.includes(label)) out.push(label);
  }
  return out;
}

// A labelled form control. The label carries what the value IS; the raw env var or
// flag name, when it matters, belongs in the help line underneath.
function FormField({ label, help, children }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[11px] font-medium text-slate-300">{label}</span>
      {children}
      {help && <span className="text-[10px] leading-snug text-slate-500">{help}</span>}
    </label>
  );
}

// File-backed contract field (a *_FILE path, e.g. the vendor-adapter's SCHEMA_FILE):
// shows the current document as a chip; clicking opens FileDocModal to view/edit/
// replace it. No paste-into-the-form textarea. Generic, driven only by the field
// being a *_FILE, no service-specific code.
function FileFieldEditor({ service, entry, toast, onApplied, shadowedBy }) {
  const path = entry.file_path || entry.value || entry.default || "";
  const fname = path.split("/").pop() || "document";
  const [content, setContent] = useState(null); // null while loading
  const [ephemeral, setEphemeral] = useState(false);
  const [open, setOpen] = useState(false);
  const [mapOpen, setMapOpen] = useState(false);
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
  // A document is a mapping schema when it has a location mapping or a fetch path.
  const hasMapping = (() => {
    if (!hasDoc) return false;
    try { const o = JSON.parse(content); return !!o && typeof o === "object" && (!!o.mapping || !!o.path); }
    catch { return false; }
  })();
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
    try { await activate(); setShadowCleared(true); toast.success(`${entry.name} saved and active, rolling out ${service}`); }
    catch (e) { toast.success(`${entry.name} saved`); toast.error(`Could not activate the file: ${e.message}`); }
    onApplied?.(); // refresh the page so the rollout shows in the status list
  };
  // One-click resolve of the shadow: point the env at the file + unset the inline twin.
  const useThisFile = async () => {
    setClearing(true);
    try {
      await activate();
      setShadowCleared(true);
      toast.success(`Using ${fname}, rolling out ${service}`);
      onApplied?.();
    } catch (e) { toast.error(`Could not activate ${fname}: ${e.message}`); }
    finally { setClearing(false); }
  };
  const showShadow = shadowedBy && !shadowCleared;
  return (
    <>
      <FieldRow
        name={entry.name}
        badges={<span className="font-mono text-[10px] text-slate-500">document</span>}
        desc={<FieldHelp text={entry.description} />}
        alert={
          <>
            {ephemeral && (
              <p className="text-[10px] text-amber-300">⟳ Loaded at runtime but not persisted (lost on restart). Open it and save to store it declaratively.</p>
            )}
            {showShadow && (
              <div className="flex flex-wrap items-center gap-2 rounded border border-amber-900/50 bg-amber-950/30 px-2 py-1.5">
                <p className="text-[10px] text-amber-300">⚠ {shadowedBy} is set inline and overrides this document, editing the file has no effect until {shadowedBy} is cleared.</p>
                <button type="button" disabled={clearing} onClick={useThisFile} className={`${btn.amber} text-[10px]`}>
                  {clearing ? "clearing…" : `Clear ${shadowedBy} & use this file`}
                </button>
              </div>
            )}
          </>
        }
      >
        {content === null ? (
          <span className="text-[11px] text-slate-500">loading…</span>
        ) : hasMapping ? (
          // The raw document stays reachable as a de-emphasized secondary link.
          <>
            <button type="button" onClick={() => setMapOpen(true)} className={btn.sky}>◎ Configure mapping</button>
            <button type="button" onClick={() => setOpen(true)} className="text-[10px] text-slate-500 hover:text-slate-300">
              <span className="font-mono">{fname}</span> · {new Blob([content]).size} B · <span className="underline underline-offset-2">raw</span>
            </button>
          </>
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
      </FieldRow>
      {open && (
        <FileDocModal service={service} entry={entry} path={path} initial={content}
          onClose={() => setOpen(false)} onSaved={onSaved} />
      )}
      {mapOpen && (
        <MappingStudio service={service} path={path} initial={content}
          onClose={() => setMapOpen(false)} onSaved={onSaved} />
      )}
    </>
  );
}

// One field of the guided setup. Non-sensitive shows the current value (editable);
// sensitive shows a password input with a "set" hint and never the value.
// ADAPTER_CAPABILITIES in plain words. It is what the engine and the CAMARA API
// are told about the source behind a generic adapter image. Opens pre-filled from
// what is already known live (the adapter's current advertisement in the engine
// registry, and the vendor/frame/height the mounted schema already states), so the
// operator confirms rather than types. Coordinates and height come from the schema
// and are not asked twice. accuracy_class is offered from the gateway's published
// vocabulary; the radius input appears only where the class needs it (coarse) or on
// request. Any other key already in the value is preserved untouched.
const BOUND_SOURCE_KEYS = ["source", "kinds", "frame", "z", "accuracy_class", "nominalAccuracy"];
function CapabilitiesField({ entry, value, onChange, hints }) {
  const raw = value !== undefined ? value : (entry.value ?? "");
  let caps = {};
  let broken = false;
  if (String(raw || "").trim()) {
    try { const j = JSON.parse(raw); if (j && typeof j === "object" && !Array.isArray(j)) caps = j; else broken = true; }
    catch { broken = true; }
  }
  const schema = hints?.schema || null;
  const advertised = hints?.advertised || null;
  const [vocab, setVocab] = useState(undefined); // undefined loading, null unavailable
  const [prefilled, setPrefilled] = useState(false);
  const [radiusOpen, setRadiusOpen] = useState(false);
  useEffect(() => {
    let alive = true;
    getNorthboundAccuracyClassVocabulary()
      .then((v) => { if (alive) setVocab(v && v.classes ? v : null); })
      .catch(() => { if (alive) setVocab(null); });
    return () => { alive = false; };
  }, []);
  const emit = (next) => {
    const clean = { ...next };
    for (const k of Object.keys(clean)) {
      const v = clean[k];
      if (v === undefined || v === null || v === "" || (Array.isArray(v) && !v.length)) delete clean[k];
    }
    onChange(entry.name, Object.keys(clean).length ? JSON.stringify(clean) : "");
  };
  const set = (patch) => emit({ ...caps, ...patch });
  // Pre-fill once, only when nothing is stored yet: registry advertisement first,
  // then the schema's own statements win (it is the document the operator wrote).
  useEffect(() => {
    if (prefilled || entry.set || value !== undefined) return;
    const seed = {};
    for (const k of BOUND_SOURCE_KEYS) if (advertised && advertised[k] !== undefined) seed[k] = advertised[k];
    if (schema?.vendor) seed.source = schema.vendor;
    if (schema?.frame) seed.frame = schema.frame;
    if (schema && typeof schema.z === "boolean") seed.z = schema.z;
    setPrefilled(true);
    if (Object.keys(seed).length) emit(seed);
  }, [advertised, schema, entry.set, value, prefilled]); // eslint-disable-line react-hooks/exhaustive-deps

  const classes = vocab?.classes || {};
  const cls = caps.accuracy_class || "";
  const band = classes[cls];
  const openEnded = !!band && band.upperBound == null;
  const assumed = caps.nominalAccuracy > 0 ? caps.nominalAccuracy : (band && band.upperBound != null ? band.upperBound : null);
  const showRadius = openEnded || radiusOpen || caps.nominalAccuracy > 0;
  const others = Object.keys(caps).filter((k) => !BOUND_SOURCE_KEYS.includes(k));
  const label = "text-[11.5px] font-medium text-slate-200";
  const help = "text-[10.5px] leading-snug text-slate-500";
  const row = "grid grid-cols-1 gap-1 sm:grid-cols-[minmax(0,1fr)_14rem] sm:items-start sm:gap-4";
  const fromSchema = <span className="text-[9.5px] text-slate-500">from the schema</span>;
  return (
    <div className="flex flex-col gap-3">
      <div>
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="font-mono text-[12px] font-semibold text-slate-100">{entry.name}</span>
          {entry.declared_by === "kelt" && (
            <span className="text-[9.5px] text-slate-500" title="The adapter's own /contract does not declare this variable; KELT offers it because the registry model reads it.">declared by KELT</span>
          )}
        </div>
        <p className={help}>What the engine and the CAMARA API are told about the source behind this adapter. The image is generic, so this is where the vendor's traits live; without a source name the engine drops its fixes.</p>
        {!entry.set && (advertised || schema) && (
          <p className="mt-1 text-[10.5px] text-sky-300">Filled in from what the adapter advertises today and from its schema. Check it, then apply.</p>
        )}
      </div>
      {broken && <p className="text-[11px] text-amber-300">The stored value is not a JSON object; saving from here replaces it.</p>}
      <div className="flex flex-col gap-3 rounded border border-slate-800 bg-slate-950/60 p-3">
        <div className={row}>
          <div>
            <div className={label}>Source name</div>
            <p className={help}>The tag every fix carries. An asset is bound to this source by that name (<span className="font-mono">capabilities[].source</span>); a name no adapter serves is refused when saving assets.</p>
          </div>
          <input className={inputCls} value={caps.source || ""} placeholder={schema?.vendor || "vendor name"} onChange={(e) => set({ source: e.target.value.trim() })} />
        </div>
        <div className={row}>
          <div>
            <div className={label}>Asset kinds it can locate</div>
            <p className={help}>Comma separated. An asset of a kind no source declares is refused when saving assets.</p>
          </div>
          <input
            className={inputCls}
            key={(caps.kinds || []).join("|")}
            defaultValue={(caps.kinds || []).join(", ")}
            placeholder="asset, uwb-tag"
            onBlur={(e) => set({ kinds: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) })}
          />
        </div>
        <div className={row}>
          <div>
            <div className={label}>Coordinates</div>
            <p className={help}>The frame the vendor reports in. The engine inverts latitude/longitude to the floor plan.</p>
          </div>
          {schema?.frame ? (
            <div className="flex flex-col items-end gap-0.5"><span className="font-mono text-[12px] text-slate-200">{caps.frame || schema.frame}</span>{fromSchema}</div>
          ) : (
            <select className={inputCls} value={caps.frame || ""} onChange={(e) => set({ frame: e.target.value })}>
              <option value="">(not declared)</option>
              <option value="local">floor-plan metres (local)</option>
              <option value="wgs84">latitude / longitude (wgs84)</option>
            </select>
          )}
        </div>
        <div className={row}>
          <div>
            <div className={label}>Height</div>
            <p className={help}>Whether fixes carry a vertical component.</p>
          </div>
          {schema && typeof schema.z === "boolean" ? (
            <div className="flex flex-col items-end gap-0.5"><span className="text-[12px] text-slate-200">{(caps.z ?? schema.z) ? "reported" : "not reported"}</span>{fromSchema}</div>
          ) : (
            <div className="inline-flex justify-self-end overflow-hidden rounded border border-slate-700">
              {[false, true].map((on) => (
                <button key={String(on)} type="button" onClick={() => set({ z: on })}
                  className={`px-3 py-1 text-[11px] transition-colors ${(caps.z === true) === on ? "bg-sky-600 text-white" : "bg-slate-950 text-slate-400 hover:text-slate-200"}`}>
                  {on ? "Reported" : "Not reported"}
                </button>
              ))}
            </div>
          )}
        </div>
        <div className={row}>
          <div>
            <div className={label}>Nominal precision</div>
            <p className={help}>How precise this technology is, as a band. A fix that reports no radius of its own is given the worst of the band{assumed != null ? `: ${assumed} m here` : ""}.</p>
          </div>
          <div className="flex flex-col gap-1">
            {vocab === null ? (
              <input className={inputCls} value={cls} placeholder="sub-metre | metre | coarse" onChange={(e) => set({ accuracy_class: e.target.value.trim() })} />
            ) : (
              <select className={inputCls} value={cls} onChange={(e) => set({ accuracy_class: e.target.value })}>
                <option value="">(not declared)</option>
                {Object.entries(classes).map(([k, b]) => (
                  <option key={k} value={k}>{k}{b.upperBound != null ? ` (${b.lowerBound ?? 0} to ${b.upperBound} m)` : ` (over ${b.lowerBound} m)`}</option>
                ))}
                {cls && !classes[cls] && <option value={cls}>{cls} (not in vocabulary)</option>}
              </select>
            )}
            {band?.description && <span className="text-right text-[10px] text-slate-500">{band.description}</span>}
            {vocab === null && <span className="text-right text-[10px] text-slate-600">bands not published by this gateway</span>}
            {!showRadius && cls && (
              <button type="button" onClick={() => setRadiusOpen(true)} className="self-end text-[10px] text-sky-400 hover:text-sky-300">assume a different radius…</button>
            )}
          </div>
        </div>
        {showRadius && (
          <div className={row}>
            <div>
              <div className={label}>Radius to assume (m)</div>
              <p className={help}>{openEnded ? `${cls} has no upper bound, so this is required or the engine drops every fix from this source.` : "Overrides the band's worst case for fixes that carry no radius."}</p>
            </div>
            <input className={inputCls} type="number" min="0" step="0.1" value={caps.nominalAccuracy ?? ""}
              placeholder={openEnded ? "required" : (band ? String(band.upperBound) : "")}
              onChange={(e) => set({ nominalAccuracy: e.target.value === "" ? undefined : Number(e.target.value) })} />
          </div>
        )}
      </div>
      {openEnded && !(caps.nominalAccuracy > 0) && <p className="text-[11px] text-amber-300">Enter the radius to assume: {cls} alone resolves to no value.</p>}
      {others.length > 0 && (
        <p className="text-[10px] text-slate-600">also declared, kept as is: <span className="font-mono">{others.map((k) => `${k}=${JSON.stringify(caps[k])}`).join("  ")}</span></p>
      )}
    </div>
  );
}

function ConfigField({ entry, required, value, onChange, upstreams, declared, service, toast, onApplied, shadowedBy, hints }) {
  // A file field (file_state set by the backend for path-valued *_FILE/*_PATH)
  // the dashboard owns is a document editor. When "external" (a PVC the service
  // writes itself, e.g. wifi-adapter's bindings/calibration), hands off: plain field.
  if (isOwnedDocument(entry) && service) {
    return <FileFieldEditor service={service} entry={entry} toast={toast} onApplied={onApplied} shadowedBy={shadowedBy} />;
  }
  if (entry.name === "ADAPTER_CAPABILITIES") {
    return <CapabilitiesField entry={entry} value={value} onChange={onChange} hints={hints} />;
  }
  const placeholder = entry.sensitive
    ? (entry.set ? "•••• set, leave blank to keep" : (entry.example || ""))
    : (entry.value ?? entry.default ?? entry.example ?? "");
  const shown = value !== undefined ? value : (entry.sensitive ? "" : (entry.value ?? ""));
  // Offer the service picker only where the field DEMONSTRABLY points at one of
  // the services it can offer: its effective value names one of them, or its
  // contract default names one that is deployed. Matching on the name ending in
  // _URL was wrong (it offered camara-gateway as the vendor's own cloud base URL),
  // and "any cluster FQDN" was wrong too (Keycloak is in-cluster but not on the
  // list, so the select hid the real value behind "use default").
  const hostOf = (u) => String(u || "").replace(/^https?:\/\//, "").split("/")[0].split(":")[0].split(".")[0];
  const effectiveHost = hostOf((value !== undefined ? value : (entry.value ?? "")) || "");
  const defaultHost = hostOf(entry.default);
  const pointsAtOffered = (upstreams || []).some((u) =>
    (!!effectiveHost && u.name === effectiveHost) ||
    (!effectiveHost && !!defaultHost && (u.name === defaultHost || imgBasename(u.image) === defaultHost)));
  const isServiceUrl = (declared || pointsAtOffered) && !entry.sensitive;
  // The control follows the type the contract declares (string | url | integer |
  // number | boolean | path, default string). Where a service is old enough not to
  // declare one at all, fall back to reading a flag off its default: that guess
  // applies only where the contract is silent, and it retires as services adopt the
  // field. The wire value stays a ConfigMap string, so the literal spelling ("1" vs
  // "true") comes from the declared default.
  const ftype = entry.type ? String(entry.type).toLowerCase() : null;
  const looksFlag = ["0", "1", "true", "false"].includes(String(entry.default ?? "").toLowerCase());
  const isFlag = !entry.sensitive && (ftype ? ftype === "boolean" : looksFlag);
  const flagPair = ["0", "1"].includes(String(entry.default ?? "").toLowerCase()) ? ["0", "1"] : ["false", "true"];
  const flagOn = ["1", "true"].includes(String(shown || entry.default || "").toLowerCase());
  const isNumeric = ftype === "integer" || ftype === "number";
  const [custom, setCustom] = useState(false);
  // An already-set secret shows as a fact ("set in Secret"), not an empty password
  // box begging to be filled in again. "replace" swaps in the input on demand.
  const [replacing, setReplacing] = useState(false);
  const secretSet = entry.sensitive && entry.set && !replacing;
  const useTextInput = !isServiceUrl || custom;
  const showDefaultNote = !entry.sensitive && !isFlag && !isServiceUrl && entry.default && !entry.set && !String(shown || "").trim();

  const badges = (
    <>
      {required && <span className="text-[9px] font-bold uppercase tracking-wide text-rose-400">required</span>}
      {entry.sensitive && <span className="text-[9.5px] text-amber-400">🔒 secret</span>}
    </>
  );

  // Prefer the contract's declared_at over the README-length description: it is
  // shorter and it is the thing the operator needs. Full text stays on hover.
  const used = usedFor(entry.declared_at);
  const desc = used.length ? (
    <p
      className="mt-0.5 text-[11px] leading-snug text-slate-500"
      title={[entry.description, (entry.declared_at || []).join("\n")].filter(Boolean).join("\n\n")}
    >
      used for {used.join(", ")}
    </p>
  ) : (
    <FieldHelp text={entry.description} />
  );

  return (
    <FieldRow name={entry.name} badges={badges} desc={desc}>
      {secretSet ? (
        <div className="flex w-full items-center justify-end gap-2">
          <span className="rounded border border-emerald-800/60 bg-emerald-950/40 px-2 py-0.5 text-[10px] text-emerald-400">✓ set</span>
          <button type="button" onClick={() => setReplacing(true)} className="text-[11px] text-sky-400 hover:text-sky-300">replace</button>
        </div>
      ) : isFlag ? (
        <div className="inline-flex overflow-hidden rounded border border-slate-700">
          {[false, true].map((on) => (
            <button
              key={String(on)}
              type="button"
              onClick={() => onChange(entry.name, on ? flagPair[1] : flagPair[0])}
              className={`px-3 py-1 text-[11px] transition-colors ${flagOn === on ? "bg-sky-600 text-white" : "bg-slate-950 text-slate-400 hover:text-slate-200"}`}
            >
              {on ? "On" : "Off"}
            </button>
          ))}
        </div>
      ) : useTextInput ? (
        <input
          className={`${inputCls} w-full`}
          type={entry.sensitive ? "password" : isNumeric ? "number" : "text"}
          placeholder={placeholder}
          value={shown}
          autoFocus={replacing}
          onChange={(e) => onChange(entry.name, e.target.value)}
        />
      ) : (
        <select
          className={`${inputCls} w-full`}
          value={shown}
          onChange={(e) => { if (e.target.value === "__custom__") { setCustom(true); } else { onChange(entry.name, e.target.value); } }}
        >
          <option value="">(use default: {entry.default || "unset"})</option>
          {upstreams.map((u) => (
            <option key={u.url} value={u.url}>{u.name} ({u.url})</option>
          ))}
          <option value="__custom__">Custom URL…</option>
        </select>
      )}
      {replacing && <span className="text-[9.5px] text-slate-600">replaces the stored Secret on apply</span>}
      {isServiceUrl && custom && (
        <button type="button" onClick={() => setCustom(false)} className="text-[9.5px] text-slate-500 hover:text-slate-300">← pick a deployed service</button>
      )}
      {showDefaultNote && <span className="text-[9.5px] text-slate-600">default <b className="font-mono font-normal text-slate-400">{entry.default}</b></span>}
    </FieldRow>
  );
}

// Guided, contract-driven setup for one service. Reads /config (schema + current
// state), renders required -> recommended -> optional in order, applies via the
// single-mechanism backend (Secret vs ConfigMap by `sensitive`), then rolls out.
const BINDING_LABELS = {
  motion_model: ["Motion model", "How the filter predicts between scans. A random walk only widens the uncertainty (no velocity to run away with); constant velocity extrapolates along the estimated motion."],
  algorithm: ["Position algorithm", "How a position is computed from the scan."],
};

// One runtime choice of an adapter: a select over the set its image declares,
// applied through the adapter's own PUT /bindings. Takes effect on the next
// scan, nothing restarts; picking the previous value is the rollback.
function BindingSelect({ service, name, options, active, toast, onDone }) {
  const [value, setValue] = useState(active || "");
  const [busy, setBusy] = useState(false);
  useEffect(() => { setValue(active || ""); }, [active]);
  const [label, help] = BINDING_LABELS[name] || [name.replace(/_/g, " "), null];
  const dirty = value && value !== active;
  const apply = async () => {
    setBusy(true);
    try {
      await setNorthboundServiceBinding(service, name, value);
      toast.success(`${label}: ${value}, effective on the next scan`);
      onDone?.(value);
    } catch (e) { toast.error(e.message || String(e)); }
    finally { setBusy(false); }
  };
  return (
    <div className="mb-3 flex items-start justify-between gap-4">
      <div className="min-w-0">
        <div className="text-[12px] text-slate-200">{label}</div>
        {help && <div className="text-[11px] leading-snug text-slate-500">{help}</div>}
        <div className="text-[11px] text-slate-500">Active now: <span className="font-mono text-slate-300">{active || "unknown"}</span>. Changes apply on the next scan, no restart.</div>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <select value={value} onChange={(e) => setValue(e.target.value)} disabled={busy}
          className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-[12px] text-slate-200">
          {options.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
        <button type="button" onClick={apply} disabled={!dirty || busy}
          className="rounded bg-sky-700 px-2 py-1 text-[12px] text-white disabled:opacity-40">{busy ? "Applying…" : "Apply"}</button>
      </div>
    </div>
  );
}

function ConfigureService({ service, services, bindings, toast, onClose, onApplied }) {
  const [cfg, setCfg] = useState(null);
  const [err, setErr] = useState(null);
  const [vals, setVals] = useState({});
  const [busy, setBusy] = useState(false);

  // Deployed services as pickable upstreams for a URL field (so the operator
  // picks the service instead of typing the FQDN). Exclude the service itself.
  // A field the backend declares as an adapter binding (with a kind) offers only
  // the deployed adapters of that kind: a wifi-adapter slot must not list the
  // engine or an auth proxy as if they could fill it.
  const allUpstreams = (services || [])
    .filter((s) => s.name !== service)
    .map((s) => ({ name: s.name, image: s.image, url: `http://${s.name}.${s.namespace}.svc.cluster.local:8080` }));
  const bindingFor = (name) => (bindings || []).find((b) => b.field === name);
  const upstreamsFor = (name) => {
    const b = bindingFor(name);
    return b ? b.candidates.map((c) => ({ name: c.name, url: c.url })) : allUpstreams;
  };
  // The service's own role/subtitle/namespace, already known from the inventory the
  // Services panel renders from. Real data, not a description written for the modal.
  const svcMeta = (services || []).find((s) => s.name === service);

  useEffect(() => {
    let alive = true;
    setCfg(null); setErr(null); setVals({});
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
        // default host, e.g. "vendor-adapter") matches exactly one deployed service
        // by image, pre-select that service. Ambiguous (>1) is left to the picker.
        for (const f of [...(env.recommended || []), ...(env.optional || [])]) {
          if (f.set || f.sensitive || !isOperatorSetting(f) || !/_URL$/.test(f.name)) continue;
          const host = String(f.default || "").replace(/^https?:\/\//, "").split("/")[0].split(":")[0];
          if (!host) continue;
          const match = (services || []).filter((s) => imgBasename(s.image) === host);
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
  // Only the operator's own settings (see isOperatorSetting), grouped by what each
  // field IS using signals the contract declares (`sensitive`, `required`, a
  // *_FILE document, `role`). Nothing is inferred from a variable's name.
  const settings = all.filter(isOperatorSetting);
  // Read alongside the settings but not edited here: deployment wiring (all.yml)
  // and storage paths. Folded away so the form stays about what the operator sets.
  const wiring = all.filter((f) => !isStorageField(f) && (f.owner === "deployment" || f.managed));
  const storage = all.filter(isStorageField);
  const isFile = (f) => isOwnedDocument(f);
  // Connection: the values the vendor issued for its API. They stay in their own
  // group once set (rather than falling into the optional pile) so the operator can
  // find and replace a rotated key.
  const isConn = (f) => !isFile(f) && (f._req || f.sensitive);
  const fileFields = settings.filter(isFile);
  const connFields = settings.filter(isConn);
  const optFields = settings.filter((f) => !isFile(f) && !isConn(f));
  // The documents section is "Field mapping" only when it holds the vendor schema
  // (contract role), otherwise it is just the documents this service reads.
  const docsTitle = fileFields.some((f) => f.role === "schema") ? "Field mapping" : "Documents";
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
  // Only a REQUIRED scalar that the deployment has not set and the operator has not
  // typed blocks Apply; optional secrets and *_FILE documents (saved via their own
  // editor button) do not.
  const unfilled = connFields.filter((f) => f._req && !f.set && !((vals[f.name] ?? "").toString().trim()));
  // Mapping targets the running adapter supports but the loaded schema leaves out
  // (position fields and discover fields are reported separately).
  const unmapped = [...(cfg?.mapping?.unmapped || []), ...(cfg?.discover_mapping?.unmapped || [])];

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

  // Header line: the service's own role/subtitle/namespace (real inventory data),
  // in place of a paragraph of boilerplate repeated for every service.
  const roleLine = svcMeta ? [svcMeta.role, svcMeta.subtitle, svcMeta.namespace].filter(Boolean).join(" · ") : null;

  return (
    <Modal title={`Configure ${service}`} hint={roleLine} onClose={onClose}>
      {!cfg && !err && <p className="text-xs text-slate-500">Loading contract…</p>}
      {err && (
        <Banner msg={{ text: `This service cannot be configured through the guided setup: ${err}.` }} />
      )}
      {cfg?.available && (
        <div className="flex flex-col text-xs">
          {/* An adapter that holds no schema declares it (`configured: false`), so
              there is nothing to guess: it has no vendor settings yet because the
              document that would define them is missing. */}
          {cfg.configured === false && (
            <div className="mb-4 rounded border border-amber-900/50 bg-amber-950/30 px-3 py-2.5 text-[11px] leading-snug text-amber-300">
              No vendor schema loaded. Add one under Field mapping: it declares the vendor, its endpoint, and the settings that appear here.
            </div>
          )}

          {/* How the adapter reaches its source (vendor-adapter 0.17.1+). One
              implemented transport is a fact; more than one would be chosen in the
              schema's own `transport` field, never here. */}
          {Array.isArray(cfg.transports) && cfg.transports.length > 0 && (
            <p className="mb-4 text-[11px] leading-snug text-slate-500">
              Reaches its source over <span className="font-mono text-slate-300">{(cfg.transport || cfg.transports[0]).toUpperCase()}</span>
              {cfg.transports.length > 1
                ? <> (this image also implements {cfg.transports.filter((t) => t !== (cfg.transport || cfg.transports[0])).map((t) => t.toUpperCase()).join(", ")}; the schema's <span className="font-mono">transport</span> field chooses)</>
                : <> (the only transport this image implements)</>}.
            </p>
          )}

          {/* Runtime choices the image declares (a list plus the active value) and
              accepts on its own PUT /bindings: effective on the next scan, no
              restart. Options come from the contract; nothing is typed. */}
          {cfg.bindings && Object.keys(cfg.bindings).length > 0 && (
            <div className="mb-4">
              <ConfigSection tone="neutral" title="Runtime choices">
                {Object.entries(cfg.bindings).map(([key, b]) => (
                  <BindingSelect key={key} service={cfg.service} name={key} options={b.options} active={b.active} toast={toast}
                    onDone={(v) => setCfg((c) => ({ ...c, bindings: { ...c.bindings, [key]: { ...c.bindings[key], active: v } } }))} />
                ))}
              </ConfigSection>
            </div>
          )}

          {settings.length === 0 && (
            <p className="text-[11px] leading-snug text-slate-500">
              Nothing to set from here: everything this service reads is deployment wiring, listed below.
            </p>
          )}

          {connFields.length > 0 && (
            <ConfigSection tone="rose" title="Connection">
              {connFields.map((f) => <ConfigField key={f.name} entry={f} required={f._req} value={vals[f.name]} onChange={setVal} upstreams={upstreamsFor(f.name)} declared={!!bindingFor(f.name)} service={service} toast={toast} onApplied={onApplied} shadowedBy={fileShadowedBy(f)} hints={cfg.capabilities_hints} />)}
            </ConfigSection>
          )}

          {fileFields.length > 0 && (
            <ConfigSection tone="sky" title={docsTitle}>
              {fileFields.map((f) => <ConfigField key={f.name} entry={f} required={f._req} value={vals[f.name]} onChange={setVal} upstreams={upstreamsFor(f.name)} declared={!!bindingFor(f.name)} service={service} toast={toast} onApplied={onApplied} shadowedBy={fileShadowedBy(f)} hints={cfg.capabilities_hints} />)}
              {/* The adapter reports what it can emit vs what this schema maps, so a
                  field added by a newer adapter does not stay silently unmapped. */}
              {unmapped.length > 0 && (
                <p className="text-[10px] leading-snug text-amber-300">
                  ⚠ this adapter can emit {unmapped.join(", ")}, the loaded schema does not map {unmapped.length > 1 ? "them" : "it"}.
                </p>
              )}
            </ConfigSection>
          )}

          {optFields.length > 0 && (
            <ConfigSection tone="neutral" title="Options">
              {optFields.map((f) => <ConfigField key={f.name} entry={f} value={vals[f.name]} onChange={setVal} upstreams={upstreamsFor(f.name)} declared={!!bindingFor(f.name)} service={service} toast={toast} onApplied={onApplied} shadowedBy={fileShadowedBy(f)} hints={cfg.capabilities_hints} />)}
            </ConfigSection>
          )}

          {settings.length > 0 && (
          <div className="flex items-center gap-3 pt-4">
            <button type="button" disabled={busy || unfilled.length > 0} onClick={submit} className={btn.sky}>
              {busy ? "Applying…" : "Apply & restart"}
            </button>
            {unfilled.length > 0 && (
              <span className="text-[11px] text-rose-400">fill: {unfilled.map((f) => f.name).join(", ")}</span>
            )}
          </div>
          )}

          {(wiring.length > 0 || storage.length > 0) && (
            <details className="mt-4 border-t border-slate-800 pt-3">
              <summary className="cursor-pointer text-[11px] text-slate-500 hover:text-slate-300">
                Also read by this service, not editable here: {[wiring.length && `${wiring.length} set by the deployment`, storage.length && `${storage.length} storage path${storage.length > 1 ? "s" : ""}`].filter(Boolean).join(", ")}
              </summary>
              <div className="mt-3 flex flex-col">
                <EnvReadGroup title="Set by the deployment" tone="neutral" rows={wiring} note="From all.yml at deploy time; change it there and re-run phase 10." />
                <EnvReadGroup title="Storage" tone="neutral" rows={storage} />
              </div>
            </details>
          )}
        </div>
      )}
    </Modal>
  );
}

// Read-only companion of Configure: what the service reads and who provides it,
// with the contract's full descriptions. Deployment wiring comes from all.yml
// (re-run phase 10 to change it), storage paths map to volumes, settings are the
// operator's and are edited in Configure.
// Read-only rows for values the operator does not edit here: who provides each
// (KELT wiring, the deployment's own ConfigMap, a volume) and its current value.
const isStorageField = (f) => !!f.file_state && !isOwnedDocument(f);
function EnvReadGroup({ title, tone, rows, note }) {
  if (!rows.length) return null;
  const shownValue = (f) => {
    if (f.sensitive) return f.set ? "set" : "unset";
    if (f.set) return String(f.value ?? "");
    return f.default !== undefined && f.default !== null && f.default !== "" ? `${f.default} (default)` : "unset";
  };
  const sourceOf = (f) => {
    if (isStorageField(f)) return { external: "volume (PVC or seed)", internal: "service's own file", unset: "not mounted" }[f.file_state] || f.file_state;
    if (f.managed) return "KELT wiring";
    return f.source || "";
  };
  return (
    <ConfigSection tone={tone} title={title}>
      {note && <p className="text-[10.5px] leading-snug text-slate-500">{note}</p>}
      {rows.map((f) => (
        <div key={f.name} className="flex flex-col gap-0.5">
          <div className="flex items-start justify-between gap-3">
            <span className="font-mono text-[12px] font-semibold text-slate-100">
              {f.name}
              {f._req && <span className="ml-1.5 text-[9px] font-bold uppercase tracking-wide text-rose-400">required</span>}
              {f.sensitive && <span className="ml-1.5 text-[9.5px] font-normal text-amber-400">🔒 secret</span>}
            </span>
            <span className="max-w-[50%] truncate text-right font-mono text-[11px] text-slate-300" title={shownValue(f)}>{shownValue(f)}</span>
          </div>
          <div className="flex items-start justify-between gap-3">
            <p className="text-[11px] leading-snug text-slate-500">{f.description || ""}</p>
            {sourceOf(f) && <span className="shrink-0 text-[9.5px] text-slate-600">{sourceOf(f)}</span>}
          </div>
        </div>
      ))}
    </ConfigSection>
  );
}

function ServiceInfo({ service, services, onClose }) {
  const [cfg, setCfg] = useState(null);
  const [err, setErr] = useState(null);
  const svcMeta = (services || []).find((s) => s.name === service);
  useEffect(() => {
    let alive = true;
    getNorthboundServiceConfig(service)
      .then((c) => { if (!alive) return; setCfg(c); if (!c.available) setErr(c.error || "no contract"); })
      .catch((e) => alive && setErr(e.message));
    return () => { alive = false; };
  }, [service]);
  const env = cfg?.env || {};
  const all = [
    ...(env.required || []).map((f) => ({ ...f, _req: true })),
    ...(env.recommended || []).map((f) => ({ ...f, _req: false })),
    ...(env.optional || []).map((f) => ({ ...f, _req: false })),
  ];
  const wiring = all.filter((f) => !isStorageField(f) && (f.owner === "deployment" || f.managed));
  const storage = all.filter(isStorageField);
  const settings = all.filter((f) => !isStorageField(f) && isOperatorSetting(f));
  const roleLine = svcMeta ? [svcMeta.role, svcMeta.subtitle, svcMeta.namespace].filter(Boolean).join(" · ") : null;
  return (
    <Modal title={`About ${service}`} hint={roleLine} onClose={onClose}>
      {!cfg && !err && <p className="text-xs text-slate-500">Loading contract…</p>}
      {err && <Banner msg={{ text: `No contract to read: ${err}.` }} />}
      {cfg?.available && (
        <div className="flex flex-col text-xs">
          {cfg.description && <p className="mb-4 text-[11.5px] leading-snug text-slate-400">{cfg.description}</p>}
          <EnvReadGroup title="Settings" tone="sky" rows={settings} note="Set by an admin in Configure." />
          <EnvReadGroup title="Set by the deployment" tone="neutral" rows={wiring} note="From all.yml at deploy time; change it there and re-run phase 10." />
          <EnvReadGroup title="Storage" tone="neutral" rows={storage} />
        </div>
      )}
    </Modal>
  );
}
