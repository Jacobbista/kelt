import { useEffect, useState } from "react";
import ServiceCard from "../components/ServiceCard";
import Loader from "../components/Loader";
import { IconLocate, IconNetwork, IconCpu, IconBoxPlus } from "../components/icons";
import { getNorthboundServices, getNorthboundAdapters, getNorthboundVersions, getApps } from "../api";

// Hub for scheduled cluster services. Northbound (positioning/CAMARA) and Edge
// apps are live; NEF-style network exposure is a roadmap placeholder. Each live
// card links to its own management sub-page.
export default function ServicesPage() {
  const [nb, setNb] = useState({ services: [], adapters: [], behind: 0, loaded: false });
  const [apps, setApps] = useState({ apps: [], loaded: false, ready: false });

  useEffect(() => {
    let alive = true;
    // Versions is best-effort: a failure (or no companion services) just means no
    // update badge, never a broken hub card.
    Promise.all([getNorthboundServices(), getNorthboundAdapters(), getNorthboundVersions().catch(() => ({}))])
      .then(([svc, ad, ver]) => {
        if (alive) setNb({ services: svc.services || [], adapters: ad || [], behind: ver?.behind_count || 0, loaded: true });
      })
      .catch(() => alive && setNb((s) => ({ ...s, loaded: true })));
    getApps()
      .then((res) => alive && setApps({ apps: res.apps || [], loaded: true, ready: !!res.ready }))
      .catch(() => alive && setApps((s) => ({ ...s, loaded: true })));
    return () => { alive = false; };
  }, []);

  const ready = nb.services.filter((s) => (s.ready_replicas || 0) > 0).length;
  const nbStatus = !nb.loaded ? "off" : nb.services.length > 0 ? "on" : "off";

  if (!nb.loaded) {
    return (
      <div className="svc-fade flex flex-col gap-6 pb-8">
        <header>
          <h2 className="text-lg font-semibold text-slate-100">Services</h2>
          <p className="text-xs text-slate-500">Scheduled workloads across the cluster. Northbound exposure and edge services.</p>
        </header>
        <div className="py-16"><Loader size="sm" label="Loading services…" /></div>
      </div>
    );
  }

  return (
    <div className="svc-fade flex flex-col gap-6 pb-8">
      <header>
        <h2 className="text-lg font-semibold text-slate-100">Services</h2>
        <p className="text-xs text-slate-500">Scheduled workloads across the cluster. Northbound exposure and edge services.</p>
      </header>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
        <ServiceCard
          icon={IconLocate}
          title="Northbound"
          subtitle="Positioning engine + CAMARA Location API"
          status={nbStatus}
          to="/services/northbound"
          badge={nb.behind > 0 ? (
            <span className="rounded-full bg-amber-900/40 px-2 py-0.5 text-[10px] font-medium text-amber-300" title="container updates available (KELT-pinned)">
              ↑ {nb.behind} update{nb.behind > 1 ? "s" : ""}
            </span>
          ) : null}
          statusDots={nb.services.map((s) => (s.pods && s.pods[0] ? s.pods[0].phase : "Unknown"))}
          stats={[
            { label: "services", value: nb.loaded ? nb.services.length : "—" },
            { label: "ready", value: nb.loaded ? ready : "—" },
            { label: "adapters", value: nb.loaded ? nb.adapters.length : "—" },
          ]}
        />

        <ServiceCard
          icon={IconBoxPlus}
          title="Custom workload"
          subtitle="Deploy any container image as a scheduled workload"
          status="off"
          cta="deploy"
          to="/services/custom"
        />

        <ServiceCard
          icon={IconCpu}
          title="Edge apps"
          subtitle="Deploy your own image; reachable at its own subdomain"
          status={apps.loaded && apps.ready ? "on" : "off"}
          cta="manage"
          to="/services/apps"
          stats={[
            { label: "apps", value: apps.loaded ? apps.apps.length : "—" },
            { label: "ready", value: apps.loaded ? apps.apps.filter((a) => a.ready).length : "—" },
            { label: "exposed", value: apps.loaded ? apps.apps.filter((a) => a.exposed).length : "—" },
          ]}
        />

        <ServiceCard
          icon={IconNetwork}
          title="NEF functions"
          subtitle="Network exposure (event subscriptions, QoS)"
          status="planned"
        />
      </div>
    </div>
  );
}
