import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { IconArrowLeft } from "../components/icons";
import { Panel, inputCls, btn, Field, Toggle } from "../components/ui";
import Loader from "../components/Loader";
import { useToast } from "../context/ToastContext";
import { useConfirm } from "../context/ConfirmContext";
import { env } from "../runtime-env";
import {
  getApps,
  deployApp,
  deleteApp,
  checkAppUpdates,
  setAppImage,
  getAppRegistryCredentials,
  getAppRegistryImages,
  getStarterKitZip,
  provisionAppsStream,
} from "../api";

const DOCS_URL = `${env("VITE_DOCS_URL", "https://jacobbista.github.io/kelt")}/architecture/edge-apps/`;

// Split a deployed image ref into {repo, tag, inRegistry} relative to our registry host.
function parseImage(image, host) {
  if (!image) return { repo: "", tag: "", inRegistry: false };
  let rest = image;
  let inRegistry = false;
  if (host && image.startsWith(host + "/")) { rest = image.slice(host.length + 1); inRegistry = true; }
  const i = rest.lastIndexOf(":");
  return i > 0
    ? { repo: rest.slice(0, i), tag: rest.slice(i + 1), inRegistry }
    : { repo: rest, tag: "latest", inRegistry };
}

// A pinned image reads `host/repo@sha256:<64 hex>`, which is unreadable in a row.
// Show the tag it was deployed from plus a short digest, full ref on hover: the
// tag says what was intended, the digest says what is actually running.
function shortImage(image, imageTag) {
  if (!image) return "";
  const at = image.indexOf("@sha256:");
  if (at < 0) return image;
  const short = image.slice(at + 8, at + 20);
  const label = imageTag || image.slice(0, at);
  return `${label} · ${short}`;
}

// Compact "pushed 2h ago" for registry tag timestamps; full date on hover via title.
function relTime(iso) {
  if (!iso) return "";
  const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 0 || Number.isNaN(s)) return "";
  if (s < 60) return "just now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return d < 30 ? `${d}d ago` : new Date(iso).toLocaleDateString();
}

