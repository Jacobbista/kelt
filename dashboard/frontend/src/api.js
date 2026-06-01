// All paths are relative — Vite dev/preview server (and nginx in prod) reverse-proxies
// `/api` and `/health` to the backend. This avoids CORS, Private Network Access
// blocking, and mixed-content issues when the dashboard is served via tunnel.
const API_BASE = "";

import { getCurrentAccessToken } from "./auth/AuthContext";

function _authHeader() {
  const token = getCurrentAccessToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function get(path) {
  const res = await fetch(`${API_BASE}${path}`, { headers: { ..._authHeader() } });
  if (!res.ok) throw new Error(`${path} failed: ${res.status}`);
  return res.json();
}

async function post(path, body) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ..._authHeader() },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${path} failed: ${res.status} ${text}`);
  }
  return res.json();
}

async function put(path, body) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", ..._authHeader() },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${path} failed: ${res.status} ${text}`);
  }
  return res.json();
}

async function del(path) {
  const res = await fetch(`${API_BASE}${path}`, { method: "DELETE", headers: { ..._authHeader() } });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${path} failed: ${res.status} ${text}`);
  }
  return res.json();
}

async function patch(path, body) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ..._authHeader() },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${path} failed: ${res.status} ${text}`);
  }
  return res.json();
}

// Health (unauthenticated liveness probe)
export const getHealth = () => get("/health");
// Runtime metadata (mode, source). Unauthenticated so the shell can render
// before the OIDC login redirect resolves.
export const getRuntimeInfo = () => get("/api/v1/cluster/info");

// Admin: restart backend via systemd
export const restartBackend = () => post("/api/v1/admin/restart-backend", {});
export const getServiceStatus = () => get("/api/v1/admin/service-status");
// Returns the watchdog shared token. JWT admin-gated; caller caches in memory
// so watchdog stays callable when backend dies.
export const getWatchdogToken = () => get("/api/v1/admin/watchdog-token").then((j) => j.token);

// Dev frontend control. The cluster pod is the baseline; the Vite dev
// frontend on the ansible VM is an opt-in extra toggled from here.
export const getDevFrontendStatus = () => get("/api/v1/dev-frontend/status");
export const enableDevFrontend = () => post("/api/v1/dev-frontend/enable", {});
export const disableDevFrontend = () => post("/api/v1/dev-frontend/disable", {});

// Cluster
export const getClusterSummary = () => get("/api/v1/cluster/summary");
export const getNfStatus = () => get("/api/v1/nf/status");
export const getPods = (ns = "5g") => get(`/api/v1/pods?namespace=${ns}`);

// Pod details
export const describePod = (pod, ns = "5g") => get(`/api/v1/pods/${pod}/describe?namespace=${ns}`);

// AMF CNI file-exists alert & repair
export const getAmfCniAlert = () => get("/api/v1/pods/amf-cni-alert");

export const scaleAmfController = (kind, name, replicas, namespace = "5g") =>
  post("/api/v1/pods/amf-controllers/scale", {
    namespace,
    kind,
    name,
    replicas,
  });

// Deployments
export const restartDeployment = (ns, dep) => post(`/api/v1/deployments/${dep}/restart`, { namespace: ns });

// NF log level (Open5GS)
export const getNfLogLevel = (deployment, ns = "5g") => get(`/api/v1/nf/${deployment}/log-level?namespace=${ns}`);
export const setNfLogLevel = (deployment, level, ns = "5g") => patch(`/api/v1/nf/${deployment}/log-level?namespace=${ns}`, { level });

// Topology & Network
export const getTopology = (ns = "5g") => get(`/api/v1/topology?namespace=${ns}`);
export const getBridgeFlows = (bridge) => get(`/api/v1/ovs/bridges/${bridge}/flows`);
export const getNads = (ns = "5g") => get(`/api/v1/network/nads?namespace=${ns}`);
export const getNetworkInterfaces = (ns = "5g") => get(`/api/v1/network/interfaces?namespace=${ns}`);

// Subscribers
export const getSubscribers = () => get("/api/v1/subscribers");
export const getSubscriber = (imsi) => get(`/api/v1/subscribers/${imsi}`);
export const createSubscriber = (data) => post("/api/v1/subscribers", data);
export const updateSubscriber = (imsi, data) => put(`/api/v1/subscribers/${imsi}`, data);
export const deleteSubscriber = (imsi) => del(`/api/v1/subscribers/${imsi}`);
export const importSubscribers = (data) => post("/api/v1/subscribers/import", data);
export const initSubscribers = () => post("/api/v1/subscribers/init", {});

