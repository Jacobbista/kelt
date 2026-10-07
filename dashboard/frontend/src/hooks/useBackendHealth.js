import { useCallback, useEffect, useRef, useState } from "react";
import { setBackendDown } from "../lib/backendState";

const POLL_INTERVAL_MS = 5000;
// Down: retry fast at first, then every 5 s (each failed probe is a red line in
// the browser console). check() can always be called for an immediate probe.
const RETRY_WHEN_DOWN_MS = [2000, 2000, 3000, 5000];
const FETCH_TIMEOUT_MS = 5000;

/**
 * Polls /health and tracks backend reachability.
 * Uses AbortController + timeout so we never hang on a stuck backend.
 * When down: unreachable=true, retries every 2s.
 * When back up: unreachable=false.
 * state: "unknown" until the first answer, then "up", "down", or
 * "access-expired" (a perimeter gate redirects to its login): AuthGate waits on it.
 */
export function useBackendHealth() {
  const [unreachable, setUnreachable] = useState(false);
  const [sessionExpired, setSessionExpired] = useState(false);
  const [serverTime, setServerTime] = useState(null);
  const [answered, setAnswered] = useState(false);
  const fails = useRef(0);

  const check = useCallback(async () => {
    const ctrl = new AbortController();
    const to = setTimeout(() => ctrl.abort(), FETCH_TIMEOUT_MS);
    try {
      const res = await fetch(`/health`, {
        method: "GET",
        signal: ctrl.signal,
        // redirect: "manual" so a perimeter gate (e.g. Cloudflare Access)
        // bouncing us to its login page returns an opaqueredirect instead of
        // a CORS failure we would misread as "backend down".
        redirect: "manual",
      });
      clearTimeout(to);
      if (res.type === "opaqueredirect" || res.status === 0) {
        fails.current += 1;
        setSessionExpired(true);
        setUnreachable(true);
        setAnswered(true);
        return false;
      }
      if (res.ok) {
        fails.current = 0;
        setBackendDown(false);
        setUnreachable(false);
        setSessionExpired(false);
        setAnswered(true);
        try {
          const data = await res.json();
          if (data.server_time_utc) setServerTime(data.server_time_utc);
        } catch { /* ignore parse errors */ }
        return true;
      }
    } catch {
      clearTimeout(to);
    }
    fails.current += 1;
    setBackendDown(true);
    setUnreachable(true);
    setAnswered(true);
    return false;
  }, []);

  useEffect(() => {
    let timer;
    let alive = true;
    const loop = async () => {
      const up = await check();
      if (!alive) return;
      const ms = up ? POLL_INTERVAL_MS : RETRY_WHEN_DOWN_MS[Math.min(fails.current, RETRY_WHEN_DOWN_MS.length) - 1];
      timer = setTimeout(loop, ms);
    };
    loop();
    return () => { alive = false; clearTimeout(timer); };
  }, [check]);

  const state = !answered ? "unknown" : sessionExpired ? "access-expired" : unreachable ? "down" : "up";
  return { state, unreachable, sessionExpired, check, serverTime };
}
