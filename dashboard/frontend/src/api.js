const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8080";

async function get(path) {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) throw new Error(`${path} failed: ${res.status}`);
  return res.json();
}

async function post(path, body) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
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
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${path} failed: ${res.status} ${text}`);
  }
  return res.json();
}

async function del(path) {
  const res = await fetch(`${API_BASE}${path}`, { method: "DELETE" });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${path} failed: ${res.status} ${text}`);
  }
  return res.json();
}

async function patch(path, body) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${path} failed: ${res.status} ${text}`);
  }
  return res.json();
}

// Health
export const getRuntimeInfo = () => get("/health");

// Admin: restart backend via systemd
export const restartBackend = () => post("/api/v1/admin/restart-backend", {});

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
  const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8080";
  const res = await fetch(`${API_BASE}/api/v1/ran/modes/physical/enable/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
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
  const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8080";
  const res = await fetch(`${API_BASE}/api/v1/ran/modes/physical/disable/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
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
export const getNodeMetricsRange = (mins = 30) => get(`/api/v1/metrics/range/nodes?minutes=${mins}`);
export const getNfMetricsRange = (mins = 30) => get(`/api/v1/metrics/range/nf?minutes=${mins}`);

// Network Health
export const getNetworkHealth = () => get("/api/v1/network/health");
export const runNetworkHealthCheck = () => post("/api/v1/network/health/run", {});
export const getN6NatDiagnostics = () => get("/api/v1/network/n6-nat");

// UE Monitoring
export const getUeSummary = () => get("/api/v1/ue/summary");
export const getUeEvents = (mins = 10) => get(`/api/v1/ue/events?minutes=${mins}`);
export const getActiveUes = () => get("/api/v1/ue/active");
export const getUeGnbs = () => get("/api/v1/ue/gnbs");
export const getUePods = () => get("/api/v1/ue/pods");
export const runUePing = (pod, target = "8.8.8.8") => post("/api/v1/ue/test/ping", { pod, target });
export const runUeIperf = (pod, server = "10.45.0.1", duration = 5) => post("/api/v1/ue/test/iperf", { pod, server, duration });

// Sniffer
export const getSnifferPoints = () => get("/api/v1/sniffer/points");
export const runPathTrace = (duration = 5) => post(`/api/v1/sniffer/trace?duration=${duration}`, {});

export function buildSnifferWsUrl(pointId, { filter, count = 0, duration = 300 } = {}) {
  const url = new URL(`${API_BASE}/api/v1/ws/sniffer/${pointId}`);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  if (filter) url.searchParams.set("filter", filter);
  if (count > 0) url.searchParams.set("count", String(count));
  url.searchParams.set("duration", String(duration));
  return url.toString();
}

// Logs WebSocket
export function buildLogsWsUrl(namespace, pod, container) {
  const url = new URL(`${API_BASE}/api/v1/ws/logs/${namespace}/${pod}`);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  if (container) url.searchParams.set("container", container);
  return url.toString();
}
