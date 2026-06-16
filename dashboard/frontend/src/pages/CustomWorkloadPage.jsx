import { useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { IconArrowLeft } from "../components/icons";
import { Panel, inputCls, btn } from "../components/ui";
import { useToast } from "../context/ToastContext";
import { deployNorthboundWorkload } from "../api";

// Namespaces a custom workload may land in (must match the backend allow-list).
const NAMESPACES = ["mec", "positioning", "camara"];

export default function CustomWorkloadPage() {
  const auth = useAuth();
  const isAdmin = auth.roles.includes("dashboard-admin");

  const [name, setName] = useState("");
  const [image, setImage] = useState("");
  const [port, setPort] = useState(8080);
  const [namespace, setNamespace] = useState("mec");
  const [pullSecret, setPullSecret] = useState("");
  const [env, setEnv] = useState([]);
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  const addEnv = () => setEnv((e) => [...e, { name: "", value: "", sensitive: false }]);
  const setEnvAt = (i, k, v) => setEnv((e) => e.map((row, j) => (j === i ? { ...row, [k]: v } : row)));
  const rmEnv = (i) => setEnv((e) => e.filter((_, j) => j !== i));

  const submit = async (e) => {
    e.preventDefault();
    if (!name || !image) return;
    setBusy(true);
    try {
      const res = await deployNorthboundWorkload({
        name: name.trim(),
        image: image.trim(),
        port: Number(port) || 8080,
        namespace,
        image_pull_secret: pullSecret.trim() || null,
        env: env.filter((r) => r.name).map((r) => ({ name: r.name.trim(), value: r.value, sensitive: !!r.sensitive })),
      });
      toast.success(`deployed ${res.name} in ${res.namespace}`);
    } catch (err) {
      toast.error(`deploy failed: ${err.message}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="svc-fade flex flex-col gap-5 pb-8">
      <header className="flex flex-col gap-2">
        <Link to="/services" className="inline-flex w-fit items-center gap-1 text-xs text-slate-400 hover:text-slate-200">
          <IconArrowLeft size={14} /> Services
        </Link>
        <div>
          <h2 className="text-lg font-semibold text-slate-100">Custom workload</h2>
          <p className="text-xs text-slate-500">
            Deploy any container image as a scheduled workload (Deployment + ClusterIP Service) in an allowed namespace.
            {isAdmin ? "" : " Read-only (dashboard-admin required to deploy)."}
          </p>
        </div>
      </header>

      <Panel title="Deploy from image" hint="Creates a Deployment + ClusterIP Service on the worker node. Secret-marked env vars go into a Secret.">
        {!isAdmin ? (
          <p className="text-xs text-slate-500">Deploying workloads requires the dashboard-admin role.</p>
        ) : (
          <form className="flex flex-col gap-2 text-xs" onSubmit={submit}>
            <div className="flex flex-wrap gap-2">
              <input className={inputCls} placeholder="name" value={name} onChange={(e) => setName(e.target.value)} />
              <input className={`${inputCls} min-w-[24rem] flex-1`} placeholder="image:tag" value={image} onChange={(e) => setImage(e.target.value)} />
              <input className={`${inputCls} w-20`} type="number" placeholder="port" value={port} onChange={(e) => setPort(e.target.value)} />
              <select className={inputCls} value={namespace} onChange={(e) => setNamespace(e.target.value)}>
                {NAMESPACES.map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
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
            <button type="submit" disabled={busy} className={`${btn.sky} self-start`}>{busy ? "deploying…" : "deploy"}</button>
          </form>
        )}
      </Panel>

      <p className="text-[11px] text-slate-500">
        For a positioning adapter (auto-registered with the engine), use the deploy form in{" "}
        <Link to="/services/northbound" className="text-sky-400 underline">Northbound</Link> instead.
      </p>
    </div>
  );
}
