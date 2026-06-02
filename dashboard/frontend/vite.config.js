import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Reverse-proxy targets. Both default to localhost since Vite runs on the
// same node as the backend + watchdog. Overridden via .env templated by
// ansible phase 08.
const backendTarget  = process.env.VITE_BACKEND_TARGET  || "http://127.0.0.1:8080";
const watchdogTarget = process.env.VITE_WATCHDOG_TARGET || "http://127.0.0.1:31881";
const keycloakTarget = process.env.VITE_KEYCLOAK_PROXY_TARGET || "";

const proxy = {
  "/api":      { target: backendTarget,  changeOrigin: true, ws: true },
  "/health":   { target: backendTarget,  changeOrigin: true },
  // Watchdog is a separate tiny HTTP server that restarts the backend
  // via systemd — see dashboard/backend/watchdog.py.
  "/watchdog": { target: watchdogTarget, changeOrigin: true, rewrite: (p) => p.replace(/^\/watchdog/, "") },
};

if (keycloakTarget) {
  // Keep original Host and forward proto/host headers so Keycloak does not
  // rewrite browser redirects toward the internal NodePort/IP.
  proxy["^/auth/(?!callback).*"] = { target: keycloakTarget, changeOrigin: false, xfwd: true };
}

// Comma-separated list via VITE_ALLOWED_HOSTS, or `true` to accept any.
// Vite dev/preview block unrecognized Host headers by default — needed when
// fronted by a Cloudflare tunnel or other reverse proxy with a custom hostname.
const allowedHosts = process.env.VITE_ALLOWED_HOSTS
  ? process.env.VITE_ALLOWED_HOSTS.split(",")
  : true;

// HMR through a public tunnel needs the browser to reach the dev server
// over wss on the public port. VITE_HMR_HOST should be set to the public
// hostname (for example dev.example.com) when the dev frontend is
// fronted by a reverse proxy. Falls back to Vite defaults for direct
// LAN access where the public host equals the listener.
//
// VITE_HMR_DISABLED=1 fully disables the HMR client. Use when the proxy
// in front of the dev server does not pass through WebSocket upgrades
// (some Zero-Trust configurations). The dev server still works, only
// auto-reload on file change is lost.
const hmr = process.env.VITE_HMR_DISABLED === "1"
  ? false
  : process.env.VITE_HMR_HOST
    ? {
        host: process.env.VITE_HMR_HOST,
        protocol: process.env.VITE_HMR_PROTOCOL || "wss",
        clientPort: Number(process.env.VITE_HMR_CLIENT_PORT || 443),
        // VITE_HMR_PATH moves the HMR WebSocket off the root path so an
        // upstream Zero-Trust policy can bypass auth for just that path
        // (root usually carries the SPA auth gate). Leave unset to use
        // Vite default of "/".
        ...(process.env.VITE_HMR_PATH ? { path: process.env.VITE_HMR_PATH } : {}),
      }
    : undefined;

export default defineConfig({
  plugins: [react()],
  resolve: {
    // Ensure a single React instance in all dependency graphs.
    dedupe: ["react", "react-dom"],
  },
  optimizeDeps: {
    include: ["react", "react-dom"],
  },
  server: {
    host: "0.0.0.0",
    port: 5173,
    allowedHosts,
    proxy,
    hmr,
    watch: {
      usePolling: true,
      interval: 1000,
    },
  },
  preview: {
    host: "0.0.0.0",
    port: 5173,
    allowedHosts,
    proxy,
  },
});
