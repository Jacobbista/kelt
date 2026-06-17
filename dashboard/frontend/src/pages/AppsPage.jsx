import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { IconArrowLeft } from "../components/icons";
import { Panel, inputCls, btn } from "../components/ui";
import Loader from "../components/Loader";
import { useToast } from "../context/ToastContext";
import { env } from "../runtime-env";
import {
  getApps,
  deployApp,
  deleteApp,
  getAppRegistryCredentials,
  provisionAppsStream,
} from "../api";

const DOCS_URL = `${env("VITE_DOCS_URL", "https://jacobbista.github.io/kelt")}/architecture/edge-apps/`;

// Edge apps platform (phase 12). Operators deploy their own container image as a
// pod; an exposed app is reachable at <name>.<base> through the front-door. Viewer
// sees the inventory read-only; deploy/delete require dashboard-admin. The image
// must be pushed to the local registry first (see the Manual).
export default function AppsPage() {
  const auth = useAuth();
  const isAdmin = auth.roles.includes("dashboard-admin");
  const toast = useToast();

  const [state, setState] = useState({ apps: [], loaded: false, ready: false, registryHost: "" });
  const [name, setName] = useState("");
  const [image, setImage] = useState("");
  const [port, setPort] = useState(80);
  const [replicas, setReplicas] = useState(1);
  const [expose, setExpose] = useState(true);
  const [pullSecret, setPullSecret] = useState("");
  const [envVars, setEnvVars] = useState([]);
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

  useEffect(() => { refresh(); }, [refresh]);

  const addEnv = () => setEnvVars((e) => [...e, { name: "", value: "", sensitive: false }]);
  const setEnvAt = (i, k, v) => setEnvVars((e) => e.map((row, j) => (j === i ? { ...row, [k]: v } : row)));
  const rmEnv = (i) => setEnvVars((e) => e.filter((_, j) => j !== i));

  const submit = async (e) => {
    e.preventDefault();
    if (!name || !image) return;
    setBusy(true);
    try {
      const res = await deployApp({
        name: name.trim(),
        image: image.trim(),
        port: Number(port) || 80,
        replicas: Number(replicas) || 1,
        expose,
        image_pull_secret: pullSecret.trim() || null,
        env: envVars.filter((r) => r.name).map((r) => ({ name: r.name.trim(), value: r.value, sensitive: !!r.sensitive })),
      });
      toast.success(res.public_url ? `deployed ${res.name} → ${res.public_url}` : `deployed ${res.name}`);
      setName(""); setImage(""); setEnvVars([]);
      refresh();
    } catch (err) {
      toast.error(`deploy failed: ${err.message}`);
    } finally {
      setBusy(false);
    }
  };

  const remove = async (app) => {
    if (!window.confirm(`Delete app "${app}"? This removes its Deployment, Service and config.`)) return;
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

      <Panel title="Push images to the local registry" hint="Build outside the cluster, push here, then deploy below.">
        {state.registryHost ? (
          <>
            <p className="text-xs text-slate-400">
              Tag images with the registry host below (insecure HTTP + basic-auth, reachable on the worker NodePort,
              never via the tunnel). Add it to your client's <span className="font-mono">insecure-registries</span> and
              push over LAN/Tailscale.
            </p>
            <pre className="mt-2 overflow-x-auto rounded bg-slate-950 p-2 font-mono text-[11px] text-slate-300">{`docker login ${state.registryHost}
docker build -t ${state.registryHost}/myapp:dev .
docker push ${state.registryHost}/myapp:dev`}</pre>
            {isAdmin && (
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
            )}
          </>
        ) : (
          <p className="text-xs text-slate-500">Registry host not configured (deploy the platform first).</p>
        )}
        <p className="mt-2 text-[11px] text-slate-500">
          Full walkthrough and security notes in the{" "}
          <a href={DOCS_URL} target="_blank" rel="noreferrer" className="text-sky-400 underline">Edge apps documentation</a>.
        </p>
      </Panel>

      <Panel title="Deployed apps" hint="Build and push your image to the local registry, then deploy it here.">
        {!state.loaded ? (
          <div className="py-8"><Loader size="sm" label="Loading apps…" /></div>
        ) : state.apps.length === 0 ? (
          <p className="text-xs text-slate-500">No apps deployed yet.</p>
        ) : (
          <div className="flex flex-col divide-y divide-slate-800 text-xs">
            {state.apps.map((a) => (
              <div key={a.name} className="flex flex-wrap items-center gap-x-4 gap-y-1 py-2">
                <span className={`inline-block h-2 w-2 rounded-full ${a.ready ? "bg-emerald-400" : "bg-amber-400"}`} title={a.ready ? "ready" : "not ready"} />
                <span className="font-medium text-slate-100">{a.name}</span>
                <span className="font-mono text-[11px] text-slate-400">{a.image}</span>
                <span className="text-slate-500">{a.ready_replicas}/{a.replicas} ready</span>
                {a.public_url ? (
                  <a href={a.public_url} target="_blank" rel="noreferrer" className="font-mono text-[11px] text-sky-400 underline">
                    {a.public_url.replace(/^https?:\/\//, "")}
                  </a>
                ) : (
                  <span className="text-[11px] text-slate-600">{a.exposed ? "exposed (no base domain)" : "not exposed"}</span>
                )}
                {isAdmin && (
                  <button type="button" onClick={() => remove(a.name)} className={`${btn.ghost} ml-auto`}>delete</button>
                )}
              </div>
            ))}
          </div>
        )}
      </Panel>

      <Panel title="Deploy from image" hint="Creates a Deployment (+ Service when exposed) on the worker. Secret-marked env vars go into a Secret.">
        {!isAdmin ? (
          <p className="text-xs text-slate-500">Deploying apps requires the dashboard-admin role.</p>
        ) : (
          <form className="flex flex-col gap-2 text-xs" onSubmit={submit}>
            <div className="flex flex-wrap gap-2">
              <input className={inputCls} placeholder="name" value={name} onChange={(e) => setName(e.target.value)} />
              <input className={`${inputCls} min-w-[24rem] flex-1`} placeholder="registry-host/image:tag" value={image} onChange={(e) => setImage(e.target.value)} />
              <input className={`${inputCls} w-20`} type="number" placeholder="port" value={port} onChange={(e) => setPort(e.target.value)} />
              <input className={`${inputCls} w-24`} type="number" min="0" max="10" placeholder="replicas" value={replicas} onChange={(e) => setReplicas(e.target.value)} />
              <input className={inputCls} placeholder="imagePullSecret (optional)" value={pullSecret} onChange={(e) => setPullSecret(e.target.value)} />
              <label className="flex items-center gap-1 text-[11px] text-slate-300">
                <input type="checkbox" checked={expose} onChange={(e) => setExpose(e.target.checked)} /> expose at &lt;name&gt;.&lt;base&gt;
              </label>
            </div>
            <div className="flex flex-col gap-1">
              {envVars.map((row, i) => (
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
            <button type="submit" disabled={busy} className={`${btn.sky} self-start`}>{busy ? "deploying…" : "deploy"}</button>
          </form>
        )}
      </Panel>
    </div>
  );
}
