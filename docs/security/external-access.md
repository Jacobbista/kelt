# External Access

The testbed binds all interactive services to RFC1918 addresses on the lab
network. Exposing them to the public internet is out of scope for the
provisioning code and stays on the operator side. This document describes the
moving parts that any external-access setup must align with, regardless of
which tunnel technology is chosen.

## What needs an external entry point

Four HTTP surfaces carry external traffic. Each may live on its own
hostname when a tunnel is in front of the lab, or stay on the worker
NodePort for LAN-only operation:

| Service | Default LAN URL | Phase | Audience | Notes |
|---------|-----------------|-------|----------|-------|
| Dashboard frontend (prod) | `http://<worker>:31573` | 09 | Operators | Reverse-proxies `/api`, `/health`, `/watchdog` to the backend, `/auth` to Keycloak. Single origin for browser, API, and WebSockets. |
| Dashboard frontend (dev, opt-in) | `http://<control>:31573` | 09 | Frontend developers | Vite HMR on the ansible VM. Started on demand from the prod sidebar widget. |
| CAMARA Gateway | `http://<worker>:31920` | 10 | M2M API consumers | Stateless gateway, OAuth2 client_credentials. No browser session. |
| Positioning Demo | `http://<worker>:31940` | 12 | End users | Browser app with PKCE login against Keycloak. |

Keycloak itself (`http://<worker>:31910`) is reachable only when one of the
above services needs to redirect a user agent to the login screen. Two layout
options are supported by phase 08:

- **Path-prefix layout** (`keycloak_path_prefix: "/auth"`): a single external
  origin serves both the dashboard and Keycloak. The dashboard frontend
  reverse-proxies `/auth/*` to the Keycloak service. Keycloak runs with
  `KC_HTTP_RELATIVE_PATH` set so the issuer, login, and JWKS URLs include
  the prefix. Default: `""` (root, separate-origin layout).
- **Subdomain layout** (`keycloak_path_prefix: ""`): Keycloak runs at the
  root of its own external hostname. Frontend redirects directly to that
  hostname. Requires a separate DNS record and tunnel entry per environment.

The path-prefix layout reduces the number of external hostnames to one and
removes a class of CORS, mixed-content, and Private Network Access (PNA)
issues. It is the recommended default when fronting the lab with a tunnel.

## Variables to override at deploy time

External hostnames are not hardcoded. The realm template and frontend env
read the following Ansible variables:

| Variable | Default | Purpose |
|----------|---------|---------|
| `dashboard_external_origin` | `http://<worker-ip>:31573` | OIDC redirect URI and Web Origin for the `dashboard` client. Also used as `KC_HOSTNAME` so Keycloak emits browser-coherent URLs behind a proxy. |
| `dashboard_dev_external_url` | `""` | Optional second origin for the opt-in Vite dev frontend. When set, added to the `dashboard` client allow lists. |
| `camara_gateway_external_origin` | `http://<worker-ip>:31920` | Advertised in the gateway's OpenAPI `servers` block and in any absolute URL the gateway emits. Operator routes the chosen hostname to the worker NodePort. |
| `positioning_demo_external_origin` | `http://<worker-ip>:31940` | OIDC redirect URI and Web Origin for the `positioning-demo` client. |
| `keycloak_path_prefix` | `""` | Path under which Keycloak is served (e.g. `"/auth"` for single-origin layout). |
| `keycloak_admin_password` | `changeme-admin` | Keycloak master admin. Replace before any non-lab deploy. |
| `keycloak_db_password` | `changeme-db` | PostgreSQL backing-store password. |
| `camara_client_secret` | `changeme-camara` | Secret of the `camara-gateway` confidential client. |
| `dashboard_readonly_secret` | `changeme-readonly` | Secret of the `dashboard-readonly` confidential client. |

Override individually with `-e key=value`, through `ansible-vault`, or via the
interactive helper:

```bash
./testbed-config auth-network preset-cloudflare example.com
```

The preset writes the sub-domain convention below into `.testbed.env`,
which subsequent `run-phase` and `provision` invocations source
automatically. Direct invocation for ad-hoc tuning:

```bash
ansible-playbook ansible/phases/08-iam/playbook.yml \
  -e dashboard_external_origin=https://core.example.com \
  -e dashboard_dev_external_url=https://dev.example.com \
  -e camara_gateway_external_origin=https://api.example.com \
  -e positioning_demo_external_origin=https://demo.example.com \
  -e keycloak_path_prefix=/auth \
  -e keycloak_admin_password='<strong-secret>' \
  -e keycloak_db_password='<strong-secret>' \
  -e camara_client_secret='<strong-secret>' \
  -e dashboard_readonly_secret='<strong-secret>'
```

## Sub-domain convention

The `auth-network preset-cloudflare <root-domain>` helper derives one
sub-domain per public surface. The same convention applies to any tunnel
or reverse-proxy provider; only the routing layer changes.

