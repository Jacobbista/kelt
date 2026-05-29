import React, { useEffect, useMemo, useState } from "react";

import Loader from "../components/Loader";
import {
  getK8sEvents,
  getK8sNamespaces,
  getK8sNodesDetailed,
  getK8sPvcs,
  getK8sServices,
  getK8sStorageClasses,
} from "../api";

const TABS = [
  { id: "namespaces", label: "Namespaces" },
  { id: "nodes", label: "Nodes" },
  { id: "storage", label: "Storage" },
  { id: "services", label: "Services" },
  { id: "events", label: "Events" },
];

const REFRESH_MS = 15000;

function age(iso) {
  if (!iso) return "-";
  const ms = Date.now() - new Date(iso).getTime();
  if (ms < 0) return "-";
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h}h`;
  const d = Math.floor(h / 24);
  return `${d}d`;
}

function useInterval(callback, delay) {
  useEffect(() => {
    callback();
    if (!delay) return undefined;
    const id = setInterval(callback, delay);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [delay]);
}

function TabBar({ tab, onTab }) {
  return (
    <div className="flex rounded-lg border border-slate-700 bg-slate-900 p-0.5">
      {TABS.map((t) => (
        <button
          key={t.id}
          type="button"
          onClick={() => onTab(t.id)}
          className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
            tab === t.id
              ? "bg-indigo-600/30 text-indigo-300"
              : "text-slate-400 hover:text-white hover:bg-slate-800"
          }`}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

function NamespaceFilter({ namespaces, value, onChange }) {
  return (
    <select
      value={value || ""}
      onChange={(e) => onChange(e.target.value || null)}
      className="rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-xs text-slate-200"
    >
      <option value="">All namespaces</option>
      {namespaces.map((ns) => (
        <option key={ns.name} value={ns.name}>{ns.name}</option>
      ))}
    </select>
  );
}

