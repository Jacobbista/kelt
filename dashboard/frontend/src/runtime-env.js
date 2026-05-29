// Read a configuration value with runtime override precedence.
//
// Order:
//   1. window.__ENV__[key]   set at deploy time via /env-config.js (ConfigMap in prod, /public in dev)
//   2. import.meta.env[key]  baked at build time from Vite .env (dev only; empty in the prod bundle)
//   3. fallback              caller-provided default
//
// Use this everywhere instead of reading import.meta.env directly so the
// production nginx image stays generic and a single ConfigMap retargets
// authority, client id, dev link, etc., without rebuilding.

const runtime = (typeof window !== "undefined" && window.__ENV__) || {};

export function env(key, fallback = "") {
  const r = runtime[key];
  if (r !== undefined && r !== null && r !== "") return r;
  const b = import.meta.env ? import.meta.env[key] : undefined;
  if (b !== undefined && b !== null && b !== "") return b;
  return fallback;
}
