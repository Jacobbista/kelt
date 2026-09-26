import React, { createContext, useContext, useEffect, useState } from "react";
import { getIsolationPlanes } from "../api";
import { useAuth } from "../auth/AuthContext";

// Blocked plane crossings over the last 24 h, for the sidebar badge and the
// Overview card: one poll a minute for both. Waits for a session: the shell is
// mounted on the login callback and the logged-out page too, and a 401 there
// would start a new sign-in redirect.
const Ctx = createContext({ loading: true, available: false });

export function IsolationSummaryProvider({ children }) {
  const auth = useAuth();
  const authReady = !auth.enabled || (!auth.loading && !!auth.user);
  const [value, setValue] = useState({ loading: true, available: false });

  useEffect(() => {
    if (!authReady) return undefined;
    let alive = true;
    const load = async () => {
      try {
        const p = await getIsolationPlanes("24h");
        if (alive) setValue(p.available
          ? { loading: false, available: true, blocked: p.blocked_total, mode: p.mode }
          : { loading: false, available: false, reason: p.reason });
      } catch (e) {
        if (alive) setValue({ loading: false, available: false, reason: e.message || "request failed" });
      }
    };
    load();
    const timer = setInterval(load, 60000);
    return () => { alive = false; clearInterval(timer); };
  }, [authReady]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export const useIsolationSummary = () => useContext(Ctx);
