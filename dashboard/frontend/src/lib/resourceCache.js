// One cache per key, shared by every component that reads it: the last value
// stays on screen while a refresh runs in the background (stale-while-
// revalidate), and requests for the same key are deduplicated. Pure, so it is
// unit-tested with node --test (resourceCache.test.js).
export function createResourceCache({ now = Date.now } = {}) {
  const entries = new Map();

  function entry(key) {
    let e = entries.get(key);
    if (!e) {
      e = { data: undefined, error: null, updatedAt: null, inflight: null, listeners: new Set(), snap: null };
      e.snap = { data: e.data, error: e.error, updatedAt: e.updatedAt, refreshing: false };
      entries.set(key, e);
    }
    return e;
  }

  function changed(e) {
    e.snap = { data: e.data, error: e.error, updatedAt: e.updatedAt, refreshing: !!e.inflight };
    for (const fn of [...e.listeners]) fn();
  }

  return {
    snapshot: (key) => entry(key).snap,
    subscribe(key, fn) {
      const e = entry(key);
      e.listeners.add(fn);
      return () => e.listeners.delete(fn);
    },
    // `after: true` does not join the request in flight: it waits for it and
    // then makes a new one, for a read that must follow an action.
    refresh(key, fetcher, { after = false } = {}) {
      const e = entry(key);
      if (e.inflight && !after) return e.inflight;
      // The request starts now (or right after the one in flight); a fetcher
      // that throws synchronously still lands in the error branch.
      const start = () => new Promise((resolve) => resolve(fetcher()));
      const p = (e.inflight ? e.inflight.then(start, start) : start())
        .then(
          (data) => { e.data = data; e.error = null; e.updatedAt = now(); },
          (error) => { e.error = error; },
        )
        .finally(() => {
          if (e.inflight !== p) return; // a newer request owns the entry
          e.inflight = null;
          changed(e);
        });
      e.inflight = p;
      changed(e);
      return p;
    },
  };
}

export const resourceCache = createResourceCache();
