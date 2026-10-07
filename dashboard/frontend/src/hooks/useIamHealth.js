import { useEffect, useState } from "react";
import { AUTH_ENABLED, KEYCLOAK_AUTHORITY } from "../auth/oidc";

const RETRY_MS = 2000;
const TIMEOUT_MS = 4000;

// Whether Keycloak answers, for the gate before the shell (lib/authFlow.js):
// "unknown" | "up" | "down" | "access-expired". It probes only until Keycloak is
// up, then stops: once the shell is open the OIDC client talks to Keycloak by
// itself, and a probe every few seconds in every tab was pure load.
export function useIamHealth() {
  const [state, setState] = useState(AUTH_ENABLED ? "unknown" : "up");

  useEffect(() => {
    if (!AUTH_ENABLED) return undefined;
    let mounted = true;
    let timer;

    async function tick() {
      const ctrl = new AbortController();
      const to = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
      let next = "down";
      try {
        // redirect: "manual": a perimeter gate bouncing the probe to its login
        // page returns an opaqueredirect instead of a CORS failure.
        const res = await fetch(`${KEYCLOAK_AUTHORITY}/.well-known/openid-configuration`, {
          signal: ctrl.signal, cache: "no-store", redirect: "manual",
        });
        if (res.type === "opaqueredirect" || res.status === 0) next = "access-expired";
        else if (res.ok) next = "up";
      } catch { /* down */ }
      clearTimeout(to);
      if (!mounted) return;
      setState(next);
      if (next !== "up") timer = setTimeout(tick, RETRY_MS);
    }

    tick();
    return () => { mounted = false; clearTimeout(timer); };
  }, []);

  return state;
}
