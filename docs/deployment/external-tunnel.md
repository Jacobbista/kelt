# External Tunnel / Reverse Proxy

The testbed is self-contained on LAN, but the dashboard, Keycloak, and demo apps can be exposed externally via a tunnel, reverse proxy, or Zero-Trust gateway (Cloudflare Tunnel, Tailscale Funnel, ngrok, nginx + certbot, Caddy, Traefik, etc.).

This document lists the HTTP paths that must pass through unaltered for the dashboard and IAM to work end-to-end, and provides a Cloudflare Zero-Trust example.

---

## Required passthrough paths

Any upstream layer between the browser and the cluster must forward the following paths without auth interception, body rewriting, or WebSocket-upgrade stripping:

| Path | Reason |
|------|--------|
| `/auth/realms/<realm>/*` | Keycloak OIDC discovery, login form POST, token endpoint, JWKS. POST form actions render `KC_HOSTNAME`-pinned URLs, so an external auth layer that intercepts the form submission causes `cookie_not_found` (400). |
| `/auth/resources/*` | Keycloak login theme static assets (JS, CSS, fonts). The login HTML loads these from the browser origin; interception triggers cross-origin redirect to the auth gateway and CORS blocks the script load. |
| `/api/v1/ws/*` | Dashboard WebSocket endpoints (pod logs, sniffer, exec, traffic intensity). Browser WebSocket handshakes cannot carry custom headers, so the JWT travels as `?access_token=<jwt>` query string. An external auth layer that intercepts the upgrade replaces the handshake with a 302 to its own login page, breaking the connection. |
| `/api/*` (REST) | Bearer token already validates the caller; an additional auth layer adds latency without adding security. Safe to bypass when JWT validation is enforced server-side. |

Paths that should remain protected by the external auth layer:

| Path | Reason |
|------|--------|
| `/auth/admin/*` | Keycloak admin console. Direct access bypasses the dashboard role model; defense-in-depth via the gateway is appropriate. |
| `/` (SPA root) | The SPA enforces OIDC client-side, but a gateway block prevents anonymous SPA load. |

---

## Cloudflare Zero-Trust example

Cloudflare Tunnel exposes the dashboard hostname (`core.example.com`) over an outbound tunnel from the operator host. Cloudflare Access then sits in front and enforces an identity policy on every request.

### Tunnel config

`cloudflared` config on the operator host (`~/.cloudflared/config.yml`):

```yaml
tunnel: <tunnel-uuid>
credentials-file: /home/operator/.cloudflared/<tunnel-uuid>.json
ingress:
  - hostname: core.example.com
    service: http://192.168.56.11:31573
  - service: http_status:404
```

`192.168.56.11:31573` is the cluster dashboard NodePort on the worker VM.

WebSocket forwarding is on by default in `cloudflared`; no `disableChunkedEncoding` or extra flag needed.

### Access policies

In the Cloudflare Zero-Trust dashboard (Access controls → Applications), create one Self-hosted Access application per bypass path, plus the catch-all that protects the SPA. Cloudflare matches the most specific path first, so the three bypass apps below take precedence over the catch-all.

Each bypass application uses a single policy with **Action: Bypass** and **Include: Everyone** (not "Service Token only", which would block browser users).

**App 1. Bypass Keycloak realm endpoints**

| Field | Value |
|-------|-------|
| Application name | `keycloak-realm-bypass` |
| Path | `/auth/realms/*` |
| Policy action | `Bypass` |

**App 2 — Bypass Keycloak theme assets**

| Field | Value |
|-------|-------|
| Application name | `keycloak-resources-bypass` |
| Path | `/auth/resources/*` |
| Policy action | `Bypass` |

**App 3 — Bypass dashboard WebSockets**

| Field | Value |
|-------|-------|
| Application name | `dashboard-ws-bypass` |
| Path | `/api/v1/ws/*` |
| Policy action | `Bypass` |

Optionally, **App 4 — Bypass dashboard REST**:

| Field | Value |
|-------|-------|
| Application name | `dashboard-api-bypass` |
| Path | `/api/*` |
| Policy action | `Bypass` |

Skipping App 4 keeps the REST API behind Access too. Backend JWT validation still runs in either case.

**App 5 — Protect everything else**

| Field | Value |
|-------|-------|
| Application name | `dashboard-spa` |
| Path | `/*` |
| Policy action | `Allow` |
| Include rule | `Emails ending in: example.com` (or whatever identity policy applies) |

Cloudflare evaluates apps in order from most specific to least specific path, so the bypass apps match before the catch-all.

### Dev frontend hostname (optional)

Exposing the Vite dev frontend (`dev.example.com`) through a second tunnel hostname requires its own bypass set. The dev frontend uses absolute Keycloak authority pointing at the prod hostname (`core.example.com`), so Keycloak paths do NOT need bypass on the dev hostname; only the backend proxy paths served by Vite do.

`cloudflared` ingress entry:

