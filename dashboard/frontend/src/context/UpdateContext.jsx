/**
 * Dashboard self-update: awareness, and the rollout the operator triggers.
 *
 * The awkward part is that the frontend can update ITSELF. In the cluster deploy
 * target the pod serving this page is the one being replaced, so every request
 * through the same origin fails until the new pod is ready. Left alone that
 * scatters errors across whatever pages happen to be mounted.
 *
 * So a rollout unmounts the app and shows a full-screen overlay instead. Nothing
 * else is polling, request failures become the EXPECTED state rather than an
 * error, and the page reloads once the new version reports ready (two bundle
 * generations live in one tab is what produces "Invalid hook call").
 *
 * Completion is decided by the COMPONENT STATUS, not by this page's own server
 * going away. The dev frontend is served by Vite on the ansible VM and keeps
 * serving throughout a cluster rollout, so a check based on the page dying waits
 * for something that never happens there.
 *
 * A rollout that never completes ends in a message, never a blank page or a
 * spinner with no way out.
 */
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { getDashboardComponents, updateDashboardComponent } from "../api";
import { useToast } from "./ToastContext";
import { useAuth } from "../auth/AuthContext";

const Ctx = createContext(null);

// The new pod has to pull an image and pass its probes; 3 minutes is generous
// enough for a cold pull on a slow link and short enough to not look hung.
const ROLLOUT_TIMEOUT_MS = 180_000;
const POLL_MS = 3000;

export function UpdateProvider({ children }) {
  const toast = useToast();
  const auth = useAuth();
  const [components, setComponents] = useState([]);
  const [rollout, setRollout] = useState(null); // { name, phase, since }
  const announced = useRef(false);
  // Wait for auth before touching the API. This provider wraps the routes including
  // the OIDC callback, so firing getDashboardComponents on mount hits the backend
  // before a token exists — a burst of 401s on every cold load (dev and prod), and a
  // flash before the app settles. Ready = auth off, or signed in.
  const authReady = !auth.enabled || (!auth.loading && !!auth.user);

  const refresh = useCallback(async () => {
    try {
      const list = await getDashboardComponents();
      setComponents(list || []);
      return list || [];
    } catch {
      return [];
    }
  }, []);

  const available = components.filter((c) => c.state === "update-available");

  // Announce once per page load, not per navigation: a notice that reappears on
  // every route change reads as a fault rather than information. Held until auth is
  // ready so the first call carries a token instead of 401-ing on the callback.
  useEffect(() => {
    if (!authReady) return;
    refresh().then((list) => {
      const behind = (list || []).filter((c) => c.state === "update-available");
      if (behind.length && !announced.current) {
        announced.current = true;
        const names = behind.map((c) => c.display).join(", ");
        toast.info(`Update available for ${names}. Open Manual to apply it.`, 8000);
      }
    });
  }, [authReady, refresh, toast]);

  const startUpdate = useCallback(async (name) => {
    let res = null;
    try {
      res = await updateDashboardComponent(name);
    } catch (e) {
      // In the cluster deploy target the request can be cut off by the very
      // rollout it triggered, so a network failure is not conclusive: fall
      // through and let the component status decide.
      if (!/fetch|network|load failed/i.test(e?.message || "")) {
        toast.error(`${name}: ${e?.message || "could not start the update"}`);
        return;
      }
    }
    // The backend answers "up-to-date" when there was nothing to roll. Showing a
    // progress overlay for a no-op is how it ends up waiting for something that
    // is never going to happen.
    if (res && res.status === "up-to-date") {
      toast.info(`${name} is already at ${res.version || "the latest version"}.`);
      refresh();
      return;
    }
    setRollout({ name, since: Date.now() });
  }, [refresh, toast]);

  const dismissRollout = useCallback(() => setRollout(null), []);

  return (
    <Ctx.Provider value={{ components, available, refresh, startUpdate, rollout, dismissRollout }}>
      {rollout ? <RolloutOverlay rollout={rollout} onDismiss={dismissRollout} /> : children}
    </Ctx.Provider>
  );
}

function RolloutOverlay({ rollout, onDismiss }) {
  const [state, setState] = useState("rolling");
  const [elapsed, setElapsed] = useState(0);
  const startedAt = useRef(rollout.since || Date.now());

  // Elapsed time is the difference between "working" and "stale": without it a
  // static card gives no evidence that anything is still happening.
  useEffect(() => {
    const t = setInterval(() => setElapsed(Math.floor((Date.now() - startedAt.current) / 1000)), 1000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    if (state !== "rolling") return undefined;
    let alive = true;
    const tick = async () => {
      if (!alive) return;
      if (Date.now() - startedAt.current > ROLLOUT_TIMEOUT_MS) {
        setState("timeout");
        return;
      }
      try {
        // The component status is the only signal that works for both deploy
        // targets. Watching for this page's own server to die only holds for the
        // cluster pod; the dev server keeps serving throughout, so that check
        // waited forever for something that never happens.
        const list = await getDashboardComponents();
        const c = (list || []).find((x) => x.name === rollout.name);
        if (c && c.state === "up-to-date") {
          setState("done");
          // Reload so the tab picks up the new bundle: two bundle generations in
          // one tab is what produces "Invalid hook call".
          setTimeout(() => window.location.reload(), 900);
          return;
        }
      } catch {
        // Expected while the cluster pod is being replaced: keep waiting.
      }
      if (alive) setTimeout(tick, POLL_MS);
    };
    const t = setTimeout(tick, POLL_MS);
    return () => { alive = false; clearTimeout(t); };
  }, [state, rollout.name]);

  const COPY = {
    rolling: {
      title: "Updating the dashboard",
      body: "Pulling the new image and waiting for the pod to serve. This page reloads by itself when the new version reports ready.",
    },
    done: { title: "Update applied", body: "Reloading with the new version." },
    timeout: {
      title: "The rollout did not finish in time",
      body: "The new version has not reported ready. It may still be pulling the image, or the pod may have failed to start. Check the dashboard pod, then reload.",
    },
  }[state];

  const stuck = state === "timeout";

  return (
    <div className="fixed inset-0 z-[200] flex items-center justify-center bg-slate-950 p-6">
      <div className="w-full max-w-md rounded-lg border border-slate-700 bg-slate-900 p-6">
        <div className="flex items-center gap-3">
          {state === "rolling" && (
            <span className="h-3 w-3 shrink-0 animate-pulse rounded-full bg-sky-400" />
          )}
          <h2 className="text-sm font-semibold text-slate-100">{COPY.title}</h2>
          {state === "rolling" && (
            <span className="ml-auto font-mono text-[11px] text-slate-500">{elapsed}s</span>
          )}
        </div>
        <p className="mt-2 text-xs leading-relaxed text-slate-400">{COPY.body}</p>

        <div className="mt-4 flex flex-wrap gap-2">
          {stuck && (
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="rounded bg-sky-600/20 px-3 py-1.5 text-xs font-medium text-sky-300 hover:bg-sky-600/30"
            >
              reload anyway
            </button>
          )}
          {/* Always available: a progress screen with no way out is a trap even
              when the operation is still legitimately running. */}
          <button
            type="button"
            onClick={onDismiss}
            className="rounded bg-slate-700/60 px-3 py-1.5 text-xs font-medium text-slate-300 hover:bg-slate-700"
          >
            back to the dashboard
          </button>
        </div>
      </div>
    </div>
  );
}

export function useUpdates() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useUpdates must be used inside UpdateProvider");
  return ctx;
}
