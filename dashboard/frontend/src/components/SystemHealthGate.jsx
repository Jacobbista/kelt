import { useEffect, useState } from "react";
import { AUTH_ENABLED, KEYCLOAK_AUTHORITY } from "../auth/oidc";

const POLL_DOWN_MS = 2000;
const POLL_UP_MS = 8000;
const TIMEOUT_MS = 4000;

function fetchWithTimeout(url, ms) {
  const ctrl = new AbortController();
  const to = setTimeout(() => ctrl.abort(), ms);
  return fetch(url, { signal: ctrl.signal, cache: "no-store" })
    .finally(() => clearTimeout(to));
}

async function probe(url) {
  try {
    const res = await fetchWithTimeout(url, TIMEOUT_MS);
    return res.ok;
  } catch {
    return false;
  }
}

// Blocks rendering until the dashboard backend and (when auth is enabled)
// Keycloak respond healthy. Avoids the broken UI a user sees on a cold
// reboot where the SPA loads from the cluster pod but the backend or IAM
// pods are still starting.
export default function SystemHealthGate({ children }) {
  const [backendUp, setBackendUp] = useState(false);
  const [iamUp, setIamUp] = useState(!AUTH_ENABLED);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let mounted = true;
    let timer;

    async function tick() {
      const backend = probe("/health");
      const iam = AUTH_ENABLED
        ? probe(`${KEYCLOAK_AUTHORITY}/.well-known/openid-configuration`)
        : Promise.resolve(true);
      const [b, i] = await Promise.all([backend, iam]);
      if (!mounted) return;
      setBackendUp(b);
      setIamUp(i);
      const ok = b && i;
      if (ok) setReady(true);
      timer = setTimeout(tick, ok ? POLL_UP_MS : POLL_DOWN_MS);
    }

    tick();
    return () => {
      mounted = false;
      clearTimeout(timer);
    };
  }, []);

  if (ready) return children;

  return (
    <div className="health-gate">
      <div className="health-gate__card">
        <div className="health-gate__spinner" aria-hidden="true" />
        <h1 className="health-gate__title">5G Testbed</h1>
        <p className="health-gate__subtitle">Waiting for services to come online…</p>
        <ul className="health-gate__list">
          <li className={backendUp ? "ok" : "wait"}>
            <span className="dot" /> Dashboard API
            <span className="status">{backendUp ? "ready" : "starting"}</span>
          </li>
          <li className={iamUp ? "ok" : "wait"}>
            <span className="dot" /> Identity (Keycloak)
            <span className="status">{iamUp ? "ready" : "starting"}</span>
          </li>
        </ul>
        <p className="health-gate__hint">
          First boot after a reset may take up to 2 minutes while pods start.
        </p>
      </div>
    </div>
  );
}
