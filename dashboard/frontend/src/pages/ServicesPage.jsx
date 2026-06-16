import { useEffect, useState } from "react";
import ServiceCard from "../components/ServiceCard";
import Loader from "../components/Loader";
import { IconLocate, IconNetwork, IconCpu, IconBoxPlus } from "../components/icons";
import { getNorthboundServices, getNorthboundAdapters } from "../api";

// Hub for scheduled cluster services. Northbound (positioning/CAMARA) is live
// today; NEF-style network exposure and MEC edge apps are placeholders for the
// roadmap. Each live card links to its own management sub-page.
export default function ServicesPage() {
  const [nb, setNb] = useState({ services: [], adapters: [], loaded: false });

  useEffect(() => {
    let alive = true;
    Promise.all([getNorthboundServices(), getNorthboundAdapters()])
      .then(([svc, ad]) => {
        if (alive) setNb({ services: svc.services || [], adapters: ad || [], loaded: true });
      })
      .catch(() => alive && setNb((s) => ({ ...s, loaded: true })));
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
          icon={IconNetwork}
          title="NEF functions"
          subtitle="Network exposure (event subscriptions, QoS)"
          status="planned"
        />

        <ServiceCard
          icon={IconCpu}
          title="MEC apps (edge)"
          subtitle="N6 user-plane applications on the edge node"
          status="planned"
        />
      </div>
    </div>
  );
}
