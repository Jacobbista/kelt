import { IconDashboard, IconLayers, IconHexagon, IconNetwork, IconRadio, IconSim, IconPhone, IconStethoscope, IconBars, IconGrid, IconSliders, IconBook, IconShield, IconActivity, IconRocket } from "./components/icons";

// Every page is declared here once: the sidebar, the route ids used by
// onNavigate and the header's breadcrumb all read this file.

// Grouped by domain: what the radio network does, how traffic may move, what
// the platform runs. A group whose items are all hidden for this account is not
// shown. Admin-only entries are backed by admin-only routers end to end (showing
// them to a viewer only produced 403 banners).
export const NAV_GROUPS = [
  { items: [
    { id: "overview",      label: "Overview",    icon: IconDashboard,   path: "/" },
  ] },
  { label: "5G Network", items: [
    { id: "core",          label: "Core",        icon: IconHexagon,     path: "/core" },
    { id: "ran",           label: "RAN",         icon: IconRadio,       path: "/ran",         adminOnly: true },
    { id: "subscribers",   label: "Subscribers", icon: IconSim,         path: "/subscribers", adminOnly: true },
    { id: "ue-monitoring", label: "UE Monitor",  icon: IconPhone,       path: "/ue-monitor" },
  ] },
  { label: "Network", items: [
    { id: "topology",      label: "Topology",    icon: IconNetwork,     path: "/network/topology" },
    { id: "isolation",     label: "Isolation",   icon: IconShield,      path: "/network/isolation" },
    { id: "health",        label: "Health",      icon: IconStethoscope, path: "/network/health" },
    { id: "capture",       label: "Capture",     icon: IconActivity,    path: "/network/capture",     adminOnly: true },
  ] },
  { label: "Platform", items: [
    { id: "kubernetes",    label: "Kubernetes",  icon: IconLayers,      path: "/kubernetes" },
    // Services hub (positioning/CAMARA, edge apps). Read-only for viewers.
    { id: "services",      label: "Services",    icon: IconGrid,        path: "/services" },
    { id: "metrics",       label: "Metrics",     icon: IconBars,        path: "/metrics" },
    { id: "operations",    label: "Operations",  icon: IconRocket,      path: "/operations",  adminOnly: true },
  ] },
];

// Below the groups, apart: configuration and documentation.
export const NAV_FOOTER = [
  { id: "settings",        label: "Settings",    icon: IconSliders,     path: "/settings",    adminOnly: true },
  { id: "manual",          label: "Manual",      icon: IconBook,        path: "/manual" },
];

// Pages that are not in the sidebar, with the page they belong to. Their
// parents are links in the breadcrumb; a group label is not (groups have no page).
const SUBPAGES = [
  { path: "/services/northbound",        label: "Northbound",        parent: "/services" },
  { path: "/services/northbound/assets", label: "Assets",            parent: "/services/northbound" },
  { path: "/services/custom",            label: "Custom workload",   parent: "/services" },
  { path: "/services/apps",              label: "Edge apps",         parent: "/services" },
  { path: "/settings/iam",               label: "Identity & Access", parent: "/settings" },
  { path: "/settings/branding",          label: "Branding",          parent: "/settings" },
  { path: "/settings/storage",           label: "Storage",           parent: "/settings" },
  { path: "/settings/operations",        label: "Operations record", parent: "/settings" },
  { path: "/settings/audit",             label: "Audit",             parent: "/settings" },
];

const ALL_ITEMS = [...NAV_GROUPS.flatMap((g) => g.items.map((i) => ({ ...i, group: g.label }))), ...NAV_FOOTER];

export const ROUTES = Object.fromEntries(ALL_ITEMS.map((i) => [i.id, i.path]));

const PAGES = {
  ...Object.fromEntries(ALL_ITEMS.map((i) => [i.path, { label: i.label, group: i.group }])),
  ...Object.fromEntries(SUBPAGES.map((p) => [p.path, p])),
};

// Breadcrumb for a path: [{ label, path }], the current page last with path
// null. A top-level page shows its group (text); a subpage shows its parents
// (links) instead. An unknown path climbs to its nearest known ancestor.
export function crumbsFor(pathname) {
  let path = pathname.replace(/\/+$/, "") || "/";
  while (!PAGES[path] && path !== "/") path = path.slice(0, path.lastIndexOf("/")) || "/";
  const chain = [];
  for (let p = path; p; p = PAGES[p].parent) chain.unshift({ label: PAGES[p].label, path: p });
  const group = PAGES[chain[0].path].group;
  chain[chain.length - 1] = { ...chain[chain.length - 1], path: null };
  return group && chain.length === 1 ? [{ label: group, path: null }, ...chain] : chain;
}
