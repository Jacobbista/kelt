import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { IconArrowLeft, IconRefresh, IconTrash } from "../components/icons";
import { Panel, Modal, Banner, CopyBlock, Tabs, inputCls, btn } from "../components/ui";
import { useToast } from "../context/ToastContext";

const TABS = [
  { id: "status", label: "Status" },
  { id: "adapters", label: "Adapters" },
  { id: "engine", label: "Engine" },
  { id: "build", label: "Build your own" },
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
  deleteNorthboundWorkload,
  deployNorthboundImage,
  setNorthboundFusion,
  rolloutNorthboundManaged,
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
    image: "ghcr.io/jacobbista/5g-northbound/wifi-positioning:0.6.0",
    kind: "singleton",
    adapterKind: "wifi",
    blurb: "Wi-Fi RSSI positioning source. Deploy one.",
  },
  {
    name: "rest-adapter",
    image: "ghcr.io/jacobbista/5g-northbound/rest-adapter:0.6.0",
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

// Catalog adapters (wifi-positioning, rest-adapter) are deployed at runtime, not
// by the phase, so they drift behind the current KELT release. Compare a deployed
// image to its catalog (KELT-pinned) tag; return the recommended image when behind.
// Matched by image basename, so a per-vendor rest-adapter (e.g. "wittra") still
// maps to the rest-adapter entry. Managed/phase services are not in the catalog,
// so this never flags them (their upgrade path is Ansible). Global cross-component
// version management is a separate, deferred feature (see docs/roadmap.md).
const imgBasename = (image) => (image || "").split("/").pop().split("@")[0].split(":")[0];
const imgTag = (image) => { const p = (image || "").split(":"); return p.length > 1 ? p[p.length - 1] : ""; };
function catalogUpgrade(image) {
  const cat = CATALOG.find((c) => imgBasename(c.image) === imgBasename(image));
  if (!cat) return null;
  const target = imgTag(cat.image);
  const current = imgTag(image);
  return current && target && current !== target ? { current, target, image: cat.image } : null;
}

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

// Default subdomain inferred when a service's contract does not declare one. With
// a wildcard tunnel (*.<base>) any of these route, so inferring is safe; the
// contract's `subdomain` field overrides. ONE config = the base domain, derived
// from the dashboard's own hostname (its parent domain). See external-access.md.
const SUBDOMAIN_DEFAULT = {
  "camara-gateway": "api",
  "positioning-demo": "demo",
  "placement-editor": "placement",
  "oauth2-proxy-placement": "placement",
};

// Resolve a service's public URL = <subdomain>.<base>, where base is the parent
// domain of the dashboard's own hostname and subdomain is the contract default
// (s.subdomain) or the inferred default. Null for internal services, or when the
// dashboard is reached by IP/localhost (no domain to derive a subdomain from).
function publicUrl(s) {
  if (s.kind !== "ui" && s.kind !== "api") return null;  // internal / no-contract: no public link
  const { protocol, hostname } = window.location;
  if (/^\d{1,3}(\.\d{1,3}){3}$/.test(hostname) || hostname === "localhost") return null;
  const sub = s.subdomain || SUBDOMAIN_DEFAULT[s.name];
  const base = hostname.split(".").slice(1).join(".");
  if (!sub || !base) return null;
  const host = `${sub}.${base}`;
  return { url: `${protocol}//${host}${s.kind === "api" ? "/docs" : "/"}`, label: host };
}

// NOTE: per-service reachability (kind + public origin + open link) will be
// driven by each service's /contract endpoint (kind: ui|api|internal and the
// external_origin var), resolved against the real deploy config. The earlier
// hardcoded subdomain convention was removed because it guessed origins that
// were never routed. See docs/security/external-access.md.

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
  const [confirm, setConfirm] = useState(null); // { title, body, label, runLabel, action }
  const [bindings, setBindings] = useState([]);
  const [readiness, setReadiness] = useState({}); // { service: { needs_config, missing[] } }
  const autoBound = useRef(new Set()); // (consumer:field) already auto-bound this session
  const toast = useToast();

  // A workload this console deployed (deletable), vs an externally-registered
  // URL (only unregisterable). Matched by name + the managed-by label.
  const deployedNames = new Set(
    services
      .filter((s) => (s.labels || {})["app.kubernetes.io/managed-by"] === "dashboard-northbound")
      .map((s) => s.name)
  );

  // Silent loader, used by the 5s auto-poll and after every action so the
  // button does not flicker every poll.
  // Throws on failure; the 5s auto-poll swallows it (a transient 500 must not
  // spam a toast every tick), while the manual refresh surfaces it once.
  const refresh = useCallback(async () => {
    const [svc, ad] = await Promise.all([getNorthboundServices(), getNorthboundAdapters()]);
    setServices(svc.services || []);
    setAdapters(ad || []);
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
          <button type="button" onClick={manualRefresh} disabled={refreshing} className={`inline-flex items-center gap-1 ${btn.ghost} disabled:opacity-60`}>
            <IconRefresh size={14} className={refreshing ? "animate-spin" : ""} /> {refreshing ? "refreshing…" : "refresh"}
          </button>
        </div>
      </header>

      <Tabs tabs={TABS} active={tab} onChange={setTab} />

      {tab === "status" && (
        <>
        <Panel title="Services">
          {services.length === 0 ? (
            <p className="text-xs text-slate-500">No northbound services found. Enable the feature with <span className="font-mono">testbed northbound on</span>.</p>
          ) : (
            <div className="flex flex-col divide-y divide-slate-800/60">
              {services.map((s) => {
                const phase = s.pods && s.pods[0] ? s.pods[0].phase : "Unknown";
                return (
                  <div key={`${s.namespace}/${s.name}`} className="flex items-center gap-3 py-2 text-xs">
                    <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${phaseDot(phase)}`} />
                    <span className="min-w-[150px] font-semibold text-slate-100">{s.name}{s.managed ? <span className="ml-1 text-slate-500" title="managed by Ansible">*</span> : ""}</span>
                    <span className="w-20 text-slate-500">{s.namespace}</span>
                    {s.kind && (
                      <span className={`shrink-0 rounded px-1.5 py-0.5 font-mono text-[10px] ${(KIND_BADGE[s.kind] || KIND_BADGE.internal).cls}`} title="how this surface is served">
                        {(KIND_BADGE[s.kind] || KIND_BADGE.internal).label}
                      </span>
                    )}
                    {readiness[s.name]?.needs_config && (
                      <span
                        className="shrink-0 rounded bg-rose-950/40 px-1.5 py-0.5 text-[10px] text-rose-300"
                        title={`needs config: ${(readiness[s.name].missing || []).join(", ")}`}
                      >
                        ⚠ needs config
                      </span>
                    )}
                    {(readiness[s.name]?.ephemeral || []).length > 0 && (
                      <span
                        className="shrink-0 rounded bg-amber-950/40 px-1.5 py-0.5 text-[10px] text-amber-300"
                        title={`loaded but not persisted (lost on restart): ${readiness[s.name].ephemeral.join(", ")} — persist via Configure`}
                      >
                        ⟳ ephemeral
                      </span>
                    )}
                    <span className="flex-1 truncate font-mono text-[11px] text-slate-400">{s.image || "?"}</span>
                    {(() => {
                      const up = catalogUpgrade(s.image);
                      if (!up) return null;
                      return (
                        <span className="inline-flex shrink-0 items-center gap-1">
                          <span className="rounded bg-amber-950/40 px-1.5 py-0.5 text-[10px] text-amber-300" title={`on ${up.current}; current release is ${up.target}`}>↑ {up.target}</span>
                          {isAdmin && (
                            <button
                              type="button"
                              disabled={busy}
                              className={btn.ghost}
                              onClick={() => setConfirm({
                                title: `Upgrade ${s.name} to ${up.target}?`,
                                body: `Patches the image to ${up.image} (its config is preserved). The pod restarts and the adapter re-registers with the engine.`,
                                label: "Upgrade",
                                runLabel: `upgrade ${s.name}`,
                                action: () => upgradeNorthboundAdapter(s.name, up.image),
                              })}
                            >
                              upgrade
                            </button>
                          )}
                        </span>
                      );
                    })()}
                    {(() => {
                      const ep = publicUrl(s);
                      return (
                        <span className="w-44 shrink-0 truncate text-right">
                          {ep ? (
                            <a
                              href={ep.url}
                              target="_blank"
                              rel="noreferrer"
                              title={`open ${ep.url}`}
                              className="inline-flex items-center gap-1 font-mono text-[11px] text-sky-400 hover:text-sky-300"
                            >
                              {ep.label} <span aria-hidden="true">↗</span>
                            </a>
                          ) : s.node_port ? (
                            <span className="font-mono text-[10px] text-slate-500" title="LAN NodePort">:{s.node_port}</span>
                          ) : null}
                        </span>
                      );
                    })()}
                    {(() => {
                      const starting = (s.ready_replicas || 0) < (s.replicas || 0);
                      return (
                        <span
                          className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 font-mono text-[10px] ${starting ? "bg-amber-950/40 text-amber-300" : "bg-slate-800 text-slate-300"}`}
                          title={starting ? "pods starting" : "ready"}
                        >
                          {starting && <IconRefresh size={10} className="animate-spin" />}
                          {s.ready_replicas}/{s.replicas}
                        </span>
                      );
                    })()}
                    {isAdmin && s.configurable && (
                      <button
                        type="button"
                        onClick={() => setConfiguring(s.name)}
                        className={`shrink-0 ${btn.ghost}`}
                      >
                        configure
                      </button>
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
                        className="shrink-0 text-rose-400 hover:text-rose-300 disabled:opacity-40"
                      >
                        <IconTrash size={13} />
                      </button>
                    )}
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
        </>
      )}

      {tab === "adapters" && (
        <>
          <Panel
            title="Adapter registry"
            hint="Live registry from the engine. Adapters self-register and heartbeat; the engine evicts dead ones. Deploy an adapter and it announces itself, no manual step."
            right={isAdmin && <button type="button" onClick={() => setDeployOpen(true)} className={btn.sky}>Deploy from image</button>}
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
                  <div key={a.name} className="flex flex-wrap items-center gap-2 rounded border border-slate-800 bg-slate-950 px-2.5 py-1.5 text-xs">
                    <span className="font-mono text-slate-200">{a.name}</span>
                    {a.kind && <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[9px] text-slate-400">{a.kind}</span>}
                    <span className={`rounded px-1.5 py-0.5 text-[9px] ${st.cls}`}>{st.label}</span>
                    {a.registered_via && <span className="text-[9px] text-slate-600">via {a.registered_via}</span>}
                    {typeof a.last_seen_s_ago === "number" && <span className="text-[9px] text-slate-600">seen {Math.round(a.last_seen_s_ago)}s ago</span>}
                    {a.in_cooldown && <span className="text-[9px] text-rose-400">cooldown {Math.round(a.cooldown_seconds_remaining || 0)}s</span>}
                    <span className="font-mono text-[10px] text-slate-600">{a.base_url}</span>
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
                        className="ml-auto text-rose-400 hover:text-rose-300 disabled:opacity-40"
                      >
                        <IconTrash size={13} />
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
          </Panel>
          {deployOpen && (
            <Modal
              title="Deploy adapter from image"
              hint="Creates a Deployment + Service in the positioning namespace; the adapter self-registers with the engine (no manual step)."
              wide
              onClose={() => setDeployOpen(false)}
            >
              <DeployForm busy={busy} onSubmit={async (body) => { await run(`deploy ${body.name}`, () => deployNorthboundImage(body)); setDeployOpen(false); }} />
            </Modal>
          )}
        </>
      )}

      {tab === "engine" && (isAdmin ? (
        <>
          <Panel title="Fusion config" hint="Classical estimators (no ML). Applies to positioning-engine and restarts it.">
            <FusionForm busy={busy} onSubmit={(body) => run("fusion update", () => setNorthboundFusion(body))} />
          </Panel>
          <Panel title="Managed image rollout" hint="Retargets a running deployment. The durable image lives in all.yml; re-run the phase to reconcile.">
            <ManagedForm busy={busy} onSubmit={(dep, image) => run(`rollout ${dep}`, () => rolloutNorthboundManaged(dep, image))} />
          </Panel>
        </>
      ) : (
        <Panel title="Engine"><p className="text-xs text-slate-500">Engine configuration requires the dashboard-admin role.</p></Panel>
      ))}

      {tab === "build" && (
        <Panel title="Adapter contract" hint="Build your own positioning source.">
          {!contract ? (
            <p className="text-xs text-slate-500">Loading…</p>
          ) : (
            <div className="flex flex-col gap-2 text-xs text-slate-300">
              <p>Every adapter implements <span className="font-mono text-slate-200">{contract.endpoints.join(" and ")}</span>; the engine fuses the responses.</p>
              <CopyBlock label="Measurement (GET /measurement/{id} response)" text={JSON.stringify(contract.measurement_schema, null, 2)} />
              <CopyBlock label="Python adapter skeleton" text={contract.python_skeleton} />
              <CopyBlock label="env.contract.yaml template" text={contract.env_contract_template} />
              <div className="text-[11px] text-slate-400">
                Docs:{" "}
                <a className="text-sky-400 underline" href={contract.docs.adapter_contract} target="_blank" rel="noreferrer">adapter contract</a>{" · "}
                <a className="text-sky-400 underline" href={contract.docs.rest_adapter} target="_blank" rel="noreferrer">vendor REST adapter</a>{" · "}
                <a className="text-sky-400 underline" href={contract.docs.env_contract} target="_blank" rel="noreferrer">env contract</a>
              </div>
            </div>
          )}
        </Panel>
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

// Focused viewer/editor for a file-backed document (a *_FILE the dashboard owns).
// Default Preview parses the JSON and renders its entries (no raw text); Edit is
// the textarea. Replace-from-file (with confirm), validate-on-save, then store it
// in the service's files ConfigMap and roll the pod. Rendered above the config
// modal (z-60 + capture-phase Escape so Escape closes only this one).
function FileDocModal({ service, entry, path, initial, onClose, onSaved }) {
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

  const pickFile = (f) => {
    if (!f) return;
    if ((dirty || draft.trim()) && !window.confirm(`Replace the current document with “${f.name}”?`)) return;
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
      <div className="flex max-h-[88vh] w-full max-w-3xl flex-col rounded-xl border border-slate-700 bg-slate-900 shadow-2xl">
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
          {isJson && view === "preview" ? (
            <div className="max-h-80 overflow-auto rounded border border-slate-800 bg-slate-950 px-3 py-2 font-mono text-[12px]">
              {parsed.ok
                ? <JsonView value={parsed.value} />
                : <span className="text-rose-300">{draft.trim() ? "Invalid JSON — switch to Edit to fix it." : "Empty document."}</span>}
            </div>
          ) : (
            <textarea
              className={`${inputCls} h-80 font-mono text-[12px]`}
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
  const needs = all.filter(isNeed);
  const configured = all.filter((f) => !isNeed(f));
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
  const unfilled = needs.filter((f) => f._req && !isFile(f) && !((vals[f.name] ?? "").toString().trim()));

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
        <div className="flex flex-col gap-3 text-xs">
          {cfg.description && <p className="text-slate-400">{cfg.description}</p>}
          <div>
            <p className="mb-1 text-[10px] uppercase tracking-wide text-rose-400">Needs your input</p>
            {needs.length === 0 ? (
              <p className="text-[11px] text-emerald-400/80">Fully configured by the deployment, nothing required.</p>
            ) : (
              needs.map((f) => <ConfigField key={f.name} entry={f} required={f._req} value={vals[f.name]} onChange={setVal} upstreams={upstreams} service={service} toast={toast} onApplied={onApplied} shadowedBy={fileShadowedBy(f)} />)
            )}
          </div>
          {configured.length > 0 && (
            <div>
              <button type="button" onClick={() => setShowAll((v) => !v)} className="mb-1 text-[10px] uppercase tracking-wide text-slate-500 hover:text-slate-300">
                {showAll ? "▾" : "▸"} Configured / optional ({configured.length})
              </button>
              {showAll && configured.map((f) => <ConfigField key={f.name} entry={f} value={vals[f.name]} onChange={setVal} upstreams={upstreams} service={service} toast={toast} onApplied={onApplied} shadowedBy={fileShadowedBy(f)} />)}
            </div>
          )}
          <div className="flex items-center gap-3">
            <button type="button" disabled={busy || unfilled.length > 0} onClick={submit} className={btn.sky}>
              Apply &amp; restart
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
