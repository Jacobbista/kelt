// Runtime environment overrides for the dashboard frontend.
// Loaded synchronously before the React bundle so window.__ENV__ exists when
// the app boots. In production this file is replaced by a ConfigMap mounted
// at /usr/share/nginx/html/env-config.js. Empty values fall back to the
// build-time import.meta.env that Vite injected from the .env file.
window.__ENV__ = {
  VITE_AUTH_ENABLED: "",
  VITE_KEYCLOAK_AUTHORITY: "",
  VITE_KEYCLOAK_CLIENT_ID: "",
  DASHBOARD_DEV_EXTERNAL_URL: ""
};