// RAN
export const getRanStatus = () => get("/api/v1/ran/status");
export const enableRan = () => post("/api/v1/ran/enable", {});
export const disableRan = () => post("/api/v1/ran/disable", {});
export const getRanModesStatus = () => get("/api/v1/ran/modes/status");
export const enablePhysicalMode = () => post("/api/v1/ran/modes/physical/enable", {});

/**
 * Enable physical RAN with streaming progress. Calls onProgress for each event.
 * Resolves with the final result, rejects on error.
 */
export async function enablePhysicalModeStream(onProgress) {
  const res = await fetch(`/api/v1/ran/modes/physical/enable/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ..._authHeader() },
  });
  if (!res.ok) throw new Error(`Enable failed: ${res.status}`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result = null;
  let errMsg = null;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";
    for (const line of lines) {
      if (!line.trim()) continue;
      try {
        const ev = JSON.parse(line);
        if (ev.result) result = ev.result;
        else if (ev.error) errMsg = ev.error;
        else if (onProgress) onProgress(ev);
      } catch (_) {}
    }
  }
  if (buffer.trim()) {
    try {
      const ev = JSON.parse(buffer);
      if (ev.result) result = ev.result;
      else if (ev.error) errMsg = ev.error;
      else if (onProgress) onProgress(ev);
    } catch (_) {}
  }
  if (errMsg) throw new Error(errMsg);
  return result;
}

/**
 * Disable physical RAN with streaming progress. Calls onProgress for each event.
 */
export async function disablePhysicalModeStream(onProgress) {
  const res = await fetch(`/api/v1/ran/modes/physical/disable/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ..._authHeader() },
  });
  if (!res.ok) throw new Error(`Disable failed: ${res.status}`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result = null;
  let errMsg = null;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";
    for (const line of lines) {
      if (!line.trim()) continue;
      try {
        const ev = JSON.parse(line);
        if (ev.result) result = ev.result;
        else if (ev.error) errMsg = ev.error;
        else if (onProgress) onProgress(ev);
      } catch (_) {}
    }
  }
  if (buffer.trim()) {
    try {
      const ev = JSON.parse(buffer);
      if (ev.result) result = ev.result;
      else if (ev.error) errMsg = ev.error;
      else if (onProgress) onProgress(ev);
    } catch (_) {}
  }
  if (errMsg) throw new Error(errMsg);
  return result;
}

export const disablePhysicalMode = () => post("/api/v1/ran/modes/physical/disable", {});
export const enableUeransimMode = () => post("/api/v1/ran/modes/ueransim/enable", {});
export const disableUeransimMode = () => post("/api/v1/ran/modes/ueransim/disable", {});
export const getUeransimStatus = () => get("/api/v1/ran/ueransim/status");
export const getUeransimDefaults = () => get("/api/v1/ran/ueransim/defaults");
export const createUeransimGnb = (manifest) => post("/api/v1/ran/ueransim/gnbs", { manifest });
export const createUeransimUe = (manifest) => post("/api/v1/ran/ueransim/ues", { manifest });
export const createUeransimGnbForm = (payload) => post("/api/v1/ran/ueransim/gnbs/form", payload);
export const createUeransimUeForm = (payload) => post("/api/v1/ran/ueransim/ues/form", payload);
export const patchUeransimGnb = (name, payload) => patch(`/api/v1/ran/ueransim/gnbs/${name}`, payload);
export const patchUeransimUe = (name, payload) => patch(`/api/v1/ran/ueransim/ues/${name}`, payload);
export const activateUeransimGnb = (name) => post(`/api/v1/ran/ueransim/gnbs/${name}/activate`, {});
export const deactivateUeransimGnb = (name) => post(`/api/v1/ran/ueransim/gnbs/${name}/deactivate`, {});
export const activateUeransimUe = (name) => post(`/api/v1/ran/ueransim/ues/${name}/activate`, {});
export const deactivateUeransimUe = (name) => post(`/api/v1/ran/ueransim/ues/${name}/deactivate`, {});
export const deleteUeransimGnb = (name) => del(`/api/v1/ran/ueransim/gnbs/${name}`);
export const deleteUeransimUe = (name) => del(`/api/v1/ran/ueransim/ues/${name}`);

// Metrics
export const getNodeMetrics = () => get("/api/v1/metrics/nodes");
export const getNfMetrics = () => get("/api/v1/metrics/nf");
export const getMetricsOverview = () => get("/api/v1/metrics/overview");
export const getNodeMetricsRange = (mins = 30, step = "60s") => get(`/api/v1/metrics/range/nodes?minutes=${mins}&step=${step}`);
export const getNfMetricsRange = (mins = 30, step = "60s") => get(`/api/v1/metrics/range/nf?minutes=${mins}&step=${step}`);