function DataTable({ columns, rows, empty = "No items" }) {
  if (!rows || rows.length === 0) {
    return (
      <div className="rounded-lg border border-slate-700 bg-slate-900 p-6 text-center text-xs text-slate-500">
        {empty}
      </div>
    );
  }
  return (
    <div className="overflow-auto rounded-lg border border-slate-700">
      <table className="w-full text-left text-sm">
        <thead className="bg-slate-900 text-xs uppercase tracking-wide text-slate-400">
          <tr>
            {columns.map((c) => (
              <th key={c.key} className="p-2 font-medium">{c.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={row._key || i} className="border-t border-slate-800 hover:bg-slate-900/50">
              {columns.map((c) => (
                <td key={c.key} className={`p-2 align-top ${c.cellClass || ""}`}>
                  {c.render ? c.render(row) : (row[c.key] ?? "-")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function StatusDot({ ok, text }) {
  return (
    <span className="inline-flex items-center gap-2">
      <span className={`h-2.5 w-2.5 rounded-full ${ok ? "bg-emerald-500" : "bg-rose-500"}`} />
      <span>{text}</span>
    </span>
  );
}

// ─── Tab panes ──────────────────────────────────────────────────────────────

function NamespacesPane({ namespaces }) {
  const columns = [
    { key: "name", label: "Name", cellClass: "font-mono" },
    {
      key: "phase",
      label: "Phase",
      render: (r) => <StatusDot ok={r.phase === "Active"} text={r.phase || "-"} />,
    },
    {
      key: "labels",
      label: "Labels",
      render: (r) => (
        <span className="text-xs text-slate-400">
          {Object.keys(r.labels || {}).length
            ? Object.entries(r.labels).map(([k, v]) => `${k}=${v}`).join(" ")
            : "-"}
        </span>
      ),
    },
    { key: "age", label: "Age", render: (r) => age(r.created) },
  ];
  const rows = (namespaces || []).map((n) => ({ ...n, _key: n.name }));
  return <DataTable columns={columns} rows={rows} empty="No namespaces" />;
}

function NodesPane({ nodes }) {
  const columns = [
    { key: "name", label: "Name", cellClass: "font-mono" },
    {
      key: "ready",
      label: "Status",
      render: (r) => <StatusDot ok={r.ready} text={r.ready ? "Ready" : "NotReady"} />,
    },
    {
      key: "roles",
      label: "Roles",
      render: (r) => (r.roles?.length ? r.roles.join(",") : "-"),
    },
    { key: "internal_ip", label: "Internal IP", cellClass: "font-mono" },
    { key: "kubelet_version", label: "Kubelet" },
    {
      key: "capacity",
      label: "Capacity",
      render: (r) => (
        <span className="text-xs text-slate-400">
          {r.capacity?.cpu || "?"} CPU · {r.capacity?.memory || "?"}
        </span>
      ),
    },
    {
      key: "taints",
      label: "Taints",
      render: (r) => (r.taints?.length
        ? r.taints.map((t) => `${t.key}=${t.value ?? ""}:${t.effect}`).join(" ")
        : "-"),
    },
    { key: "age", label: "Age", render: (r) => age(r.created) },
  ];
  const rows = (nodes || []).map((n) => ({ ...n, _key: n.name }));
  return <DataTable columns={columns} rows={rows} empty="No nodes" />;
}

function pvcStatusOk(phase) {
  return phase === "Bound";
}

function StoragePane({ pvcs, storageClasses }) {
  const pvcColumns = [
    { key: "name", label: "Name", cellClass: "font-mono" },
    { key: "namespace", label: "Namespace" },
    {
      key: "phase",
      label: "Phase",
      render: (r) => <StatusDot ok={pvcStatusOk(r.phase)} text={r.phase || "-"} />,
    },
    { key: "storage_class", label: "StorageClass" },
    {
      key: "access_modes",
      label: "Access",
      render: (r) => (r.access_modes?.length ? r.access_modes.join(",") : "-"),
    },
    {
      key: "size",
      label: "Requested / Capacity",
      render: (r) => `${r.requested || "-"} / ${r.capacity || "-"}`,
    },
    { key: "volume", label: "PV", cellClass: "font-mono text-xs" },
    { key: "age", label: "Age", render: (r) => age(r.created) },
  ];
  const scColumns = [
    {
      key: "name",
      label: "Name",
      render: (r) => (
        <span className="font-mono">
          {r.name}
          {r.is_default && (
            <span className="ml-2 rounded bg-indigo-600/30 px-1.5 py-0.5 text-[10px] text-indigo-300">
              default
            </span>
          )}
        </span>
      ),
    },
    { key: "provisioner", label: "Provisioner" },
    { key: "reclaim_policy", label: "Reclaim" },
    { key: "volume_binding_mode", label: "Binding" },
    {
      key: "allow_volume_expansion",
      label: "Expandable",
      render: (r) => (r.allow_volume_expansion ? "yes" : "no"),
    },
    { key: "age", label: "Age", render: (r) => age(r.created) },
  ];
  const pvcRows = (pvcs || []).map((p) => ({ ...p, _key: `${p.namespace}/${p.name}` }));
  const scRows = (storageClasses || []).map((s) => ({ ...s, _key: s.name }));
  return (
    <div className="space-y-6">
      <section>
        <h3 className="mb-2 text-sm font-medium uppercase tracking-wide text-slate-400">
          PersistentVolumeClaims
        </h3>
        <DataTable columns={pvcColumns} rows={pvcRows} empty="No PVCs in this scope" />
      </section>
      <section>
        <h3 className="mb-2 text-sm font-medium uppercase tracking-wide text-slate-400">
          StorageClasses
        </h3>
        <DataTable columns={scColumns} rows={scRows} empty="No StorageClasses" />
      </section>
    </div>
  );
}

function ServicesPane({ services }) {
  const columns = [
    { key: "name", label: "Name", cellClass: "font-mono" },
    { key: "namespace", label: "Namespace" },
    { key: "type", label: "Type" },
    { key: "cluster_ip", label: "Cluster IP", cellClass: "font-mono" },
    {
      key: "ports",
      label: "Ports",
      render: (r) => (
        <span className="font-mono text-xs">
          {(r.ports || []).map((p) => {
            const base = `${p.port}/${p.protocol}`;
            const np = p.node_port ? ` → ${p.node_port}` : "";
            return `${base}${np}`;
          }).join("  ") || "-"}
        </span>
      ),
    },
    {
      key: "selector",
      label: "Selector",
      render: (r) => (
        <span className="text-xs text-slate-400">
          {Object.keys(r.selector || {}).length
            ? Object.entries(r.selector).map(([k, v]) => `${k}=${v}`).join(",")
            : "-"}
        </span>
      ),
    },
    { key: "age", label: "Age", render: (r) => age(r.created) },
  ];
  const rows = (services || []).map((s) => ({ ...s, _key: `${s.namespace}/${s.name}` }));
  return <DataTable columns={columns} rows={rows} empty="No services in this scope" />;
}

function eventTypeColor(t) {
  if (t === "Warning") return "bg-amber-500";
  if (t === "Normal") return "bg-emerald-500";
  return "bg-slate-500";
}

function EventsPane({ events }) {
  if (!events || events.length === 0) {
    return (
      <div className="rounded-lg border border-slate-700 bg-slate-900 p-6 text-center text-xs text-slate-500">
        No events
      </div>
    );
  }
  return (
    <div className="overflow-auto rounded-lg border border-slate-700">
      <table className="w-full text-left text-sm">
        <thead className="bg-slate-900 text-xs uppercase tracking-wide text-slate-400">
          <tr>
            <th className="p-2 font-medium">Type</th>
            <th className="p-2 font-medium">Reason</th>
            <th className="p-2 font-medium">Object</th>
            <th className="p-2 font-medium">Message</th>
            <th className="p-2 font-medium">Count</th>
            <th className="p-2 font-medium">Age</th>
          </tr>
        </thead>
        <tbody>
          {events.map((ev, i) => (
            <tr key={i} className="border-t border-slate-800 hover:bg-slate-900/50">
              <td className="p-2">
                <span className="inline-flex items-center gap-2">
                  <span className={`h-2.5 w-2.5 rounded-full ${eventTypeColor(ev.type)}`} />
                  {ev.type || "?"}
                </span>
              </td>
              <td className="p-2 font-mono text-xs">{ev.reason || "-"}</td>
              <td className="p-2 text-xs">
                <div className="font-mono">
                  {ev.involved?.kind}/{ev.involved?.name}
                </div>
                <div className="text-slate-500">{ev.involved?.namespace || ev.namespace || ""}</div>
              </td>
              <td className="p-2 text-xs text-slate-300">{ev.message || "-"}</td>
              <td className="p-2 text-xs tabular-nums">{ev.count}</td>
              <td className="p-2 text-xs">{age(ev.last_seen)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ─── Page ───────────────────────────────────────────────────────────────────

export default function KubernetesPage() {
  const [tab, setTab] = useState("namespaces");
  const [namespace, setNamespace] = useState(null);

  const [namespaces, setNamespaces] = useState([]);
  const [nodes, setNodes] = useState([]);
  const [pvcs, setPvcs] = useState([]);
  const [storageClasses, setStorageClasses] = useState([]);
  const [services, setServices] = useState([]);
  const [events, setEvents] = useState([]);

  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(null);
  const [refreshedAt, setRefreshedAt] = useState(null);
  const [refreshFeedback, setRefreshFeedback] = useState("idle");

  const tabUsesNamespace = useMemo(
    () => ["storage", "services", "events"].includes(tab),
    [tab],
  );

  async function refresh(source = "auto") {
    setError(null);
    if (source === "manual") {
      setRefreshing(true);
      setRefreshFeedback("loading");
    }
    try {
      const loaders = [
        getK8sNamespaces().then(setNamespaces),
      ];
      if (tab === "nodes") loaders.push(getK8sNodesDetailed().then(setNodes));
      if (tab === "storage") {
        loaders.push(getK8sPvcs(namespace).then(setPvcs));
        loaders.push(getK8sStorageClasses().then(setStorageClasses));
      }
      if (tab === "services") {
        loaders.push(getK8sServices(namespace).then(setServices));
      }
      if (tab === "events") {
        loaders.push(getK8sEvents(namespace, 200).then(setEvents));
      }
      await Promise.all(loaders);
      setRefreshedAt(new Date());
      if (source === "manual") setRefreshFeedback("success");
    } catch (e) {
      setError(e.message || String(e));
      if (source === "manual") setRefreshFeedback("error");
    } finally {
      if (source === "manual") {
        setRefreshing(false);
      }
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!["success", "error"].includes(refreshFeedback)) return undefined;
    const id = setTimeout(() => setRefreshFeedback("idle"), 2500);
    return () => clearTimeout(id);
  }, [refreshFeedback]);

  useInterval(() => { refresh("auto"); }, REFRESH_MS);
  useEffect(() => { setLoading(true); refresh("auto"); /* eslint-disable-next-line */ }, [tab, namespace]);

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Kubernetes</h1>
          <p className="text-xs text-slate-500">
            Cluster inventory (namespaces, nodes, storage, services, events).
          </p>
        </div>
        <div className="flex items-center gap-2">
          {tabUsesNamespace && (
            <NamespaceFilter namespaces={namespaces} value={namespace} onChange={setNamespace} />
          )}
          <TabBar tab={tab} onTab={setTab} />
          <button
            type="button"
            onClick={() => refresh("manual")}
            disabled={refreshing}
            className={`rounded-md border px-3 py-1.5 text-xs disabled:cursor-not-allowed disabled:opacity-60 ${
              refreshFeedback === "success"
                ? "border-emerald-700 bg-emerald-900/30 text-emerald-200"
                : refreshFeedback === "error"
                  ? "border-rose-700 bg-rose-900/30 text-rose-200"
                  : "border-slate-700 bg-slate-900 text-slate-300 hover:bg-slate-800"
            }`}
          >
            {refreshing || refreshFeedback === "loading"
              ? "Refreshing..."
              : refreshFeedback === "success"
                ? "Refreshed"
                : refreshFeedback === "error"
                  ? "Refresh failed"
                  : "Refresh"}
          </button>
        </div>
      </header>

      {error && (
        <div className="rounded-lg border border-rose-700 bg-rose-900/30 p-3 text-sm text-rose-200">
          {error}
        </div>
      )}

      {loading ? (
        <Loader />
      ) : (
        <div>
          {tab === "namespaces" && <NamespacesPane namespaces={namespaces} />}
          {tab === "nodes" && <NodesPane nodes={nodes} />}
          {tab === "storage" && (
            <StoragePane pvcs={pvcs} storageClasses={storageClasses} />
          )}
          {tab === "services" && <ServicesPane services={services} />}
          {tab === "events" && <EventsPane events={events} />}
        </div>
      )}

      {refreshedAt && (
        <div className="text-right text-[10px] text-slate-500">
          updated {refreshedAt.toLocaleTimeString()} · auto-refresh every {REFRESH_MS / 1000}s
        </div>
      )}
    </div>
  );
}
