import React, { createContext, useContext, useEffect, useState } from "react";
import { getStatusSummary } from "../api";
import { useAuth } from "../auth/AuthContext";

// Overall state for the header's status pill (nodes, 5G pods, the last network
// check run, the AMF CNI alert; see app/services/status_service.py). Polled
// every 30 s once there is a session, for the same reason as the isolation summary.
const Ctx = createContext({ loading: true });

export function StatusSummaryProvider({ children }) {
  const auth = useAuth();
  const authReady = !auth.enabled || (!auth.loading && !!auth.user);
  const [value, setValue] = useState({ loading: true });

  useEffect(() => {
    if (!authReady) return undefined;
    let alive = true;
    const load = async () => {
      try {
        const s = await getStatusSummary();
        if (alive) setValue({ loading: false, state: s.state, problems: s.problems || [] });
      } catch (e) {
        if (alive) setValue({ loading: false, state: "unknown", problems: [], error: e.message || "request failed" });
      }
    };
    load();
    const timer = setInterval(load, 30000);
    return () => { alive = false; clearInterval(timer); };
  }, [authReady]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export const useStatusSummary = () => useContext(Ctx);
