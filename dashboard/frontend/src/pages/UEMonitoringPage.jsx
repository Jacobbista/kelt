import React, { useEffect, useState, useCallback, useRef } from "react";
import { useAuth } from "../auth/AuthContext";
import {
  getUeSummary,
  getUeEvents,
  getActiveUes,
  getUeGnbs,
  getUePods,
  getRanStatus,
  runUePing,
  runUeIperf,
  upsertUePersonalization,
  deleteUePersonalization,
} from "../api";
import Loader from "../components/Loader";

const ICON_OPTIONS = [
  { id: "phone",    glyph: "\u{1F4F1}" }, // mobile phone
  { id: "modem",    glyph: "\u{1F4E1}" }, // satellite antenna
  { id: "drone",    glyph: "\u{1F681}" }, // helicopter
  { id: "camera",   glyph: "\u{1F4F7}" },
  { id: "satellite",glyph: "\u{1F6F0}" },
  { id: "sensor",   glyph: "\u{1F52C}" }, // microscope
  { id: "laptop",   glyph: "\u{1F4BB}" },
  { id: "server",   glyph: "\u{1F5A5}" },
  { id: "iot",      glyph: "\u{1F3ED}" }, // factory
  { id: "car",      glyph: "\u{1F697}" },
];

function iconGlyph(id) {
  return ICON_OPTIONS.find((o) => o.id === id)?.glyph || "";
}

function formatKbps(kbps) {
  if (kbps == null) return "-";
  if (kbps >= 1_000_000) return `${(kbps / 1_000_000).toFixed(1)} Gbps`;
  if (kbps >= 1000) return `${(kbps / 1000).toFixed(1)} Mbps`;
  return `${kbps} kbps`;
}

// Client-side image constraints. Anything accepted here is resized to a
// thumbnail before upload, so the user can drop a full-resolution photo
// without sending megabytes to the backend.
const IMAGE_ACCEPT_MIMES = ["image/png", "image/jpeg", "image/jpg", "image/webp"];
const IMAGE_SOURCE_MAX_BYTES = 5 * 1024 * 1024;
const IMAGE_THUMBNAIL_DIM = 128;
const IMAGE_THUMBNAIL_QUALITY = 0.85;

/**
 * Load an image file and return a square-fit thumbnail as a WebP data URL.
 * The canvas keeps aspect ratio by center-cropping, because avatars look
 * terrible with letterboxing.
 */
function fileToThumbnailDataUrl(file) {
  return new Promise((resolve, reject) => {
    if (!IMAGE_ACCEPT_MIMES.includes(file.type)) {
      reject(new Error(`Unsupported type: ${file.type || "unknown"}. Use PNG, JPEG, or WebP.`));
      return;
    }
    if (file.size > IMAGE_SOURCE_MAX_BYTES) {
      reject(new Error(`Image is ${Math.round(file.size / 1024 / 1024)} MB, max 5 MB.`));
      return;
    }
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("Failed to read file"));
    reader.onload = () => {
      const img = new Image();
      img.onerror = () => reject(new Error("Failed to decode image"));
      img.onload = () => {
        const side = Math.min(img.naturalWidth, img.naturalHeight);
        const sx = (img.naturalWidth - side) / 2;
        const sy = (img.naturalHeight - side) / 2;
        const canvas = document.createElement("canvas");
        canvas.width = IMAGE_THUMBNAIL_DIM;
        canvas.height = IMAGE_THUMBNAIL_DIM;
        const ctx = canvas.getContext("2d");
        ctx.imageSmoothingQuality = "high";
        ctx.drawImage(img, sx, sy, side, side, 0, 0, IMAGE_THUMBNAIL_DIM, IMAGE_THUMBNAIL_DIM);
        // Browsers that can't encode WebP fall back to JPEG automatically.
        let dataUrl = canvas.toDataURL("image/webp", IMAGE_THUMBNAIL_QUALITY);
        if (!dataUrl.startsWith("data:image/webp")) {
          dataUrl = canvas.toDataURL("image/jpeg", IMAGE_THUMBNAIL_QUALITY);
        }
        resolve(dataUrl);
      };
      img.src = reader.result;
    };
    reader.readAsDataURL(file);
  });
}

/**
 * Deterministic color per IMSI so the fallback avatar doesn't flicker on
 * every render.
 */
function avatarColor(seed) {
  let h = 0;
  for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) | 0;
  const hue = Math.abs(h) % 360;
  return `hsl(${hue} 50% 35%)`;
}

function UeAvatar({ ue, size = 28 }) {
  const p = ue.personalization || {};
  const dim = { width: size, height: size, minWidth: size };
  if (p.image) {
    return (
      <img
        src={p.image}
        alt=""
        className="rounded-full object-cover border border-slate-700 shrink-0"
        style={dim}
      />
    );
  }
  const glyph = iconGlyph(p.icon);
  if (glyph) {
    return (
      <span
        className="inline-flex items-center justify-center rounded-full bg-slate-800 border border-slate-700 shrink-0"
        style={{ ...dim, fontSize: size * 0.55, lineHeight: 1 }}
      >
        {glyph}
      </span>
    );
  }
  const seed = p.nickname || ue.imsi || "?";
  const initial = seed.trim().charAt(0).toUpperCase() || "?";
  return (
    <span
      className="inline-flex items-center justify-center rounded-full font-semibold text-white shrink-0"
      style={{ ...dim, fontSize: size * 0.45, lineHeight: 1, background: avatarColor(seed) }}
    >
      {initial}
    </span>
  );
}

/* ── helpers ─────────────────────────────────────────────────── */

