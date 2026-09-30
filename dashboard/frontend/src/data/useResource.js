import { useCallback, useEffect, useRef, useSyncExternalStore } from "react";
import { resourceCache } from "../lib/resourceCache";

// Read a resource from the shared cache: what it had is returned at once, a
// refresh runs in the background, polling pauses while the tab is hidden and
// catches up when it comes back. Pages do not keep their own loading state for
// data they already have.
export default function useResource(key, fetcher, { every } = {}) {
  const snap = useSyncExternalStore(
    useCallback((cb) => resourceCache.subscribe(key, cb), [key]),
    () => resourceCache.snapshot(key),
  );
  const fetchRef = useRef(fetcher);
  fetchRef.current = fetcher;
  const refresh = useCallback((opts) => resourceCache.refresh(key, () => fetchRef.current(), opts), [key]);

  useEffect(() => {
    refresh();
    if (!every) return undefined;
    const tick = () => { if (document.visibilityState === "visible") refresh(); };
    const id = setInterval(tick, every);
    document.addEventListener("visibilitychange", tick);
    return () => { clearInterval(id); document.removeEventListener("visibilitychange", tick); };
  }, [every, refresh]);

  return { ...snap, refresh };
}