```yaml
  - hostname: dev.example.com
    service: http://192.168.56.13:31573    # Vite dev server on the ansible VM
```

Then create on the dev hostname:

**App D1 — Bypass dashboard backend through Vite**

| Field | Value |
|-------|-------|
| Application name | `dev-api-bypass` |
| Path | `dev.example.com/api/*` |
| Policy action | `Bypass` + `Everyone` |

Covers REST + `/api/v1/ws/*` WebSocket endpoints. Backend Bearer JWT validation still runs.

**App D2 — Bypass health probe**

| Field | Value |
|-------|-------|
| Application name | `dev-health-bypass` |
| Path | `dev.example.com/health` |
| Policy action | `Bypass` + `Everyone` |

`SystemHealthGate` polls `/health` before the SPA hydrates; an Access challenge here keeps the splash screen up forever.

**App D3 — Bypass watchdog**

| Field | Value |
|-------|-------|
| Application name | `dev-watchdog-bypass` |
| Path | `dev.example.com/watchdog/*` |
| Policy action | `Bypass` + `Everyone` |

Watchdog uses an `X-Watchdog-Token` header issued by the backend; Access would strip the upgrade and add nothing.

**App D4 — Bypass Vite HMR WebSocket** (only when HMR is wanted)

| Field | Value |
|-------|-------|
| Application name | `dev-hmr-bypass` |
| Path | `dev.example.com/__vite_hmr*` |
| Policy action | `Bypass` + `Everyone` |

Vite listens for HMR upgrades on `/__vite_hmr` by default (set in `dashboard_dev_hmr_path`), so this single bypass is the only Access change required. Without the bypass the WebSocket upgrade is intercepted, the page enters a reload loop, and HMR is unusable. To opt out of HMR entirely instead, set `DASHBOARD_DEV_HMR_ENABLED=false` and skip App D4.

**App D5 — Protect the dev SPA root**

| Field | Value |
|-------|-------|
| Application name | `dev-spa` |
| Path | `dev.example.com/*` |
| Policy action | `Allow` |
| Include rule | (your identity policy) |

The four bypass apps above match more specific paths than `/*`, so Cloudflare evaluates them first.

### Cloudflare Bot Fight Mode

Bot Fight Mode rejects `curl` and other non-browser clients by JA3 fingerprint, returning TLS reset (`Recv failure: Connection reset by peer`). Disable Bot Fight Mode on the hostname or use the browser DevTools Network panel for diagnostics; terminal `curl` will not work for end-to-end testing through the tunnel.

---

## Other tunnel providers

The same passthrough requirements apply:

- **nginx reverse proxy**: ensure `proxy_set_header Upgrade $http_upgrade` and `proxy_set_header Connection "upgrade"` on `/api/v1/ws/`. No auth in front of `/auth/realms/`, `/auth/resources/`, `/api/v1/ws/`.
- **Caddy**: `reverse_proxy` handles WebSocket automatically. No `forward_auth` directive on the listed paths.
- **Traefik**: no `Middleware` chain with auth on the listed paths.
- **Tailscale Funnel**: passes all paths transparently; no Access layer to configure.

---

## Diagnostic signals

| Symptom | Likely cause |
|---------|--------------|
| Login form POST returns 400 with `cookie_not_found` | `/auth/realms/*` intercepted; form action target differs from cookie domain. |
| Browser console: CORS error on `/auth/resources/.../js/passwordVisibility.js` | `/auth/resources/*` not bypassed; gateway 302s to its own origin. |
| Browser console: `WebSocket connection to 'wss://.../api/v1/ws/...' failed` | `/api/v1/ws/*` not bypassed; gateway rejects upgrade. |
| Periodic dashboard reload, `SystemHealthGate` splash flashes | Vite HMR WebSocket dropped by gateway. Set `DASHBOARD_DEV_HMR_PATH=/__vite_hmr` and add a bypass for that path, or disable HMR with `DASHBOARD_DEV_HMR_ENABLED=false`. |
| `SystemHealthGate` stuck on "Waiting for services to come online" on the dev hostname | `/health` not bypassed on the dev hostname. The fetch hangs at the Access challenge and never resolves. |
| Logout redirects back to the dashboard logged in | Two causes. Keycloak SSO session on the prod hostname is still alive across tabs (close them and retry), or the `dashboard` client has no `post.logout.redirect.uris` and the SPA `post_logout_redirect_uri` does not match `redirectUris` exactly. Phase 08 realm template sets the attribute; re-apply the playbook after changing dev/prod hostnames. |

---

## Related

- [Dashboard architecture](../dashboard/) — frontend cluster pod, backend on ansible VM, Keycloak on worker NodePort
- [Keycloak realm template](https://github.com/Jacobbista/5g-k3s-kubedge-testbed/blob/main/ansible/phases/08-iam/roles/keycloak_setup/templates/keycloak-realm.json.j2) — `redirectUris` and `webOrigins` must include every external origin (cluster, dev, demo)
