import { useEffect, useState } from "react";
import { useAuth } from "../auth/AuthContext";
import { useToast } from "../context/ToastContext";
import { Panel, btn } from "../components/ui";
import { IconArrowRight, IconRefresh } from "../components/icons";
import { env } from "../runtime-env";
import { getDashboardComponents, updateDashboardComponent } from "../api";

// Docs base URL. Defaults to the public MkDocs site, but is overridable via
// VITE_DOCS_URL so a LAN-only / air-gapped deploy can point at a locally served
// copy. The dashboard never depends on it to function: the in-app "Learn" notes
// below render offline, and the external links open in a new tab (a failed tab
// does not affect the SPA).
const DOCS = env("VITE_DOCS_URL", "https://jacobbista.github.io/kelt");

const DOC_LINKS = [
  { label: "Getting started", href: `${DOCS}/getting-started/`, note: "Install, deploy, first steps" },
  { label: "Architecture", href: `${DOCS}/architecture/overview/`, note: "Layers, topology, network functions" },
  { label: "Deployment phases", href: `${DOCS}/deployment/phases/`, note: "What each phase does and how to run it" },
  { label: "Dashboard", href: `${DOCS}/dashboard/overview/`, note: "Modules, access, API reference" },
  { label: "Security / IAM", href: `${DOCS}/security/iam/`, note: "Keycloak realm, roles, tokens" },
  { label: "Troubleshooting", href: `${DOCS}/operations/troubleshooting/`, note: "Diagnostics and runbooks" },
];

// "How to move around" quick map of the dashboard sections (operational).
const NAV_HELP = [
  ["Overview / Kubernetes", "Cluster-wide status, nodes, namespaces, events."],
  ["5G Core", "The Open5GS network functions: status, logs, restart, scale, image rollout."],
  ["Topology / RAN / UE Monitor", "Network map, RAN mode, and live UE sessions."],
  ["Subscribers", "Manage 5G subscribers (IMSI, keys, slices); admin only."],
  ["Services", "Northbound positioning/CAMARA and custom workloads (this area)."],
  ["Metrics / Diagnostics", "Prometheus dashboards and connectivity checks."],
];

// Short, plain, honest explanations + a link into the docs for depth.
const LEARN = [
  {
    title: "5G core (Open5GS SBA)",
    body: "The control and user plane functions (AMF, SMF, UPF, NRF, ...) talk over the 3GPP service-based architecture. They run as pods; the UPF carries user traffic.",
    href: `${DOCS}/architecture/overview/`,
  },
  {
    title: "Per-interface overlays",
    body: "Each 5G interface (N1-N4, N6) rides its own VXLAN overlay over OVS, wired into pods by Multus. One VNI per interface keeps the planes isolated.",
    href: `${DOCS}/architecture/5g-interfaces/`,
  },
  {
    title: "Edge (KubeEdge)",
    body: "An optional edge node runs KubeEdge EdgeCore with UERANSIM or a physical gNB, extending the cluster to the network edge for MEC-style workloads.",
    href: `${DOCS}/architecture/virtualization-layers/`,
  },
  {
    title: "CAMARA Location API",
    body: "A northbound REST API that exposes device location to applications. The gateway is a lightweight adapter over Open5GS, not a 3GPP NEF/LMF (the analogy is only for the exposure role).",
    href: `${DOCS}/architecture/positioning-adapters/`,
  },
  {
    title: "Positioning (engine + adapters)",
    body: "A thin engine fuses measurements from pluggable adapters (each speaks one technology) into a unified position. Fusion uses classical estimators, no ML. Track assets, not only UEs.",
    href: `${DOCS}/architecture/positioning-adapters/`,
  },
  {
    title: "IAM (Keycloak)",
    body: "A single Keycloak realm authenticates the dashboard, the CAMARA gateway, and the demo. Two dashboard roles (admin / viewer) plus an orthogonal CAMARA role; access is granted by group.",
    href: `${DOCS}/security/iam/`,
  },
];

function StateBadge({ state }) {
  if (state === "update-available") {
    return <span className="inline-flex items-center gap-1.5 rounded-full bg-amber-900/40 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-amber-300"><span className="h-1.5 w-1.5 rounded-full bg-amber-400" /> update available</span>;
  }
  if (state === "up-to-date") {
    return <span className="rounded-full bg-emerald-900/40 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-emerald-300">up to date</span>;
  }
  if (state === "not-deployed") {
    return <span className="rounded-full bg-slate-800 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-slate-400">not deployed</span>;
  }
  return <span className="rounded-full bg-slate-800 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-slate-400" title="registry unreachable (offline)">can't check</span>;
}

