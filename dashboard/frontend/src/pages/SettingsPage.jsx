import { useEffect, useState } from "react";
import ServiceCard from "../components/ServiceCard";
import { IconShield, IconPalette, IconDisk } from "../components/icons";
import { getBranding, getStorageUsage } from "../api";

// Admin configuration hub, in the same idiom as the Services hub: a card per
// surface, each linking to its own sub-page. It replaced a tab strip, which said
// nothing about what was behind a tab and forced a click to find out. Each card
// carries the one fact that decides whether the surface needs attention (how full
// the disk is, whether branding was ever customised), so the hub is worth reading
// on its own rather than being a menu.

function fmtBytes(bytes) {
  if (bytes === null || bytes === undefined) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let n = bytes;
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i += 1; }
  return `${n < 10 && i > 0 ? n.toFixed(1) : Math.round(n)} ${units[i]}`;
}

export default function SettingsPage() {
  const [brand, setBrand] = useState(null);
  const [disk, setDisk] = useState(null);

  useEffect(() => {
    let alive = true;
    // Both probes are best-effort: a card still renders (with em dashes) when its
    // request fails, rather than blanking the hub.
    getBranding().then((b) => alive && setBrand(b || {})).catch(() => alive && setBrand({}));
    getStorageUsage().then((d) => alive && setDisk(d || {})).catch(() => alive && setDisk({}));
    return () => { alive = false; };
  }, []);

  const fs = disk?.filesystem;
  const branded = !!(brand?.org_name || brand?.org_logo || brand?.has_logo);

  return (
    <div className="svc-fade flex flex-col gap-6 pb-8">
      <header>
        <h2 className="text-lg font-semibold text-slate-100">Settings</h2>
        <p className="text-xs text-slate-500">
          Deployment configuration: who can get in, how the front door looks, and what the nodes are storing.
        </p>
      </header>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
        <ServiceCard
          icon={IconShield}
          title="Identity & Access"
          subtitle="Roles, OIDC clients, and how to add a user"
          status=""
          cta="open"
          to="/settings/iam"
        />

        <ServiceCard
          icon={IconPalette}
          title="Branding"
          subtitle="Organisation name and logo on the front-door welcome page"
          status=""
          cta="edit"
          to="/settings/branding"
          stats={[
            { label: "customised", value: brand === null ? "—" : branded ? "yes" : "no" },
          ]}
        />

        <ServiceCard
          icon={IconDisk}
          title="Storage"
          subtitle="What fills the node disk, and how to reclaim it"
          status=""
          cta="open"
          to="/settings/storage"
          badge={fs && fs.used_pct >= 85 ? (
            <span
              className="rounded-full bg-amber-900/40 px-2 py-0.5 text-[10px] font-medium text-amber-300"
              title="the node filesystem is nearly full"
            >
              {fs.used_pct}% full
            </span>
          ) : null}
          stats={[
            { label: "used", value: fs ? `${fs.used_pct}%` : "—" },
            { label: "free", value: fs ? fmtBytes(fs.free) : "—" },
          ]}
        />
      </div>
    </div>
  );
}