function StatCard({ label, value, sub, color = "text-indigo-300", onClick }) {
  return (
    <div
      className={`rounded-lg border border-slate-700 bg-slate-900 p-4${onClick ? " cursor-pointer hover:border-slate-600" : ""}`}
      onClick={onClick}
    >
      <div className="text-xs text-slate-400 mb-1">{label}</div>
      <div className={`text-3xl font-bold tabular-nums ${color}`}>{value}</div>
      {sub && <div className="mt-1 text-[11px] text-slate-500">{sub}</div>}
    </div>
  );
}

const DISCONNECT_TYPES = [
  "gnb_disconnect",
  "ue_context_release",
  "pdu_session_rel",
  "ue_removed",
  "session_removed",
  "deregistration_req",
  "deregistration_ok",
];
const ERROR_TYPES = ["registration_fail", "auth_reject"];

const WINDOW_OPTIONS = [
  { label: "1m",  seconds: 60 },
  { label: "5m",  seconds: 300 },
  { label: "15m", seconds: 900 },
  { label: "1h",  seconds: 3600 },
  { label: "6h",  seconds: 21600 },
];

function windowLabel(seconds) {
  const opt = WINDOW_OPTIONS.find((o) => o.seconds === seconds);
  return opt ? opt.label : `${seconds}s`;
}

function EventIcon({ ev }) {
  if (ev.type === "auth_reject") {
    return <span className="inline-block h-2.5 w-2.5 rounded-full bg-rose-500" title="Auth rejected" />;
  }
  if (ev.type === "deregistration_req" || ev.type === "deregistration_ok") {
    return <span className="inline-block h-2 w-2 rounded-full bg-amber-400" title="Deregistration" />;
  }
  if (ev.type === "ue_context_release") {
    return <span className="inline-block h-2 w-2 rounded-full bg-amber-400" title="Went idle (CM-IDLE)" />;
  }
  if (ev.type === "service_request") {
    return <span className="inline-block h-2 w-2 rounded-full bg-cyan-400" title="Returned from idle" />;
  }
  if (DISCONNECT_TYPES.includes(ev.type)) {
    return <span className="inline-block h-2 w-2 rounded-full bg-rose-400" title="Detach" />;
  }
  if (ev.type === "pdu_session_rel") {
    return <span className="inline-block h-2 w-2 rounded-full bg-amber-400" title="Session released" />;
  }
  const cls =
    ev.severity === "warning"
      ? "bg-amber-400"
      : ev.severity === "error"
        ? "bg-rose-400"
        : "bg-emerald-400";
  return <span className={`inline-block h-2 w-2 rounded-full ${cls}`} />;
}