export default function ManualPage() {
  const auth = useAuth();
  const isAdmin = auth.roles.includes("dashboard-admin");
  const toast = useToast();
  const [components, setComponents] = useState([]);
  const [busy, setBusy] = useState("");

  const load = () => getDashboardComponents().then(setComponents).catch(() => {});
  useEffect(() => { load(); }, []);

  const doUpdate = async (name) => {
    setBusy(name);
    try {
      await updateDashboardComponent(name);
      toast.success(`${name}: rolling out latest`);
      setTimeout(load, 1500);
    } catch (e) {
      toast.error(`${name} update failed: ${e.message}`);
    } finally {
      setBusy("");
    }
  };

  return (
    <div className="svc-fade flex flex-col gap-5 pb-8">
      <header>
        <h2 className="text-lg font-semibold text-slate-100">Manual</h2>
        <p className="text-xs text-slate-500">Find your way around the dashboard and cluster, and learn how the testbed works.</p>
      </header>

      <Panel
        title="Updates"
        hint="Dashboard components vs the latest published image. Updating re-pulls only that component (no full redeploy)."
        right={<button type="button" onClick={load} className={`inline-flex items-center gap-1 ${btn.ghost}`}><IconRefresh size={13} /> check</button>}
      >
        {components.length === 0 ? (
          <p className="text-xs text-slate-500">No component status yet.</p>
        ) : (
          <div className="flex flex-col divide-y divide-slate-800/60">
            {components.map((c) => (
              <div key={c.name} className="flex items-center gap-3 py-2 text-xs">
                <span className="min-w-[150px] font-medium text-slate-200">{c.display}</span>
                <StateBadge state={c.state} />
                <span className="flex-1" />
                {isAdmin && c.state !== "not-deployed" && (
                  <button
                    type="button"
                    disabled={busy === c.name}
                    onClick={() => doUpdate(c.name)}
                    className={btn.sky}
                  >
                    {busy === c.name ? "updating…" : (c.state === "update-available" ? "update" : "re-pull")}
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
      </Panel>

      <Panel title="Documentation" hint="The full docs site (always in sync with the repo).">
        <a
          href={DOCS}
          target="_blank"
          rel="noreferrer"
          className="mb-3 inline-flex items-center gap-1 rounded bg-sky-600/20 px-3 py-1.5 text-xs font-medium text-sky-300 transition-colors hover:bg-sky-600/30"
        >
          Open the docs site <IconArrowRight size={14} />
        </a>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-3">
          {DOC_LINKS.map((d) => (
            <a
              key={d.label}
              href={d.href}
              target="_blank"
              rel="noreferrer"
              className="group rounded-lg border border-slate-700 bg-slate-950 p-3 transition-colors hover:border-slate-600"
            >
              <div className="flex items-center justify-between text-sm font-medium text-slate-200">
                {d.label}
                <IconArrowRight size={14} className="text-slate-500 transition-transform group-hover:translate-x-0.5" />
              </div>
              <p className="mt-0.5 text-[11px] text-slate-500">{d.note}</p>
            </a>
          ))}
        </div>
        <p className="mt-3 text-[10px] text-slate-500">
          LAN-only or offline? Point <span className="font-mono">VITE_DOCS_URL</span> at a local docs mirror. The Learn notes below work without internet.
        </p>
      </Panel>

      <Panel title="Where things are" hint="A quick map of the dashboard sections.">
        <dl className="grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-2">
          {NAV_HELP.map(([k, v]) => (
            <div key={k} className="text-xs">
              <dt className="font-medium text-slate-300">{k}</dt>
              <dd className="text-slate-500">{v}</dd>
            </div>
          ))}
        </dl>
      </Panel>

      <Panel title="Learn" hint="Short notes on the concepts behind the testbed.">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {LEARN.map((c) => (
            <div key={c.title} className="rounded-lg border border-slate-700 bg-slate-950 p-3">
              <h4 className="text-sm font-semibold text-slate-200">{c.title}</h4>
              <p className="mt-1 text-[11px] leading-relaxed text-slate-400">{c.body}</p>
              <a href={c.href} target="_blank" rel="noreferrer" className="mt-2 inline-flex items-center gap-1 text-[11px] text-sky-400 hover:text-sky-300">
                read more <IconArrowRight size={12} />
              </a>
            </div>
          ))}
        </div>
      </Panel>
    </div>
  );
}