| Hostname | Routes to | Audience |
|----------|-----------|----------|
| `core.<root>` | worker NodePort `31573` | Operators (prod dashboard) |
| `dev.<root>` | ansible VM port `31573` | Frontend developers (opt-in) |
| `api.<root>` | worker NodePort `31920` | M2M CAMARA API consumers |
| `demo.<root>` | worker NodePort `31940` | End users (positioning demo) |

Keycloak is reachable under `core.<root>/auth/` via the dashboard
frontend reverse proxy. No separate `auth.<root>` hostname is required
when `keycloak_path_prefix=/auth`.

## Tunnel-agnostic checklist

Whatever tunnel provider is used (Cloudflare Tunnel, ngrok, frp, Tailscale,
headscale, WireGuard + reverse proxy, etc.), the external-access path must
satisfy the following:

1. **One external hostname per public origin.** Mixing the dashboard and
   Keycloak under separate hostnames is supported but requires a second
   tunnel route and DNS record. The path-prefix layout collapses both into
   one hostname.
2. **HTTPS termination at the edge.** Browser PKCE flows require HTTPS for
   the realm to accept the redirect. The internal lab traffic can remain
   HTTP because the tunnel terminates TLS upstream.
3. **WebSocket upgrade allowed.** The dashboard streams logs, packet captures,
   and pod exec over `wss://`. Tunnel must forward `Upgrade: websocket`.
4. **HTTP Host header preserved.** Vite blocks unknown Host headers by
   default; the dashboard sets `allowedHosts` permissively in dev, but
   prod-style serving (nginx in front of the bundle) should validate
   `Host` itself.
5. **No exposure of NodePorts beyond the dashboard origin.** The backend
   (`:31880`), the watchdog (`:31881`), and Keycloak (`:31910`) must not be
   reachable directly from outside; all traffic flows through the dashboard
   frontend's reverse proxy.

## Front-end gating (recommended)

A front-end identity gate at the tunnel level (Cloudflare Access, Tailscale
ACL, basic auth in nginx, etc.) is recommended as a network-perimeter filter,
even when Keycloak provides application-level RBAC. The two layers serve
distinct purposes:

| Layer | Concern | Failure mode |
|-------|---------|--------------|
| Tunnel identity gate | Only known identities can reach the origin | Compromise: attacker enumerates endpoints, runs unauthenticated requests |
| Keycloak + backend JWT middleware | Each request carries a verified role | Compromise: attacker has valid JWT, performs role-appropriate actions |

The provisioning code does not configure either layer; both stay on the
operator side because the choice depends on the deployment environment.

## Quick start: Cloudflare Tunnel

Cloudflare Tunnel (`cloudflared`) is one supported pattern. Any other
HTTPS reverse proxy that meets the tunnel-agnostic checklist works the
same way. The configuration lives entirely outside the testbed repo,
on the host running the tunnel daemon.

Prerequisites:

1. A Cloudflare account with a zone for `<root-domain>`.
2. `cloudflared` installed on a host that has IP reachability to
   `192.168.56.0/24` (typically the laptop or NUC running Vagrant).
3. `cloudflared tunnel login` executed once to fetch `cert.pem`.
4. `cloudflared tunnel create <name>` to provision the tunnel and the
   credentials JSON.

Minimum `/etc/cloudflared/config.yml`:

```yaml
tunnel: <tunnel-uuid>
credentials-file: /etc/cloudflared/<tunnel-uuid>.json

ingress:
  - hostname: core.<root-domain>
    service: http://192.168.56.11:31573
  - hostname: dev.<root-domain>
    service: http://192.168.56.13:31573
  - hostname: api.<root-domain>
    service: http://192.168.56.11:31920
  - hostname: demo.<root-domain>
    service: http://192.168.56.11:31940
  - service: http_status:404
```

Validate and route DNS:

```bash
sudo cloudflared --config /etc/cloudflared/config.yml tunnel ingress validate
for sub in core dev api demo; do
  sudo cloudflared tunnel route dns <tunnel-uuid> "${sub}.<root-domain>"
done
sudo systemctl restart cloudflared
```

For Cloudflare Zero Trust Access (optional perimeter gate), define one
self-hosted Access application per hostname and attach a policy. Add a
bypass policy for the realm endpoints so M2M token exchange and
discovery work without interactive login:

```
Destination: *.<root-domain>/auth/realms/<realm>/*
Action: Bypass
```

## Alternative: SSH local forward

For a single-user setup with no public DNS, an SSH local forward keeps
all traffic inside an existing connection:

```bash
ssh -L 8573:192.168.56.11:31573 \
    -L 8920:192.168.56.11:31920 \
    -L 8940:192.168.56.11:31940 \
    operator@nuc
```

The browser then targets `http://localhost:8573/`. Keep
`dashboard_external_origin` empty so the realm allows the LAN URL,
and use `localhost` only at the browser layer.

## See also

- [IAM](iam.md) — Keycloak realm structure, clients, roles, token retrieval
- [Phase 08: IAM](https://github.com/Jacobbista/kelt/blob/main/ansible/phases/08-iam/README.md) — implementation notes
- [Phase 09: Dashboard](https://github.com/Jacobbista/kelt/blob/main/ansible/phases/09-dashboard/README.md) — frontend reverse-proxy layout