function formatTs(ts) {
  if (!ts) return "-";
  try {
    const d = new Date(ts);
    if (isNaN(d)) return ts.slice(11, 19) || ts;
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch {
    return ts;
  }
}

function relativeTime(ts) {
  if (!ts) return "-";
  try {
    const now = new Date();
    const then = new Date(ts);
    const diffSec = Math.floor((now - then) / 1000);
    if (diffSec < 0) return "just now";
    if (diffSec < 60) return `${diffSec}s ago`;
    if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
    return `${Math.floor(diffSec / 3600)}h ago`;
  } catch {
    return ts;
  }
}

function eventKey(ev, i) {
  return `${ev.ts || ""}-${ev.type || ""}-${ev.imsi || ev.gnb_ip || i}`;
}

const STATUS_STYLES = {
  registered: "bg-emerald-900/40 text-emerald-400",
  idle: "bg-amber-900/30 text-amber-300",
  deregistering: "bg-amber-900/30 text-amber-300",
  deregistered: "bg-slate-800 text-slate-500",
  released: "bg-amber-900/30 text-amber-300",  // legacy alias for idle
  failed: "bg-rose-900/30 text-rose-400",
  stale: "bg-rose-900/20 text-rose-400 italic",
};

// "idle" and "released" are non-terminal: UE is still registered at AMF,
// PDU sessions survive CM-IDLE in 5G NR.
const TERMINAL_STATUSES = new Set(["deregistered", "failed", "stale"]);

const EVENT_FILTERS = [
  { key: "all", label: "All" },
  {
    key: "registration",
    label: "Registration",
    types: ["registration_req", "registration_ok", "registration_fail", "deregistration_req", "deregistration_ok"],
  },
  {
    key: "session",
    label: "Session",
    types: ["pdu_session_est", "pdu_session_rel", "pdu_request"],
  },
  {
    key: "connect",
    label: "Attach",
    types: ["gnb_connect", "registration_ok", "pdu_session_est", "service_request"],
  },
  {
    key: "disconnect",
    label: "Detach",
    types: DISCONNECT_TYPES,
  },
  { key: "error", label: "Errors", types: ERROR_TYPES },
  { key: "count", label: "Counts", types: ["ue_count_change", "session_count_change"] },
];

/* ── main page ───────────────────────────────────────────────── */

export default function UEMonitoringPage() {
  const auth = useAuth();
  const canWrite = !auth.enabled || auth.roles.includes("dashboard-admin");
  const [summary, setSummary] = useState(null);
  const [events, setEvents] = useState([]);
  const [activeUes, setActiveUes] = useState([]);
  const [gnbs, setGnbs] = useState([]);
  const [uePods, setUePods] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [testPod, setTestPod] = useState("");
  const [testTarget, setTestTarget] = useState("8.8.8.8");
  const [testRunning, setTestRunning] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [ranStatus, setRanStatus] = useState(null);

  const [eventFilter, setEventFilter] = useState("all");
  const [expandedEvent, setExpandedEvent] = useState(null);
  const eventFeedRef = useRef(null);

  // Counter window (seconds). 5 minutes by default; counters use PromQL
  // increase(metric[Ns]) so tiles reflect activity in this window, not the
  // monotonic total since AMF start.
  const [windowSeconds, setWindowSeconds] = useState(300);

  // Active UE row UI state: which IMSI is expanded, which is in edit mode.
  const [expandedUe, setExpandedUe] = useState(null);
  const [editingUe, setEditingUe] = useState(null);
  const [editDraft, setEditDraft] = useState({ nickname: "", icon: "", image: "" });

  const refresh = useCallback(async () => {
    try {
      const [s, e, u, g, p] = await Promise.all([
        getUeSummary(windowSeconds),
        getUeEvents(10),
        getActiveUes(),
        getUeGnbs().catch(() => []),
        getUePods(),
      ]);
      setSummary(s);
      setEvents(e);
      setActiveUes(u);
      setGnbs(Array.isArray(g) ? g : []);
      setUePods(p);
      if (p.length > 0 && !testPod) setTestPod(p[0].name);
      setError(null);
      // /ran/status is admin-only: for a viewer the call is a guaranteed 403,
      // and its only use here is the "start the RAN" hint, which a viewer
      // cannot act on anyway.
      if ((s?.connected_gnbs ?? 0) === 0 && canWrite) {
        getRanStatus().then(setRanStatus).catch(() => setRanStatus(null));
      } else {
        setRanStatus(null);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [testPod, windowSeconds, canWrite]);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 8000);
    return () => clearInterval(id);
  }, [refresh]);

  async function handlePing() {
    setTestRunning(true);
    setTestResult(null);
    try {
      const r = await runUePing(testPod, testTarget);
      setTestResult({ type: "ping", ...r });
    } catch (err) {
      setTestResult({ type: "ping", exit_code: 1, stderr: err.message });
    } finally {
      setTestRunning(false);
    }
  }

  async function handleIperf() {
    setTestRunning(true);
    setTestResult(null);
    try {
      const r = await runUeIperf(testPod);
      setTestResult({ type: "iperf", ...r });
    } catch (err) {
      setTestResult({ type: "iperf", exit_code: 1, stderr: err.message });
    } finally {
      setTestRunning(false);
    }
  }

  function scrollToEvents(filter) {
    setEventFilter(filter);
    eventFeedRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function openEdit(ue) {
    setEditingUe(ue.imsi);
    setExpandedUe(ue.imsi);
    setEditDraft({
      nickname: ue.personalization?.nickname || "",
      icon: ue.personalization?.icon || "",
      image: ue.personalization?.image || "",
    });
  }

  async function saveEdit(imsi) {
    try {
      const next = {
        nickname: editDraft.nickname.trim() || null,
        icon: editDraft.icon || null,
        image: editDraft.image || null,
      };
      await upsertUePersonalization(imsi, {
        nickname: next.nickname,
        icon: next.icon,
        // Send "" to explicitly clear a previously-saved image.
        image: editDraft.image === "" && !next.image ? "" : next.image,
      });
      // Optimistically update in-place so the UI reflects the change before
      // the next 8s poll arrives.
      setActiveUes((prev) =>
        prev.map((u) =>
          u.imsi === imsi ? { ...u, personalization: next } : u,
        ),
      );
      setEditingUe(null);
    } catch (err) {
      setError(`Failed to save personalization: ${err.message}`);
    }
  }

  async function clearPersonalization(imsi) {
    try {
      await deleteUePersonalization(imsi);
      setActiveUes((prev) =>
        prev.map((u) => (u.imsi === imsi ? { ...u, personalization: undefined } : u)),
      );
      setEditingUe(null);
    } catch (err) {
      setError(`Failed to clear personalization: ${err.message}`);
    }
  }

  if (loading) {
    return (
      <div className="flex min-h-[200px] items-center justify-center p-6">
        <Loader size="lg" label="Loading UE monitoring data..." />
      </div>
    );
  }

  const s = summary || {};

  const filteredEvents = eventFilter === "all"
    ? events
    : events.filter((ev) => {
        const filter = EVENT_FILTERS.find((f) => f.key === eventFilter);
        if (!filter?.types) return true;
        return filter.types.includes(ev.type);
      });

  return (
    <div className="space-y-6 p-6">
      <h2 className="text-xl font-semibold text-white">UE Monitoring</h2>

      {error && (
        <div className="rounded border border-rose-700/40 bg-rose-950/30 p-3 text-sm text-rose-300">
          {error}
        </div>
      )}

      {/* Info when no gNBs - only if both Prometheus and infoAPI say 0 */}
      {(s.connected_gnbs ?? 0) === 0 && gnbs.length === 0 && ranStatus && (() => {
        const prereqsOk = ranStatus.amf_pod_ready && ranStatus.bridge_exists && ranStatus.amf_has_physical_ran;
        return (
          <div className={`rounded-lg border p-4 ${
            prereqsOk
              ? "border-slate-700/50 bg-slate-900/30"
              : "border-amber-800/50 bg-amber-950/20"
          }`}>
            <h3 className={`text-sm font-medium mb-2 ${prereqsOk ? "text-slate-300" : "text-amber-300"}`}>
              {prereqsOk ? "No gNB connected yet" : "No gNBs connected -- check prerequisites"}
            </h3>
            {prereqsOk ? (
              <p className="text-xs text-slate-400">
                Core and RAN infra are ready. If using a physical femtocell, ensure it is powered on and connected to the worker NIC.
              </p>
            ) : (
              <>
                <p className="text-xs text-slate-400 mb-3">If using a physical femtocell, verify:</p>
                <ul className="text-xs text-slate-400 space-y-1 mb-3">
                  <li>AMF pod: {ranStatus.amf_pod_ready ? "Running" : "Not ready"}</li>
                  <li>br-ran bridge: {ranStatus.bridge_exists ? "Exists" : "Missing"}</li>
                  <li>Worker NIC (in br-ran): {ranStatus.bridge_detected ? `${ranStatus.ran_interface_detected || "detected"}` : "Not found"}</li>
                  <li>AMF n2-physical: {ranStatus.amf_has_physical_ran ? "Enabled" : "Disabled -- click Enable in RAN Config"}</li>
                </ul>
                <p className="text-[11px] text-slate-500">
                  RAN page &rarr; Physical RAN: click <strong>Enable Physical</strong> to add n2-physical to AMF.
                  If the host interface changed (e.g. via hub), run{" "}
                  <code className="rounded bg-slate-800 px-1">PHYSICAL_RAN_BRIDGE=&lt;host_nic&gt; vagrant reload worker</code>.
                </p>
              </>
            )}
          </div>
        );
      })()}

      {/* Summary cards - use infoAPI count when Prometheus returns 0 */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard
          label="Connected gNBs"
          value={Math.max(s.connected_gnbs ?? 0, gnbs.length)}
          color={(s.connected_gnbs ?? 0) > 0 || gnbs.length > 0 ? "text-emerald-400" : "text-slate-500"}
        />
        <StatCard
          label="RAN UEs"
          value={s.ran_ues ?? 0}
          color={s.ran_ues > 0 ? "text-cyan-400" : "text-slate-500"}
        />
        <StatCard
          label="Active Sessions"
          value={s.amf_sessions ?? 0}
          color={s.amf_sessions > 0 ? "text-indigo-400" : "text-slate-500"}
        />
        <StatCard
          label="Registered Subscribers"
          value={s.registered_subscribers ?? 0}
          color={s.registered_subscribers > 0 ? "text-violet-400" : "text-slate-500"}
        />
      </div>

      {/* Registration counters — windowed rates, not cumulative totals */}
      <div className="rounded-lg border border-slate-700 bg-slate-900 p-4">
        <div className="mb-3 flex items-center justify-between gap-3">
          <h3 className="text-sm font-medium text-slate-300">
            Registration Activity
            <span className="ml-2 text-[11px] font-normal text-slate-500">
              last {windowLabel(windowSeconds)} &middot; via PromQL increase()
            </span>
          </h3>
          <div className="flex rounded-md border border-slate-700 bg-slate-950/50 p-0.5">
            {WINDOW_OPTIONS.map((opt) => (
              <button
                key={opt.seconds}
                type="button"
                onClick={() => setWindowSeconds(opt.seconds)}
                className={`rounded px-2 py-0.5 text-[10px] font-medium transition-colors ${
                  windowSeconds === opt.seconds
                    ? "bg-indigo-600/30 text-indigo-300"
                    : "text-slate-400 hover:bg-slate-800 hover:text-slate-200"
                }`}
                title={`Window size: ${opt.label}`}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>
        <div className="grid grid-cols-3 gap-4 sm:grid-cols-6 text-center">
          <CounterCell label="Init Req" value={s.reg_init_req} />
          <CounterCell label="Init OK" value={s.reg_init_succ} ok />
          <CounterCell label="Init Fail" value={s.reg_init_fail} bad />
          <CounterCell label="Mobility Req" value={s.reg_mobility_req} />
          <CounterCell label="Mobility OK" value={s.reg_mobility_succ} ok />
          <div
            onClick={() => (s.auth_reject ?? 0) > 0 && scrollToEvents("error")}
            className={(s.auth_reject ?? 0) > 0 ? "cursor-pointer" : ""}
            title={(s.auth_reject ?? 0) > 0 ? "Click to see auth reject details in event feed" : ""}
          >
            <CounterCell label="Auth Reject" value={s.auth_reject} bad />
          </div>
        </div>
      </div>

      {/* Auth reject context banner — only shown when recent log events include a rejection */}
      {events.some((e) => e.type === "auth_reject") && (
        <div className="rounded border border-rose-800/40 bg-rose-950/20 px-3 py-2 text-xs text-rose-300">
          Authentication reject detected in recent logs.
          {" "}Check the event feed below for per-UE details, or verify subscriber K/OPc keys on the Subscribers page.
          <button
            onClick={() => scrollToEvents("error")}
            className="ml-2 underline text-rose-400 hover:text-rose-300"
          >
            View errors
          </button>
        </div>
      )}

      {/* Connected gNBs (from AMF infoAPI) */}
      {gnbs.length > 0 && (
        <div className="rounded-lg border border-slate-700 bg-slate-900 p-4">
          <h3 className="text-sm font-medium text-slate-300 mb-3">
            Connected gNBs
            <span className="ml-2 text-xs text-slate-500 font-normal">gnb_id, PLMN, peer</span>
          </h3>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-slate-700 text-left text-slate-400">
                  <th className="pb-2 pr-4">gNB ID</th>
                  <th className="pb-2 pr-4">PLMN</th>
                  <th className="pb-2 pr-4">Peer (SCTP)</th>
                  <th className="pb-2">UEs</th>
                </tr>
              </thead>
              <tbody>
                {gnbs.map((g, i) => (
                  <tr key={i} className="border-b border-slate-800">
                    <td className="py-2 pr-4 font-mono text-cyan-300">{g.gnb_id ?? "--"}</td>
                    <td className="py-2 pr-4 font-mono text-slate-200">{g.plmn ?? "--"}</td>
                    <td className="py-2 pr-4 font-mono text-slate-400">{g.peer ?? "--"}</td>
                    <td className="py-2 text-slate-500">{g.num_connected_ues ?? 0}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Active UEs table */}
      <div className="rounded-lg border border-slate-700 bg-slate-900 p-4">
        <h3 className="text-sm font-medium text-slate-300 mb-3">
          Active UEs
          {activeUes.length === 0 && (
            <span className="ml-2 text-xs text-slate-500 font-normal">
              No UE registrations detected in recent logs
            </span>
          )}
        </h3>
        {(() => {
          // Surface inconsistencies between the log-reconstructed list and
          // Backend marks orphaned AMF contexts as stale via gnb-info count comparison.
          // Prometheus ran_ue divergence during CM-IDLE transitions is a transient
          // race condition — not shown to avoid false alarms.
          const staleCount = activeUes.filter((u) => u.status === "stale").length;
          if (staleCount === 0) return null;
          return (
            <div className="mb-3 rounded border border-amber-700/50 bg-amber-900/20 px-3 py-2 text-[11px] text-amber-200">
              <div className="font-semibold">Stale UE context detected</div>
              <ul className="mt-1 list-disc pl-4 text-amber-100/80">
                <li>
                  <span className="font-mono">{staleCount}</span> UE
                  {staleCount !== 1 ? "s" : ""} flagged stale — AMF retains
                  context but gNB UE count is lower.
                </li>
              </ul>
              <div className="mt-1 text-[10px] text-amber-100/60">
                Likely cause: UE crash, radio drop, or forced reboot without
                Deregistration. Open5GS will release the orphan context on its
                own timer.
              </div>
            </div>
          );
        })()}
        {activeUes.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-slate-700 text-left text-slate-400">
                  <th className="pb-2 pr-2 w-6"></th>
                  <th className="pb-2 pr-4">Device</th>
                  <th className="pb-2 pr-4">Status</th>
                  <th className="pb-2 pr-4">PDU Sessions</th>
                  <th className="pb-2 pr-2">Last Seen</th>
                  <th className="pb-2 w-10"></th>
                </tr>
              </thead>
              <tbody>
                {activeUes.map((ue) => {
                  const isDead = TERMINAL_STATUSES.has(ue.status);
                  const isExpanded = expandedUe === ue.imsi;
                  const isEditing = editingUe === ue.imsi;
                  const nickname = ue.personalization?.nickname;
                  return (
                  <React.Fragment key={ue.imsi}>
                  <tr
                    className={`border-b border-slate-800${isDead ? " opacity-60" : ""} cursor-pointer hover:bg-slate-800/40`}
                    onClick={() => setExpandedUe(isExpanded ? null : ue.imsi)}
                  >
                    <td className="py-2 pr-2 text-slate-500 text-center text-[10px]">
                      {isExpanded ? "\u25BE" : "\u25B8"}
                    </td>
                    <td className="py-2 pr-4">
                      <div className="flex items-center gap-2.5">
                        <UeAvatar ue={ue} size={32} />
                        <div>
                          {nickname && (
                            <div className="text-slate-200 font-medium leading-tight">{nickname}</div>
                          )}
                          <div className="font-mono text-[11px] text-slate-400 leading-tight">
                            {ue.imsi}
                          </div>
                        </div>
                      </div>
                    </td>
                    <td className="py-2 pr-4">
                      <span
                        className={`rounded px-1.5 py-0.5 text-[10px] font-bold uppercase ${
                          STATUS_STYLES[ue.status] || "bg-slate-800 text-slate-500"
                        }`}
                        title={ue.status_reason || ""}
                      >
                        {ue.status}
                      </span>
                    </td>
                    <td className="py-2 pr-4">
                      {isDead ? (
                        <span className="text-slate-600">&mdash;</span>
                      ) : ue.sessions && ue.sessions.length > 0 ? (
                        <>
                          {ue.sessions.slice(0, 3).map((sess, i) => (
                            <span
                              key={i}
                              className="mr-1.5 mb-1 inline-block rounded bg-indigo-900/30 px-1.5 py-0.5 text-[10px] text-indigo-300"
                            >
                              {sess.ue_ip || "?"} ({sess.dnn || "?"})
                            </span>
                          ))}
                          {ue.sessions.length > 3 && (
                            <span
                              className="inline-block rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-400"
                              title={ue.sessions.slice(3).map(s => `${s.ue_ip} (${s.dnn})`).join(", ")}
                            >
                              +{ue.sessions.length - 3} more
                            </span>
                          )}
                        </>
                      ) : (
                        <span className="text-slate-600">&mdash;</span>
                      )}
                    </td>
                    <td className="py-2 pr-2 text-slate-500 text-[11px]" title={formatTs(ue.last_seen)}>
                      {relativeTime(ue.last_seen)}
                    </td>
                    <td className="py-2 text-right">
                      {/* Nicknames are persisted server-side through an admin-only
                          route, so a viewer gets no edit affordance. */}
                      {canWrite && (
                      <button
                        onClick={(e) => { e.stopPropagation(); openEdit(ue); }}
                        className="rounded px-1.5 py-0.5 text-[10px] text-slate-400 hover:bg-slate-700 hover:text-slate-200"
                        title="Set nickname / icon for this UE"
                      >
                        Edit
                      </button>
                      )}
                    </td>
                  </tr>
                  {isExpanded && (
                    <tr className="border-b border-slate-800 bg-slate-950/40">
                      <td></td>
                      <td colSpan={5} className="px-2 py-3">
                        <UeDetails ue={ue} />
                        {isEditing && (
                          <UePersonalizationEditor
                            draft={editDraft}
                            onChange={setEditDraft}
                            onSave={() => saveEdit(ue.imsi)}
                            onClear={
                              ue.personalization ? () => clearPersonalization(ue.imsi) : null
                            }
                            onCancel={() => setEditingUe(null)}
                          />
                        )}
                      </td>
                    </tr>
                  )}
                  </React.Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Event feed */}
      <div ref={eventFeedRef} className="rounded-lg border border-slate-700 bg-slate-900 p-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-medium text-slate-300">
            Event Feed
            <span className="ml-2 text-xs text-slate-500 font-normal">last 10 min</span>
          </h3>
          <div className="flex gap-1">
            {EVENT_FILTERS.map((f) => (
              <button
                key={f.key}
                onClick={() => { setEventFilter(f.key); setExpandedEvent(null); }}
                className={`rounded px-2 py-0.5 text-[10px] font-medium transition-colors ${
                  eventFilter === f.key
                    ? "bg-indigo-600 text-white"
                    : "bg-slate-800 text-slate-400 hover:bg-slate-700"
                }`}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>
        {filteredEvents.length === 0 ? (
          <p className="text-xs text-slate-500">
            {events.length === 0 ? "No UE events in recent logs" : "No events match this filter"}
          </p>
        ) : (
          <div className="max-h-80 overflow-y-auto space-y-0.5">
            {filteredEvents.map((ev, i) => {
              const key = eventKey(ev, i);
              const isDisconnect = DISCONNECT_TYPES.includes(ev.type);
              const isError = ERROR_TYPES.includes(ev.type);
              const isExpanded = expandedEvent === key;
              const hasReason = !!ev.reason;

              return (
                <div key={key}>
                  <div
                    className={`flex items-start gap-2 rounded px-2 py-1.5 text-xs hover:bg-slate-800/50 ${
                      isError
                        ? "border-l-2 border-rose-400/70"
                        : isDisconnect || ev.severity === "warning"
                          ? "border-l-2 border-amber-500/50"
                          : "border-l-2 border-transparent"
                    } ${hasReason ? "cursor-pointer" : ""}`}
                    onClick={() => hasReason && setExpandedEvent(isExpanded ? null : key)}
                  >
                    <EventIcon ev={ev} />
                    <span className="text-slate-500 w-16 shrink-0 tabular-nums">
                      {formatTs(ev.ts)}
                    </span>
                    <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-400 uppercase shrink-0">
                      {ev.source}
                    </span>
                    <span className={isError ? "text-rose-300" : isDisconnect ? "text-amber-300" : "text-slate-300"}>
                      {ev.type === "gnb_connect" && ev.gnb_ip && gnbs.length > 0
                        ? (() => {
                            const g = gnbs.find((x) => (x.peer || "").includes(ev.gnb_ip));
                            return g
                              ? `gNB ${g.gnb_id ?? "?"} (PLMN ${g.plmn ?? "?"}) from ${ev.gnb_ip}`
                              : ev.detail;
                          })()
                        : ev.detail}
                    </span>
                    {ev.multiplicity > 1 && (
                      <span
                        className="shrink-0 rounded bg-slate-800 px-1.5 py-0.5 text-[10px] font-mono text-slate-400"
                        title={`${ev.multiplicity} occurrences within a 30s window (first ${formatTs(ev.first_ts)})`}
                      >
                        ×{ev.multiplicity}
                      </span>
                    )}
                    {hasReason && (
                      <span className={`ml-auto shrink-0 text-[10px] text-slate-600 transition-transform ${isExpanded ? "rotate-90" : ""}`}>
                        &#9656;
                      </span>
                    )}
                  </div>
                  {isExpanded && ev.reason && (
                    <div className="ml-8 mb-1 rounded bg-slate-800/70 border border-slate-700/50 px-3 py-2 text-xs text-slate-400 space-y-2">
                      <div>
                        <span className="text-slate-500 font-medium">Cause: </span>
                        <span className={ev.type === "auth_reject" ? "text-rose-300" : ""}>{ev.reason}</span>
                      </div>
                      {ev.type === "auth_reject" && (
                        <div className="text-[10px] text-slate-500 border-t border-slate-700/50 pt-1.5">
                          {(ev.cause_code === 20 || /mac failure|K\/OPc/i.test(ev.reason)) && (
                            <span>
                              <strong className="text-slate-400">Debug:</strong> UE rejected the network challenge (wrong AUTN MAC).
                              Verify that K and OPc in the Subscribers page exactly match what is burned on the SIM.
                              If using a soft-SIM, re-burn with the correct values.
                            </span>
                          )}
                          {(ev.cause_code === 21 || /synch|sqn/i.test(ev.reason)) && (
                            <span>
                              <strong className="text-slate-400">Debug:</strong> SIM SQN counter is ahead of the network SQN.
                              Delete the subscriber and re-add it to reset the sequence number.
                            </span>
                          )}
                          {(ev.cause_code === 3 || /not found|not provisioned|malformed/i.test(ev.reason)) && (
                            <span>
                              <strong className="text-slate-400">Debug:</strong> IMSI not found or subscriber data is invalid.
                              Check the Subscribers page: the IMSI must match exactly and the JSON must be well-formed
                              (correct slice/DNN config).
                            </span>
                          )}
                          {(ev.cause_code === 11 || /plmn|mcc.*mnc/i.test(ev.reason)) && (
                            <span>
                              <strong className="text-slate-400">Debug:</strong> PLMN (MCC/MNC) mismatch.
                              Subscriber config MCC/MNC must match network configuration and SIM PLMN.
                            </span>
                          )}
                        </div>
                      )}
                      {ev.raw_log && (
                        <div>
                          <span className="text-slate-500 font-medium block mb-1">AMF log line:</span>
                          <pre className="text-[10px] font-mono text-slate-500 whitespace-pre-wrap break-all rounded bg-slate-900/60 px-2 py-1.5 leading-relaxed">
                            {ev.raw_log}
                          </pre>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Testing panel (only if UERANSIM pods exist) */}
      {uePods.length > 0 && (
        <div className="rounded-lg border border-slate-700 bg-slate-900 p-4">
          <h3 className="text-sm font-medium text-slate-300 mb-3">
            Connectivity Tests
          </h3>
          <div className="flex flex-wrap items-end gap-3">
            <div>
              <label className="block text-[11px] text-slate-500 mb-1">UE Pod</label>
              <select
                value={testPod}
                onChange={(e) => setTestPod(e.target.value)}
                className="rounded bg-slate-800 border border-slate-700 px-2 py-1.5 text-xs text-slate-200"
              >
                {uePods.map((p) => (
                  <option key={p.name} value={p.name}>
                    {p.name} ({p.phase})
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-[11px] text-slate-500 mb-1">Target</label>
              <input
                value={testTarget}
                onChange={(e) => setTestTarget(e.target.value)}
                className="w-36 rounded bg-slate-800 border border-slate-700 px-2 py-1.5 text-xs text-slate-200"
              />
            </div>
            <button
              onClick={handlePing}
              disabled={testRunning}
              className="rounded bg-cyan-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-cyan-500 disabled:opacity-50"
            >
              {testRunning ? "Running..." : "Ping"}
            </button>
            <button
              onClick={handleIperf}
              disabled={testRunning}
              className="rounded bg-violet-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-violet-500 disabled:opacity-50"
            >
              {testRunning ? "Running..." : "iperf3"}
            </button>
          </div>
          {testResult && (
            <pre className="mt-3 max-h-48 overflow-auto rounded bg-slate-950 border border-slate-700 p-3 text-xs font-mono text-slate-300 whitespace-pre-wrap">
              {testResult.stdout || testResult.stderr || "No output"}
            </pre>
          )}
        </div>
      )}

    </div>
  );
}

function CounterCell({ label, value, ok, bad }) {
  const v = value ?? 0;
  let color = "text-slate-300";
  if (ok && v > 0) color = "text-emerald-400";
  if (bad && v > 0) color = "text-rose-400";
  return (
    <div>
      <div className={`text-lg font-bold tabular-nums ${color}`}>{v}</div>
      <div className="text-[10px] text-slate-500">{label}</div>
    </div>
  );
}

function DetailRow({ label, children }) {
  return (
    <div className="flex items-start gap-3 text-[11px]">
      <span className="w-28 shrink-0 text-slate-500">{label}</span>
      <div className="flex-1 text-slate-300">{children}</div>
    </div>
  );
}

function UeDetails({ ue }) {
  const plmn = ue.plmn || {};
  const sub = ue.subscription || { configured: false };
  const ambr = sub.ue_ambr_kbps || {};
  return (
    <div className="grid gap-2 md:grid-cols-2">
      <div className="space-y-2">
        <DetailRow label="PLMN">
          {plmn.mcc && plmn.mnc ? (
            <>
              <span className="font-mono text-cyan-300">
                MCC {plmn.mcc} &middot; MNC {plmn.mnc}
              </span>
              {plmn.is_test_plmn && (
                <span className="ml-2 rounded bg-amber-900/30 px-1.5 py-0.5 text-[9px] font-semibold uppercase text-amber-300">
                  test
                </span>
              )}
            </>
          ) : (
            <span className="text-slate-500">-</span>
          )}
        </DetailRow>
        <DetailRow label="MSIN">
          <span className="font-mono text-slate-400">{plmn.msin || "-"}</span>
        </DetailRow>
        <DetailRow label="gNB">
          <span className="font-mono text-slate-400">{ue.gnb_ip || "-"}</span>
        </DetailRow>
        <DetailRow label="Auth method">
          <span className="font-mono text-slate-400">{sub.auth_method || "-"}</span>
        </DetailRow>
      </div>
      <div className="space-y-2">
        <DetailRow label="Provisioned">
          {sub.configured ? (
            <span className="text-emerald-400">Yes</span>
          ) : (
            <span className="text-rose-400">Not found in subscriber DB</span>
          )}
        </DetailRow>
        <DetailRow label="UE-AMBR">
          {ambr.uplink || ambr.downlink ? (
            <span className="font-mono text-slate-400">
              &uarr; {formatKbps(ambr.uplink)} &middot; &darr; {formatKbps(ambr.downlink)}
            </span>
          ) : (
            <span className="text-slate-500">-</span>
          )}
        </DetailRow>
        <DetailRow label="Slices">
          {sub.slices && sub.slices.length > 0 ? (
            <ul className="space-y-0.5">
              {sub.slices.map((sl, i) => (
                <li key={i} className="font-mono text-slate-400">
                  SST {sl.sst}
                  {sl.sd ? ` SD ${sl.sd}` : ""}
                  {sl.default && (
                    <span className="ml-1 rounded bg-indigo-900/40 px-1 py-0.5 text-[9px] text-indigo-300">
                      default
                    </span>
                  )}
                  {sl.dnns && sl.dnns.length > 0 && (
                    <span className="ml-2 text-slate-500">&rarr; {sl.dnns.join(", ")}</span>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <span className="text-slate-500">-</span>
          )}
        </DetailRow>
      </div>
    </div>
  );
}

function ImageDropZone({ value, onChange, onError }) {
  const inputRef = useRef(null);
  const [dragActive, setDragActive] = useState(false);
  const [busy, setBusy] = useState(false);

  async function handleFile(file) {
    if (!file) return;
    setBusy(true);
    try {
      const dataUrl = await fileToThumbnailDataUrl(file);
      onChange(dataUrl);
    } catch (err) {
      onError?.(err.message || String(err));
    } finally {
      setBusy(false);
    }
  }

  function onDrop(e) {
    e.preventDefault();
    setDragActive(false);
    const f = e.dataTransfer?.files?.[0];
    if (f) handleFile(f);
  }

  return (
    <div>
      <label className="block text-[10px] text-slate-500 mb-1">Image</label>
      <div className="flex items-start gap-3">
        <div
          onDragEnter={(e) => { e.preventDefault(); setDragActive(true); }}
          onDragOver={(e) => { e.preventDefault(); setDragActive(true); }}
          onDragLeave={() => setDragActive(false)}
          onDrop={onDrop}
          onClick={() => inputRef.current?.click()}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") inputRef.current?.click();
          }}
          role="button"
          tabIndex={0}
          className={`flex min-h-[72px] w-60 cursor-pointer flex-col items-center justify-center rounded border-2 border-dashed px-2 py-2 text-center text-[11px] transition-colors ${
            dragActive
              ? "border-indigo-400 bg-indigo-500/10 text-indigo-200"
              : "border-slate-700 bg-slate-800/50 text-slate-400 hover:border-slate-500"
          }`}
        >
          {busy ? (
            <span>Processing image...</span>
          ) : value ? (
            <>
              <span>Click or drop to replace</span>
              <span className="text-[10px] text-slate-500 mt-0.5">
                Resized to {IMAGE_THUMBNAIL_DIM}x{IMAGE_THUMBNAIL_DIM} WebP
              </span>
            </>
          ) : (
            <>
              <span>Drop image here or click to select</span>
              <span className="text-[10px] text-slate-500 mt-0.5">
                PNG / JPEG / WebP, max 5 MB
              </span>
            </>
          )}
          <input
            ref={inputRef}
            type="file"
            accept={IMAGE_ACCEPT_MIMES.join(",")}
            className="hidden"
            onChange={(e) => handleFile(e.target.files?.[0])}
          />
        </div>
        {value && (
          <div className="flex flex-col items-center gap-1">
            <img
              src={value}
              alt="UE avatar preview"
              className="h-16 w-16 rounded-full border border-slate-700 object-cover"
            />
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); onChange(""); }}
              className="text-[10px] text-rose-300 hover:text-rose-200"
            >
              Remove
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

function UePersonalizationEditor({ draft, onChange, onSave, onClear, onCancel }) {
  const [imageErr, setImageErr] = useState(null);
  return (
    <div className="mt-3 rounded border border-slate-700 bg-slate-900/70 p-3">
      <div className="mb-2 text-[11px] font-medium text-slate-300">
        Personalization <span className="text-slate-500">(stored in dashboard DB, per-IMSI)</span>
      </div>
      <div className="flex flex-wrap items-start gap-4">
        <div>
          <label className="block text-[10px] text-slate-500 mb-1">Nickname</label>
          <input
            value={draft.nickname}
            onChange={(e) => onChange({ ...draft, nickname: e.target.value })}
            maxLength={64}
            placeholder="e.g. drone #1"
            className="w-48 rounded bg-slate-800 border border-slate-700 px-2 py-1 text-xs text-slate-200"
          />
        </div>
        <ImageDropZone
          value={draft.image}
          onChange={(image) => { setImageErr(null); onChange({ ...draft, image }); }}
          onError={setImageErr}
        />
        <div>
          <label className="block text-[10px] text-slate-500 mb-1">
            Icon <span className="text-slate-600">(fallback when no image)</span>
          </label>
          <div className="flex flex-wrap gap-1">
            <button
              type="button"
              onClick={() => onChange({ ...draft, icon: "" })}
              className={`h-7 w-7 rounded border text-sm ${
                draft.icon === ""
                  ? "border-indigo-400 bg-indigo-600/20"
                  : "border-slate-700 bg-slate-800 hover:border-slate-500"
              }`}
              title="No icon"
            >
              &oslash;
            </button>
            {ICON_OPTIONS.map((opt) => (
              <button
                key={opt.id}
                type="button"
                onClick={() => onChange({ ...draft, icon: opt.id })}
                className={`h-7 w-7 rounded border text-base ${
                  draft.icon === opt.id
                    ? "border-indigo-400 bg-indigo-600/20"
                    : "border-slate-700 bg-slate-800 hover:border-slate-500"
                }`}
                title={opt.id}
              >
                {opt.glyph}
              </button>
            ))}
          </div>
        </div>
      </div>
      {imageErr && (
        <div className="mt-2 rounded border border-rose-800/40 bg-rose-950/20 px-2 py-1 text-[11px] text-rose-300">
          {imageErr}
        </div>
      )}
      <div className="mt-3 flex gap-2 justify-end">
        {onClear && (
          <button
            type="button"
            onClick={onClear}
            className="rounded bg-slate-800 px-2 py-1 text-[11px] text-rose-300 hover:bg-slate-700"
            title="Remove personalization from DB"
          >
            Clear all
          </button>
        )}
        <button
          type="button"
          onClick={onCancel}
          className="rounded bg-slate-800 px-2 py-1 text-[11px] text-slate-300 hover:bg-slate-700"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={onSave}
          className="rounded bg-indigo-600 px-3 py-1 text-[11px] font-medium text-white hover:bg-indigo-500"
        >
          Save
        </button>
      </div>
    </div>
  );
}