// Network Health
export const getNetworkHealth = () => get("/api/v1/network/health");
export const runNetworkHealthCheck = () => post("/api/v1/network/health/run", {});
export const getN6NatDiagnostics = () => get("/api/v1/network/n6-nat");

// UE Monitoring
export const getUeSummary = (windowSeconds = 300) =>
  get(`/api/v1/ue/summary?window=${windowSeconds}`);
export const getUeEvents = (mins = 10) => get(`/api/v1/ue/events?minutes=${mins}`);
export const getActiveUes = () => get("/api/v1/ue/active");
export const getUeGnbs = () => get("/api/v1/ue/gnbs");
export const getUePods = () => get("/api/v1/ue/pods");
export const runUePing = (pod, target = "8.8.8.8") => post("/api/v1/ue/test/ping", { pod, target });
export const runUeIperf = (pod, server = "10.45.0.1", duration = 5) => post("/api/v1/ue/test/iperf", { pod, server, duration });
export const getNfRawLogs = (nf, tail = 100) => get(`/api/v1/ue/logs/${nf}?tail=${tail}`);

// UE personalizations (dashboard-only nickname/icon, persisted in Mongo)
export const getUePersonalizations = () => get("/api/v1/ue/personalizations");
export const upsertUePersonalization = (imsi, { nickname, icon } = {}) =>
  put(`/api/v1/ue/personalizations/${imsi}`, { nickname, icon });
export const deleteUePersonalization = (imsi) =>
  del(`/api/v1/ue/personalizations/${imsi}`);

// Time Sync
export const getTimeSync = () => get("/api/v1/time/sync");
export const forceTimeSync = () => post("/api/v1/time/force-sync", {});

// Sniffer
export const getSnifferPoints = () => get("/api/v1/sniffer/points");
export const runPathTrace = (duration = 5) => post(`/api/v1/sniffer/trace?duration=${duration}`, {});

// WS URLs derive from window.location so they follow whatever origin (and
// scheme: ws/wss) is currently serving the frontend — Cloudflare tunnel,
// localhost, or LAN IP. The Bearer token cannot be set as a header on
// WebSocket upgrades from the browser, so we append it as an
// `access_token` query parameter; the backend reads it via Query().
function _wsUrl(path) {
  const url = new URL(path, window.location.origin);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  const token = getCurrentAccessToken();
  if (token) url.searchParams.set("access_token", token);
  return url;
}

export function buildSnifferWsUrl(pointId, { filter, count = 0, duration = 300 } = {}) {
  const url = _wsUrl(`/api/v1/ws/sniffer/${pointId}`);
  if (filter) url.searchParams.set("filter", filter);
  if (count > 0) url.searchParams.set("count", String(count));
  url.searchParams.set("duration", String(duration));
  return url.toString();
}

// Logs WebSocket
export function buildLogsWsUrl(namespace, pod, container, opts = {}) {
  const url = _wsUrl(`/api/v1/ws/logs/${namespace}/${pod}`);
  if (container) url.searchParams.set("container", container);
  if (opts.tail) url.searchParams.set("tail", String(opts.tail));
  if (opts.fromStart) url.searchParams.set("from_start", "1");
  return url.toString();
}

// Exec WebSocket
export function buildExecWsUrl(namespace, pod, container, command = "/bin/sh") {
  const url = _wsUrl(`/api/v1/ws/exec/${namespace}/${pod}`);
  if (container) url.searchParams.set("container", container);
  if (command) url.searchParams.set("command", command);
  return url.toString();
}

// Deployment scaling
export const scaleDeployment = (name, replicas, ns = "5g") =>
  post(`/api/v1/deployments/${name}/scale`, { replicas, namespace: ns });

// Kubernetes inventory (generic cluster section, not 5G-specific)
export const getK8sNamespaces = () => get("/api/v1/k8s/namespaces");
export const getK8sNodesDetailed = () => get("/api/v1/k8s/nodes");
export const getK8sPvcs = (ns) =>
  get(`/api/v1/k8s/pvcs${ns ? `?namespace=${encodeURIComponent(ns)}` : ""}`);
export const getK8sStorageClasses = () => get("/api/v1/k8s/storageclasses");
export const getK8sServices = (ns) =>
  get(`/api/v1/k8s/services${ns ? `?namespace=${encodeURIComponent(ns)}` : ""}`);
export const getK8sEvents = (ns, limit = 200) => {
  const params = new URLSearchParams();
  if (ns) params.set("namespace", ns);
  params.set("limit", String(limit));
  return get(`/api/v1/k8s/events?${params.toString()}`);
};

// NF version management (5g-nf-platform integration)
export const getNfVersions = () => get("/api/v1/nf/versions");
export const getNfUpdateStreamUrl = () => "/api/v1/nf/update/stream";
