import React, { useEffect, useState, useRef } from "react";
import { getTimeSync, forceTimeSync } from "../api";

const VM_ORDER = ["ansible", "master", "worker", "edge"];

// Module-level cache so reopening is instant
let cachedData = null;
let cachedAt = 0;

// Reuse browser timezone for consistent display with sidebar clock
const _localFmt = new Intl.DateTimeFormat(undefined, {
  hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
});
const _tzAbbr = (() => {
  const parts = new Intl.DateTimeFormat(undefined, { timeZoneName: "short" }).formatToParts(new Date());
  return parts.find((p) => p.type === "timeZoneName")?.value ?? "LOC";
})();

function OffsetBadge({ offset, reachable }) {
  if (!reachable) return <span className="text-rose-400 text-[10px]">unreachable</span>;
  if (offset == null) return <span className="text-slate-500 text-[10px]">--</span>;
  const abs = Math.abs(offset);
  let color = "text-emerald-400";
  if (abs >= 2000) color = "text-rose-400";
  else if (abs >= 500) color = "text-amber-400";
  const sign = offset >= 0 ? "+" : "";
  return <span className={`font-mono text-[10px] ${color}`}>{sign}{offset}ms</span>;
}

function liveTime(baseIso, fetchedAt, now) {
  if (!baseIso) return "--";
  const advanced = new Date(Date.parse(baseIso) + (now - fetchedAt));
  return _localFmt.format(advanced);
}

function SkeletonRow() {
  return (
    <tr className="border-b border-slate-800/50">
      <td className="py-1 pr-2"><span className="inline-block h-3 w-12 animate-pulse rounded bg-slate-800" /></td>
      <td className="py-1 pr-2"><span className="inline-block h-3 w-20 animate-pulse rounded bg-slate-800" /></td>
      <td className="py-1 text-right"><span className="inline-block h-3 w-10 animate-pulse rounded bg-slate-800" /></td>
    </tr>
  );
}

export default function TimeSyncPopover({ onClose }) {
  const [data, setData] = useState(cachedData);
  const [fetchedAt, setFetchedAt] = useState(cachedAt || Date.now());
  const [loading, setLoading] = useState(!cachedData);
  const [syncing, setSyncing] = useState(false);
  const [now, setNow] = useState(Date.now());
  const autoSyncedRef = useRef(false);
  const ref = useRef(null);

  // Tick every second so displayed times advance live
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  const applyData = (d) => {
    const ts = Date.now();
    cachedData = d;
    cachedAt = ts;
    setData(d);
    setFetchedAt(ts);
  };

  const fetchData = async () => {
    setLoading(true);
    try {
      const d = await getTimeSync();
      applyData(d);

      // Auto-correct once per popover open if drift detected
      if (!d.in_sync && !autoSyncedRef.current) {
        autoSyncedRef.current = true;
        doForceSync();
      }
    } catch {
      if (!cachedData) setData(null);
    } finally {
      setLoading(false);
    }
  };

  const doForceSync = async () => {
    setSyncing(true);
    try {
      const result = await forceTimeSync();
      // force-sync returns updated time data alongside sync_results
      applyData(result);
    } catch {
      // Fall back to a plain refresh
      await fetchData();
    } finally {
      setSyncing(false);
    }
  };

  // Initial fetch + auto-refresh every 30s while open
  useEffect(() => {
    fetchData();
    const id = setInterval(fetchData, 30_000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    function handleClick(e) {
      if (ref.current && !ref.current.contains(e.target)) onClose();
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [onClose]);

  const busy = loading || syncing;

  return (
    <div
      ref={ref}
      className="absolute bottom-12 left-2 z-50 w-72 rounded-lg border border-slate-700 bg-slate-900 shadow-xl p-3"
    >
      <div className="flex items-center justify-between mb-2">
        <h4 className="text-xs font-medium text-slate-300">Cluster Time Sync</h4>
        <div className="flex items-center gap-2">
          {data && !data.in_sync && (
            <button
              onClick={doForceSync}
              disabled={busy}
              className="text-[10px] text-amber-400 hover:text-amber-300 disabled:opacity-50"
            >
              {syncing ? "Syncing..." : "Force Sync"}
            </button>
          )}
          <button
            onClick={fetchData}
            disabled={busy}
            className="text-[10px] text-indigo-400 hover:text-indigo-300 disabled:opacity-50"
          >
            {loading ? "..." : "Refresh"}
          </button>
        </div>
      </div>

      {!data && !loading && (
        <p className="text-[10px] text-rose-400">Failed to fetch time sync data</p>
      )}

      <table className="w-full text-[10px]">
        <thead>
          <tr className="text-slate-500 border-b border-slate-800">
            <th className="text-left pb-1 pr-2">VM</th>
            <th className="text-left pb-1 pr-2">Time ({_tzAbbr})</th>
            <th className="text-right pb-1">Offset</th>
          </tr>
        </thead>
        <tbody>
          {data
            ? VM_ORDER.map((vm) => {
                const node = data[vm];
                if (!node) return null;
                return (
                  <tr key={vm} className={`border-b border-slate-800/50 ${busy ? "opacity-50" : ""}`}>
                    <td className="py-1 pr-2 font-mono text-slate-300">{vm}</td>
                    <td className="py-1 pr-2 font-mono text-slate-400">
                      {node.reachable ? liveTime(node.time_utc, fetchedAt, now) : "--"}
                    </td>
                    <td className="py-1 text-right">
                      <OffsetBadge offset={node.offset_ms} reachable={node.reachable} />
                    </td>
                  </tr>
                );
              })
            : VM_ORDER.map((vm) => <SkeletonRow key={vm} />)
          }
        </tbody>
      </table>

      {data && (
        <div className={`mt-2 flex items-center justify-between ${busy ? "opacity-50" : ""}`}>
          <span className="text-[10px] text-slate-500">
            Max drift: <span className="font-mono">{data.max_drift_ms}ms</span>
          </span>
          <span className={`rounded px-1.5 py-0.5 text-[9px] font-bold uppercase ${
            data.in_sync
              ? "bg-emerald-900/40 text-emerald-400"
              : "bg-rose-900/40 text-rose-400"
          }`}>
            {data.in_sync ? "in sync" : "drift detected"}
          </span>
        </div>
      )}
    </div>
  );
}
