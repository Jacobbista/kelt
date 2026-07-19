/**
 * Dashboard self-update: awareness, and the rollout the operator triggers.
 *
 * The awkward part is that the frontend updates ITSELF. The pod serving this
 * page is the one being replaced, so during the rollout every request through
 * the same origin fails until the new pod is ready. Left alone that produces a
 * scatter of errors across whatever pages happen to be mounted.
 *
 * So a rollout unmounts the app and shows a full-screen overlay instead. Nothing
 * else is polling, request failures are the EXPECTED state rather than an error,
 * and when the backend answers again the page reloads so the new bundle replaces
 * the old one (two bundle generations live in one tab is what produces "Invalid
 * hook call"). A rollout that never completes ends in a message, never a blank
 * page or a spinner that spins forever.
 */
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { getDashboardComponents, updateDashboardComponent } from "../api";
import { useToast } from "./ToastContext";

const Ctx = createContext(null);

// The new pod has to pull an image and pass its probes; 3 minutes is generous
// enough for a cold pull on a slow link and short enough to not look hung.
const ROLLOUT_TIMEOUT_MS = 180_000;
const POLL_MS = 3000;

export function UpdateProvider({ children }) {
  const toast = useToast();
  const [components, setComponents] = useState([]);
  const [rollout, setRollout] = useState(null); // { name, phase, since }
  const announced = useRef(false);

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
  // every route change reads as a fault rather than information.
  useEffect(() => {
    refresh().then((list) => {
      const behind = (list || []).filter((c) => c.state === "update-available");
      if (behind.length && !announced.current) {
        announced.current = true;
        const names = behind.map((c) => c.display).join(", ");
        toast.info(`Update available for ${names}. Open Manual to apply it.`, 8000);
      }
    });
  }, [refresh, toast]);

  const startUpdate = useCallback(async (name) => {
    setRollout({ name, phase: "starting", since: Date.now() });
    try {
      await updateDashboardComponent(name);
    } catch (e) {
      // The request itself can be cut off by the very rollout it triggered, so a
      // failure here is not conclusive: fall through to polling and let the
      // cluster state decide.
      if (!/fetch|network|load failed/i.test(e?.message || "")) {
        setRollout({ name, phase: "failed", error: e?.message || "Could not start the update" });
        return;
      }
    }
    setRollout({ name, phase: "rolling", since: Date.now() });
  }, []);

  const dismissRollout = useCallback(() => setRollout(null), []);

  return (
    <Ctx.Provider value={{ components, available, refresh, startUpdate, rollout, dismissRollout }}>
      {rollout ? <RolloutOverlay rollout={rollout} onDismiss={dismissRollout} /> : children}
    </Ctx.Provider>
  );
}

function RolloutOverlay({ rollout, onDismiss }) {
  const [state, setState] = useState(rollout.phase === "failed" ? "failed" : "rolling");
  const [wentDown, setWentDown] = useState(false);
  const startedAt = useRef(rollout.since || Date.now());

  useEffect(() => {
    if (state === "failed") return undefined;
    let alive = true;
    const tick = async () => {
      if (!alive) return;
      if (Date.now() - startedAt.current > ROLLOUT_TIMEOUT_MS) {
        setState("timeout");
        return;
      }
      try {
        // Same-origin, so this only answers once the new pod is serving.
        const res = await fetch("/health", { cache: "no-store" });
        if (!res.ok) throw new Error(String(res.status));
        // Only treat a success as "done" after the old pod actually went away.
        // Otherwise the first poll lands on the pod that is still terminating
        // and the page reloads into the old bundle.
        if (wentDown) {
          setState("done");
          setTimeout(() => window.location.reload(), 800);
          return;
        }
      } catch {
        if (!wentDown) setWentDown(true);
      }
      if (alive) setTimeout(tick, POLL_MS);
    };
    const t = setTimeout(tick, POLL_MS);
    return () => { alive = false; clearTimeout(t); };
  }, [state, wentDown]);

  const COPY = {
    rolling: {
      title: "Updating the dashboard",
      body: wentDown
        ? "The old pod has stopped. Waiting for the new one to serve."
        : "Rolling out the new image. This page will reload by itself when it is ready.",
    },
    done: { title: "Update applied", body: "Reloading with the new version." },
    timeout: {
      title: "The rollout did not finish in time",
      body: "The new pod has not started serving. It may still be pulling the image, or it may have failed to start. Check the dashboard pod, then reload.",
    },
    failed: {
      title: "Could not start the update",
      body: rollout.error || "The update request was refused.",
    },
  }[state];

  const stuck = state === "timeout" || state === "failed";

  return (
    <div className="fixed inset-0 z-[200] flex items-center justify-center bg-slate-950 p-6">
      <div className="w-full max-w-md rounded-lg border border-slate-700 bg-slate-900 p-6">
        <div className="flex items-center gap-3">
          {!stuck && state !== "done" && (
            <span className="h-3 w-3 shrink-0 animate-pulse rounded-full bg-sky-400" />
          )}
          <h2 className="text-sm font-semibold text-slate-100">{COPY.title}</h2>
        </div>
        <p className="mt-2 text-xs leading-relaxed text-slate-400">{COPY.body}</p>

        {stuck && (
          <div className="mt-4 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="rounded bg-sky-600/20 px-3 py-1.5 text-xs font-medium text-sky-300 hover:bg-sky-600/30"
            >
              reload anyway
            </button>
            <button
              type="button"
              onClick={onDismiss}
              className="rounded bg-slate-700/60 px-3 py-1.5 text-xs font-medium text-slate-300 hover:bg-slate-700"
            >
              back to the dashboard
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

export function useUpdates() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useUpdates must be used inside UpdateProvider");
  return ctx;
}