// Edge apps platform (phase 12). Operators deploy their own container image as a
// pod; an exposed app is reachable at <name>.<base> through the front-door. Viewer
// sees the inventory read-only; deploy/delete require dashboard-admin.
export default function AppsPage() {
  const auth = useAuth();
  const isAdmin = auth.roles.includes("dashboard-admin");
  const toast = useToast();
  const confirm = useConfirm();

  const [state, setState] = useState({ apps: [], loaded: false, ready: false, registryHost: "" });
  const [registry, setRegistry] = useState({ reachable: false, images: [], loaded: false });
  const [updates, setUpdates] = useState({}); // {name: true} when registry has a newer digest
  const [regBusy, setRegBusy] = useState(false); // registry refresh in-flight (button feedback)
  const [picked, setPicked] = useState({}); // {repo: tag} selected in the deploy form dropdown
  const [pickedApp, setPickedApp] = useState({}); // {appName: tag} selected in the per-app version dropdown
  const [updatingApp, setUpdatingApp] = useState(""); // app name whose rollout is in flight
  const [name, setName] = useState("");
  const [image, setImage] = useState("");
  const [port, setPort] = useState(80);
  const [replicas, setReplicas] = useState(1);
  const [expose, setExpose] = useState(true);
  const [pullSecret, setPullSecret] = useState("");
  const [envVars, setEnvVars] = useState([]);
  const [attachMec, setAttachMec] = useState(false);
  const [mecIp, setMecIp] = useState("");
  const [udpPortsStr, setUdpPortsStr] = useState("");
  const [busy, setBusy] = useState(false);
  const [creds, setCreds] = useState({ shown: false, loading: false, data: null });
  const [prov, setProv] = useState({ busy: false, log: [] });

  const refresh = useCallback(async () => {
    try {
      const res = await getApps();
      setState({ apps: res.apps || [], loaded: true, ready: !!res.ready, registryHost: res.registry_host || "" });
    } catch {
      setState((s) => ({ ...s, loaded: true }));
    }
  }, []);

  const refreshRegistry = useCallback(async () => {
    setRegBusy(true);
    try {
      const r = await getAppRegistryImages();
      setRegistry({ reachable: !!r.reachable, images: r.images || [], loaded: true });
    } catch {
      setRegistry((s) => ({ ...s, loaded: true }));
    } finally {
      setRegBusy(false);
    }
    // Suggest updates: flag apps whose registry digest is newer than the running pod.
    try {
      const u = await checkAppUpdates();
      setUpdates(u.apps || {});
    } catch { /* best-effort */ }
  }, []);

  const applyAppImage = async (appName, image) => {
    setBusy(true);
    setUpdatingApp(appName);
    try {
      await setAppImage(appName, image);
      toast.success(`${appName} → ${image.split("/").pop()} (rolling out…)`);
      setUpdates((u) => ({ ...u, [appName]: false }));
      refresh();
      // The rollout takes a few seconds; re-check once it has likely settled.
      setTimeout(() => { refresh(); refreshRegistry(); }, 7000);
    } catch (err) {
      toast.error(`update failed: ${err.message}`);
    } finally {
      setBusy(false);
      setUpdatingApp("");
    }
  };

  useEffect(() => { refresh(); }, [refresh]);
  useEffect(() => { if (isAdmin && state.ready) refreshRegistry(); }, [isAdmin, state.ready, refreshRegistry]);
  // Poll the registry catalog so a freshly pushed image shows up without a reload.
  useEffect(() => {
    if (!(isAdmin && state.ready)) return undefined;
    const id = setInterval(refreshRegistry, 8000);
    return () => clearInterval(id);
  }, [isAdmin, state.ready, refreshRegistry]);

  // Poll while any app is still coming up, so readiness (and the clickable link)
  // tracks the rollout live; stop once everything is ready to avoid idle polling.
  useEffect(() => {
    const pending = state.loaded && state.apps.some((a) => !a.ready);
    if (!pending) return undefined;
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, [state, refresh]);

  const addEnv = () => setEnvVars((e) => [...e, { name: "", value: "", sensitive: false }]);
  const setEnvAt = (i, k, v) => setEnvVars((e) => e.map((row, j) => (j === i ? { ...row, [k]: v } : row)));
  const rmEnv = (i) => setEnvVars((e) => e.filter((_, j) => j !== i));

  const prefillFromRegistry = (repo, tag) => {
    setImage(`${state.registryHost}/${repo}:${tag}`);
    if (!name) setName((repo.split("/").pop() || "app").replace(/[^a-z0-9-]/g, "-").replace(/^-+|-+$/g, "").slice(0, 40) || "app");
  };

  const doDeploy = async (payload, okMsg) => {
    setBusy(true);
    try {
      const res = await deployApp(payload);
      toast.success(res.public_url ? `${res.name} → ${res.public_url}` : (okMsg || `deployed ${res.name}`));
      refresh();
      return true;
    } catch (err) {
      toast.error(`deploy failed: ${err.message}`);
      return false;
    } finally {
      setBusy(false);
    }
  };

  const deployDemo = () =>
    doDeploy({ name: "hello", image: "nginxdemos/hello", port: 80, replicas: 1, expose: true, env: [] });

  // Starter kit (README + .env.example + deploy.sh) for the app developer, zipped
  // by the backend prefilled with this cluster's registry host. The developer only
  // builds and pushes; the k8s deploy stays here in the UI.
  const downloadStarterKit = async () => {
    try {
      const blob = await getStarterKitZip();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = "kelt-edge-app.zip"; a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      toast.error(`download failed: ${err.message}`);
    }
  };

  const submit = async (e) => {
    e.preventDefault();
    if (!name || !image) return;
    const ok = await doDeploy({
      name: name.trim(),
      image: image.trim(),
      port: Number(port) || 80,
      replicas: Number(replicas) || 1,
      expose,
      image_pull_secret: pullSecret.trim() || null,
      env: envVars.filter((r) => r.name).map((r) => ({ name: r.name.trim(), value: r.value, sensitive: !!r.sensitive })),
      attach_mec: attachMec,
      mec_ip: attachMec && mecIp.trim() ? mecIp.trim() : null,
      udp_ports: attachMec
        ? udpPortsStr.split(",").map((s) => parseInt(s.trim(), 10)).filter((n) => Number.isInteger(n) && n > 0)
        : [],
    });
    if (ok) { setName(""); setImage(""); setEnvVars([]); setMecIp(""); setUdpPortsStr(""); }
  };

  const remove = async (app) => {
    if (!(await confirm({ title: `Delete app "${app}"?`, body: "This removes its Deployment, Service and config.", confirmLabel: "Delete", danger: true }))) return;
    try {
      await deleteApp(app);
      toast.success(`deleted ${app}`);
      refresh();
    } catch (err) {
      toast.error(`delete failed: ${err.message}`);
    }
  };

  const toggleCreds = async () => {
    if (creds.shown) { setCreds((c) => ({ ...c, shown: false })); return; }
    if (creds.data) { setCreds((c) => ({ ...c, shown: true })); return; }
    setCreds((c) => ({ ...c, loading: true }));
    try {
      const data = await getAppRegistryCredentials();
      setCreds({ shown: true, loading: false, data });
    } catch (err) {
      setCreds({ shown: false, loading: false, data: null });
      toast.error(`cannot read credentials: ${err.message}`);
    }
  };

  const doProvision = async () => {
    setProv({ busy: true, log: [] });
    try {
      await provisionAppsStream((line) => setProv((p) => ({ ...p, log: [...p.log, line].slice(-200) })));
      toast.success("platform provisioned");
      refresh();
    } catch (err) {
      toast.error(`provision failed: ${err.message}`);
    } finally {
      setProv((p) => ({ ...p, busy: false }));
    }
  };

  return (
    <div className="svc-fade flex flex-col gap-5 pb-8">
      <header className="flex flex-col gap-2">
        <Link to="/services" className="inline-flex w-fit items-center gap-1 text-xs text-slate-400 hover:text-slate-200">
          <IconArrowLeft size={14} /> Services
        </Link>
        <div>
          <h2 className="text-lg font-semibold text-slate-100">Edge apps</h2>
          <p className="text-xs text-slate-500">
            Your own application pods on the worker node. An exposed app is reachable at its own subdomain through the front-door.
            {isAdmin ? "" : " Read-only (dashboard-admin required to deploy)."}
          </p>
        </div>
      </header>

      {/* Platform must be provisioned first; only shown when it is not. */}
      {state.loaded && !state.ready && (
        <Panel title="Platform not deployed yet">
          <p className="text-xs text-slate-400">
            The edge apps feature is enabled but the <span className="font-mono">apps</span> namespace and local
            registry are not deployed yet.
            {isAdmin
              ? " Deploy it now (runs phase 12 + the front-door; restarts k3s on the worker once)."
              : " A dashboard-admin can deploy it from here."}
          </p>
          {isAdmin && (
            <button type="button" onClick={doProvision} disabled={prov.busy} className={`${btn.sky} mt-2 self-start`}>
              {prov.busy ? "deploying…" : "Deploy now"}
            </button>
          )}
          {prov.log.length > 0 && (
            <pre className="mt-2 max-h-56 overflow-auto rounded bg-slate-950 p-2 font-mono text-[10px] leading-tight text-slate-400">{prov.log.join("\n")}</pre>
          )}
        </Panel>
      )}

      {/* Primary: what is running. */}
      <Panel title="Deployed apps">
        {!state.loaded ? (
          <div className="py-8"><Loader size="sm" label="Loading apps…" /></div>
        ) : state.apps.length === 0 ? (
          <p className="text-xs text-slate-500">
            Nothing deployed yet.
            {isAdmin && state.ready && (
              <> Try a{" "}
                <button type="button" onClick={deployDemo} disabled={busy} className="text-sky-400 underline hover:text-sky-300 disabled:opacity-50">
                  demo app
                </button>{" "}
                to see the flow, or deploy your own below.
              </>
            )}
          </p>
        ) : (
          <div className="flex flex-col gap-3">
            {state.apps.map((a) => {
              const host = a.public_url ? a.public_url.replace(/^https?:\/\//, "") : null;
              // The deployed image is pinned to a digest, so repo/tag come from the
              // tag the app was deployed from (image_tag); a.image is the exact
              // identity and is shown separately, shortened.
              const { repo, tag, inRegistry } = parseImage(a.image_tag || a.image, state.registryHost);
              const reg = inRegistry ? registry.images.find((im) => im.repo === repo) : null;
              const tags = reg?.tags || [];
              const sel = pickedApp[a.name] ?? tag;
              const target = `${state.registryHost}/${repo}:${sel}`;
              // Suggest an update when a newer-dated tag exists than the running one
              // (e.g. running v2, v3 just pushed), OR the running tag was re-pushed
              // (backend digest check). ISO timestamps compare chronologically.
              const runningCreated = tags.find((t) => t.tag === tag)?.created;
              // Only suggest a newer tag when the running tag's push date is KNOWN and
              // something is strictly newer — otherwise (running tag not yet in the
              // list, or undated) stay quiet instead of false-flagging.
              const newerTag = !!runningCreated
                && tags.some((t) => t.tag !== tag && t.created && t.created > runningCreated);
              const updateAvailable = newerTag || !!updates[a.name];
              return (
                <div key={a.name} className="rounded-lg border border-slate-800 bg-slate-900/40 p-3">
                  {/* Header: status + name + state badges */}
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={`h-2 w-2 rounded-full ${a.ready ? "bg-emerald-400" : "bg-amber-400"}`} title={a.ready ? "ready" : "not ready"} />
                    <span className="font-medium text-slate-100">{a.name}</span>
                    <span className="text-[11px] text-slate-500">{a.ready_replicas}/{a.replicas} ready</span>
                    {a.mec_attached && (
                      <span className="rounded bg-teal-900/40 px-1.5 py-0.5 font-mono text-[10px] text-teal-300" title="attached to the MEC data network (n6m)">
                        n6m {a.mec_ip || "dynamic"}
                      </span>
                    )}
                    {updateAvailable && (
                      <span className="rounded bg-amber-900/40 px-1.5 py-0.5 text-[10px] font-medium text-amber-300" title="a newer version is in the registry — pick it from the version list">
                        update available
                      </span>
                    )}
                  </div>

                  {/* Meta: public URL + image ref, muted */}
                  <div className="mt-1.5 flex flex-col gap-0.5 text-[11px]">
                    {host ? (
                      a.ready ? (
                        <a href={a.public_url} target="_blank" rel="noreferrer" className="font-mono text-sky-400 hover:underline">{host}</a>
                      ) : (
                        <span className="font-mono text-slate-600" title="reachable once ready">{host} · starting…</span>
                      )
                    ) : (
                      <span className="text-slate-600">{a.exposed ? "exposed (no base domain)" : "not exposed"}</span>
                    )}
                    <span className="font-mono text-slate-500" title={a.image}>
                      {shortImage(a.image, a.image_tag)}
                    </span>
                  </div>

                  {/* Actions: version picker (date-ordered) + apply, delete on the right */}
                  {isAdmin && (
                    <div className="mt-2.5 flex flex-wrap items-center gap-2 border-t border-slate-800/70 pt-2.5">
                      {!registry.loaded ? (
                        <span className="text-[11px] text-slate-600">loading versions…</span>
                      ) : tags.length > 0 ? (
                        <>
                          <span className="text-[10px] uppercase tracking-wide text-slate-500">version</span>
                          <select
                            value={sel}
                            onChange={(e) => setPickedApp((p) => ({ ...p, [a.name]: e.target.value }))}
                            className={`${inputCls} py-0.5 font-mono text-[11px]`}
                            title="choose which version to run (newest first)"
                          >
                            {tags.map((t) => (
                              <option key={t.tag} value={t.tag}>
                                {t.tag}{t.tag === tag ? " (running)" : ""}{t.created ? ` · ${relTime(t.created)}` : ""}
                              </option>
                            ))}
                          </select>
                          <button
                            type="button"
                            disabled={busy}
                            onClick={() => applyAppImage(a.name, target)}
                            title={sel === tag ? "re-pull this tag (force a fresh pull)" : `switch this app to ${sel}`}
                            className={(sel !== tag || updateAvailable) ? btn.sky : btn.ghost}
                          >
                            {updatingApp === a.name ? "Applying…" : sel === tag ? "Re-pull" : "Switch"}
                          </button>
                        </>
                      ) : null}
                      <button type="button" onClick={() => remove(a.name)} className={`${btn.ghost} ml-auto`}>delete</button>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </Panel>

      {/* Deploy: registry images first (the easy path for your own builds), then a
          free-form field (covers public images), then push instructions collapsed. */}
      {isAdmin && state.ready && (
        <Panel title="Deploy an app">
          <div className="mb-3 flex flex-col gap-1.5">
            <div className="flex items-center gap-2">
              <p className="text-[10px] font-medium uppercase tracking-wide text-slate-500">From the local registry</p>
              <button type="button" onClick={refreshRegistry} disabled={regBusy} className={`${btn.ghost} text-[10px] disabled:opacity-50`}>{regBusy ? "refreshing…" : "refresh"}</button>
            </div>
            {!registry.loaded ? (
              <span className="text-[11px] text-slate-600">loading…</span>
            ) : !registry.reachable ? (
              <span className="text-[11px] text-slate-600">registry not reachable</span>
            ) : registry.images.length === 0 ? (
              <span className="text-[11px] text-slate-600">no images pushed yet — see "Push your own image" below</span>
            ) : (
              <div className="flex flex-col gap-1.5">
                {registry.images.map((im) => {
                  const tags = im.tags || [];
                  const sel = picked[im.repo] ?? tags[0]?.tag;
                  const selObj = tags.find((t) => t.tag === sel) || tags[0];
                  return (
                    <div key={im.repo} className="flex flex-wrap items-center gap-2 text-[11px]">
                      <span className="min-w-[8rem] font-mono text-slate-300">{im.repo}</span>
                      {tags.length === 0 ? (
                        <span className="text-slate-600">(no tags)</span>
                      ) : (
                        <>
                          <select
                            value={sel}
                            onChange={(e) => setPicked((p) => ({ ...p, [im.repo]: e.target.value }))}
                            className={`${inputCls} py-1 font-mono`}
                            title="newest first"
                          >
                            {tags.map((t) => (
                              <option key={t.tag} value={t.tag}>
                                {t.tag}{t.created ? ` · ${relTime(t.created)}` : ""}
                              </option>
                            ))}
                          </select>
                          <button type="button" onClick={() => prefillFromRegistry(im.repo, sel)} className={btn.sky}>use</button>
                          {selObj?.created && (
                            <span className="text-slate-600" title={new Date(selObj.created).toLocaleString()}>
                              pushed {relTime(selObj.created)}
                            </span>
                          )}
                        </>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          <form className="flex flex-col gap-4 text-xs" onSubmit={submit}>
            <Field label="Image" hint="A registry image from the list above, or any public image (e.g. nginxdemos/hello).">
              <input className={`${inputCls} w-full`} placeholder="registry-host/app:tag" value={image} onChange={(e) => setImage(e.target.value)} />
            </Field>

            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <Field label="Name" className="col-span-2">
                <input className={`${inputCls} w-full`} placeholder="my-app" value={name} onChange={(e) => setName(e.target.value)} />
              </Field>
              <Field label="Port">
                <input className={`${inputCls} w-full`} type="number" value={port} onChange={(e) => setPort(e.target.value)} />
              </Field>
              <Field label="Replicas">
                <input className={`${inputCls} w-full`} type="number" min="0" max="10" value={replicas} onChange={(e) => setReplicas(e.target.value)} />
              </Field>
            </div>

            <div className="flex flex-col gap-3 rounded-lg border border-slate-800 bg-slate-900/40 p-3">
              <Toggle checked={expose} onChange={setExpose} label="Expose HTTP UI" hint="reachable at <name>.<base> through the front-door" />
              <Toggle checked={attachMec} onChange={setAttachMec} label="Attach to MEC network (n6m)" hint="reachable by UEs over the 5G user plane (UPF → n6m)" />
              {attachMec && (
                <div className="ml-11 grid grid-cols-1 gap-3 border-l border-teal-900/50 pl-3 sm:grid-cols-2">
                  <Field label="Fixed n6m IP" hint="reserved band 10.208.0.200-.207; empty = dynamic">
                    <input className={`${inputCls} w-full`} placeholder="10.208.0.200" value={mecIp} onChange={(e) => setMecIp(e.target.value)} />
                  </Field>
                  <Field label="UDP ingest ports" hint="comma-separated, e.g. 5005 (RTP video from the UE)">
                    <input className={`${inputCls} w-full`} placeholder="5005" value={udpPortsStr} onChange={(e) => setUdpPortsStr(e.target.value)} />
                  </Field>
                </div>
              )}
            </div>

            <details className="text-xs">
              <summary className="cursor-pointer select-none text-slate-400 hover:text-slate-200">Advanced — env vars, image pull secret</summary>
              <div className="mt-3 flex flex-col gap-3">
                <Field label="Image pull secret" hint="name of a pre-created dockerconfigjson secret (private images only)" className="sm:w-80">
                  <input className={`${inputCls} w-full`} placeholder="optional" value={pullSecret} onChange={(e) => setPullSecret(e.target.value)} />
                </Field>
                <div className="flex flex-col gap-2">
                  <span className="text-[10px] font-medium uppercase tracking-wide text-slate-500">Environment variables</span>
                  {envVars.map((row, i) => (
                    <div key={i} className="flex flex-wrap items-center gap-2">
                      <input className={`${inputCls} w-40`} placeholder="ENV_NAME" value={row.name} onChange={(e) => setEnvAt(i, "name", e.target.value)} />
                      <input className={`${inputCls} min-w-[14rem] flex-1`} placeholder="value" value={row.value} onChange={(e) => setEnvAt(i, "value", e.target.value)} />
                      <Toggle checked={row.sensitive} onChange={(v) => setEnvAt(i, "sensitive", v)} label="secret" />
                      <button type="button" onClick={() => rmEnv(i)} className={btn.ghost}>remove</button>
                    </div>
                  ))}
                  <button type="button" onClick={addEnv} className={`${btn.ghost} self-start`}>+ add variable</button>
                </div>
              </div>
            </details>

            <button type="submit" disabled={busy} className={`${btn.sky} self-start`}>{busy ? "deploying…" : "Deploy"}</button>
          </form>

          <details className="mt-4 text-xs">
            <summary className="cursor-pointer select-none text-slate-400 hover:text-slate-200">Push your own image to the registry</summary>
            <div className="mt-2">
              {state.registryHost ? (
                <>
                  <p className="text-[11px] text-slate-500">
                    Insecure HTTP + basic-auth on the worker NodePort (never via the tunnel). Add the host to your
                    client's <span className="font-mono">insecure-registries</span>, push over LAN/Tailscale, then it
                    shows up in the list above.
                  </p>
                  <pre className="mt-2 overflow-x-auto rounded bg-slate-950 p-2 font-mono text-[11px] text-slate-300">{`docker login ${state.registryHost}
docker build -t ${state.registryHost}/myapp:dev .
docker push ${state.registryHost}/myapp:dev`}</pre>
                  <div className="mt-2 flex flex-wrap items-center gap-3 text-[11px]">
                    <button type="button" onClick={downloadStarterKit} className={btn.sky}>
                      Download starter kit (.zip)
                    </button>
                    <span className="text-slate-500">README + .env.example + deploy.sh — hand it to the app developer</span>
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px]">
                    <span className="text-slate-500">credentials:</span>
                    <button type="button" onClick={toggleCreds} disabled={creds.loading} className={btn.ghost}>
                      {creds.loading ? "…" : creds.shown ? "hide" : "show"}
                    </button>
                    {creds.shown && creds.data && (
                      <span className="font-mono text-slate-300">
                        user <span className="text-sky-300">{creds.data.username}</span> · password{" "}
                        <span className="select-all text-sky-300">{creds.data.password}</span>
                      </span>
                    )}
                  </div>
                </>
              ) : (
                <p className="text-[11px] text-slate-500">Registry host not configured.</p>
              )}
              <p className="mt-2 text-[11px] text-slate-500">
                Details and security notes:{" "}
                <a href={DOCS_URL} target="_blank" rel="noreferrer" className="text-sky-400 underline">Edge apps documentation</a>.
              </p>
            </div>
          </details>
        </Panel>
      )}
    </div>
  );
}
