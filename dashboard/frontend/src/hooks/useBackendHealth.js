import { useCallback, useEffect, useState } from "react";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8080";
const POLL_INTERVAL_MS = 5000;
const RETRY_WHEN_DOWN_MS = 2000;
const FETCH_TIMEOUT_MS = 5000;

/**
 * Polls /health and tracks backend reachability.
 * Uses AbortController + timeout so we never hang on a stuck backend.
 * When down: unreachable=true, retries every 2s.
 * When back up: unreachable=false.
 */
export function useBackendHealth() {
  const [unreachable, setUnreachable] = useState(false);
  const [serverTime, setServerTime] = useState(null);

  const check = useCallback(async () => {
    const ctrl = new AbortController();
    const to = setTimeout(() => ctrl.abort(), FETCH_TIMEOUT_MS);
    try {
      const res = await fetch(`${API_BASE}/health`, {
        method: "GET",
        signal: ctrl.signal,
      });
      clearTimeout(to);
      if (res.ok) {
        setUnreachable(false);
        try {
          const data = await res.json();
          if (data.server_time_utc) setServerTime(data.server_time_utc);
        } catch { /* ignore parse errors */ }
        return true;
      }
    } catch (err) {
      clearTimeout(to);
      console.warn("[dashboard] Backend unreachable:", err?.message || err, new Date().toISOString());
    }
    setUnreachable(true);
    return false;
  }, []);

  useEffect(() => {
    check();
    const ms = unreachable ? RETRY_WHEN_DOWN_MS : POLL_INTERVAL_MS;
    const id = setInterval(check, ms);
    return () => clearInterval(id);
  }, [check, unreachable]);

  return { unreachable, check, serverTime };
}
