# External Access

The testbed binds all interactive services to RFC1918 addresses on the lab
network. Exposing them to the public internet is out of scope for the
provisioning code and stays on the operator side. This document describes the
moving parts that any external-access setup must align with, regardless of
which tunnel technology is chosen.

## Single base domain and the front-door

External exposure is driven by one operator-set value, `external_base_domain`.
When it is set, every HTTP surface is reached as `<subdomain>.<base>` through an
in-cluster front-door (phase 11): a small nginx that routes by Host header to the
matching Service. A single Cloudflare wildcard tunnel (`*.<base>` to the
front-door NodePort `31500`) and one Access app then cover every surface, instead
of a tunnel route and Access app per service. Subdomains, the base, the scheme,
and the front-door NodePort all live in `ansible/group_vars/all.yml`; the realm
redirect URIs (phase 08) and the dashboard public links derive from the same
values, so a surface's external address is defined in exactly one place.

When `external_base_domain` is empty the front-door is not deployed and the
testbed uses the per-service LAN NodePorts below (LAN-only operation). A single
per-service `*_external_origin` override still wins over the derived value, for a
deliberate one-off.

## What needs an external entry point

Four HTTP surfaces carry external traffic. Each is reached as a subdomain through
the front-door when a base domain is set, or stays on the worker NodePort for
LAN-only operation:

| Service | Default LAN URL | Phase | Audience | Notes |
|---------|-----------------|-------|----------|-------|
| Dashboard frontend (prod) | `http://<worker>:31573` | 09 | Operators | Reverse-proxies `/api`, `/health`, `/watchdog` to the backend, `/auth` to Keycloak. Single origin for browser, API, and WebSockets. |
| Dashboard frontend (dev, opt-in) | `http://<control>:31573` | 09 | Frontend developers | Vite HMR on the ansible VM. Started on demand from the prod sidebar widget. |
| CAMARA Gateway | `http://<worker>:31920` | 10 | M2M API consumers | Stateless gateway, OAuth2 client_credentials. No browser session. |
| Positioning Demo | `http://<worker>:31940` | 10 | End users | Browser app with PKCE login against Keycloak. |

The `placement-editor` geometry UI (phase 10, `placement_editor` role, opt-in) is
a fifth surface on worker NodePort `31950`. It has no native auth, so the
`placement_editor` role always fronts it with the generic `frontdoor_gate` role
(an `oauth2-proxy`) that runs the OIDC login against Keycloak (realm client
`placement-editor-proxy`) and admits `g-positioning-editors` or
`g-dashboard-admins`. The gate is mandatory, not optional: the editor and its
gate deploy as one unit. See the front-door auth model below.

## Routes versus subdomains

Each surface is exposed one of two ways, and the choice is dictated by what the
surface serves, not by preference:

- **An API or a backend** (CAMARA gateway, the dashboard backend) is exposed by
  a path under a shared origin: the reverse proxy strips the prefix and the
  service receives ordinary requests. A JSON response references no other files,
  so a prefix is invisible to it. The service stays unaware of where it is
  mounted; the orchestrator decides placement. This is why `/api` and `/auth`
  already live under the dashboard origin, and why CAMARA can be collapsed under
  the same origin by path.
- **An independent single-page app** (positioning demo, placement-editor) gets
  its own origin (a subdomain at root), not a sub-path. A built SPA carries
  absolute asset URLs (`/assets/...`) and resolves client-side routes in the
  browser, outside the proxy. Served under `/demo/` the browser would request
  `/assets/...` from the root and fail. Serving the SPA at the root of its own
  hostname keeps those baked paths correct with no change to the image: the image
  still believes it lives at `/`, and the orchestrator only points a subdomain at
  it. A sub-path would instead require a build-time Vite `base`, which couples the
  image to its deployment location, so it is avoided.

Routing by Host is the front-door's job, not the tunnel's: Cloudflare forwards
the wildcard `*.<base>` to the single front-door NodePort, and the front-door
maps each Host to its Service. One wildcard Access policy (`*.<base>`) covers them
all, so adding a surface is a server block in the front-door config plus its
subdomain default, with no new tunnel route, DNS record, or Access rule.

## Dynamic edge-app routes

When the edge apps platform is enabled (`apps_enabled`), the front-door also
carries one regex server block that proxies any otherwise-unmatched single-label
subdomain `<name>.<base>` to the same-named Service in the `apps` namespace,
resolved at request time. An operator-deployed app is therefore reachable the
moment its Service exists, with no template edit or front-door re-run. App
frontends exposed this way have no application-level auth by default and sit
behind the same optional Access perimeter as every other surface; an app needing
login can be fronted by the `frontdoor_gate` building block. See
[../architecture/edge-apps.md](../architecture/edge-apps.md).

## Front-door auth (services without native auth)

Surfaces that already authenticate (dashboard, demo via PKCE; CAMARA via its own
JWT validation) need no extra gate. A surface with no native auth is fronted by
the generic `frontdoor_gate` role (an `oauth2-proxy`), which performs the
Keycloak OIDC login and admits only members of the configured groups. It is
parameterized (`gate_name`, `gate_upstream`, `gate_client_id`,
`gate_allowed_groups`, `gate_external_origin`), so any future no-auth surface
reuses it; `placement-editor` is the first consumer.

The gate works identically whether served locally or behind a tunnel because it
splits the OIDC URLs:

- the browser is redirected to the **canonical issuer** (the same
  `dashboard_external_origin` Keycloak advertises as `KC_HOSTNAME`), and
- the proxy redeems tokens and fetches JWKS from **in-cluster Keycloak**
  (`keycloak.<iam-ns>.svc`), so it never hairpins out through the tunnel.

Because `KC_HOSTNAME` is pinned, tokens fetched in-cluster still carry the
canonical `iss`, so they validate against the issuer the browser used. The realm
client the gate needs is created idempotently in phase 08.

Keycloak itself (`http://<worker>:31910`) is reachable only when one of the
above services needs to redirect a user agent to the login screen. Two layout
options are supported by phase 08:

- **Path-prefix layout** (`keycloak_path_prefix: "/auth"`): a single external
  origin serves both the dashboard and Keycloak. The dashboard frontend
  reverse-proxies `/auth/*` to the Keycloak service. Keycloak runs with
  `KC_HTTP_RELATIVE_PATH` set so the issuer, login, and JWKS URLs include
  the prefix. This is the default whenever `external_base_domain` is set.
- **Root layout** (`keycloak_path_prefix: ""`): Keycloak runs at the root of its
  reachable origin. This is the default in LAN mode (no base domain), where
  Keycloak is hit directly on its NodePort.

The path-prefix layout keeps Keycloak on the single `kelt.<base>` origin and
removes a class of CORS, mixed-content, and Private Network Access (PNA) issues.
It is selected automatically under the single-base model.

## Variables to override at deploy time

External hostnames are not hardcoded. The realm template and frontend env
read the following Ansible variables:

| Variable | Default | Purpose |
|----------|---------|---------|
| `external_base_domain` | `""` | The one operator-set value. When set, every surface is `<subdomain>.<base>` and each `*_external_origin` below derives from it; empty means LAN NodePorts. |
| `external_scheme` | `https` | Scheme for the derived origins. |
| `dashboard_subdomain` / `camara_subdomain` / `positioning_demo_subdomain` / `placement_editor_subdomain` / `dashboard_dev_subdomain` | `kelt` / `api` / `demo` / `placement` / `dev` | Per-surface subdomain defaults; overridable. The front-door, realm origins, and dashboard links all read these. |
| `dashboard_external_origin` | `<scheme>://<kelt>.<base>` (else `http://<worker-ip>:31573`) | OIDC redirect URI and Web Origin for the `dashboard` client. Also `KC_HOSTNAME` so Keycloak emits browser-coherent URLs behind a proxy. |
| `dashboard_dev_external_url` | `""` | Optional second origin for the opt-in Vite dev frontend. When set, added to the `dashboard` client allow lists. |
| `camara_gateway_external_origin` | `http://<worker-ip>:31920` | Advertised in the gateway's OpenAPI `servers` block and in any absolute URL the gateway emits. Operator routes the chosen hostname to the worker NodePort. |
| `positioning_demo_external_origin` | `http://<worker-ip>:31940` | OIDC redirect URI and Web Origin for the `positioning-demo` client. |
| `placement_editor_external_origin` | `http://<worker-ip>:31950` | Redirect URI base for the `placement-editor-proxy` front-door gate. Set to `https://placement.<root>` when exposed externally. |
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

The preset writes `EXTERNAL_BASE_DOMAIN` (and clears any per-service overrides)
into `.testbed.env`, which subsequent `run-phase` and `provision` invocations
source automatically. Direct invocation for ad-hoc tuning sets just the base:

```bash
ansible-playbook ansible/phases/08-iam/playbook.yml \
  -e external_base_domain=example.com \
  -e keycloak_admin_password='<strong-secret>' \
  -e keycloak_db_password='<strong-secret>' \
  -e camara_client_secret='<strong-secret>' \
  -e dashboard_readonly_secret='<strong-secret>'
```

A single surface can still be pinned to a one-off hostname by setting its
`*_external_origin` explicitly; the override wins over the base derivation.

## Sub-domain convention

The `auth-network preset-cloudflare <base-domain>` helper sets the base; every
surface is then `<subdomain>.<base>`, served through the front-door (phase 11).
The same subdomains apply to any tunnel or reverse-proxy provider; only the layer
in front of the front-door NodePort changes.

| Hostname | Front-door routes to | Audience |
|----------|----------------------|----------|
| `kelt.<base>` | `dashboard-frontend.dashboard:80` (also `/auth` `/api` `/docs`) | Operators (dashboard) |
| `dev.<base>` | ansible VM `:31573` (Vite) | Frontend developers (opt-in) |
| `api.<base>` | `camara-gateway.camara:8080` | M2M CAMARA API consumers |
| `demo.<base>` | `positioning-demo.mec:80` | End users (positioning demo) |
| `placement.<base>` | `oauth2-proxy-placement.positioning:4180` | Editors (placement-editor, behind the front-door gate) |

Keycloak is reachable under `kelt.<base>/auth/` via the dashboard frontend
reverse proxy. No separate `auth.<base>` hostname is required;
`keycloak_path_prefix` defaults to `/auth` whenever a base domain is set.

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

Minimum `/etc/cloudflared/config.yml`, one wildcard rule to the front-door
NodePort (the in-cluster front-door handles per-Host routing):

```yaml
tunnel: <tunnel-uuid>
credentials-file: /etc/cloudflared/<tunnel-uuid>.json

ingress:
  - hostname: "*.<base-domain>"
    service: http://192.168.56.11:31500   # front-door NodePort (frontdoor_nodeport)
  - service: http_status:404
```

Validate and route DNS (one wildcard CNAME):

```bash
sudo cloudflared --config /etc/cloudflared/config.yml tunnel ingress validate
sudo cloudflared tunnel route dns <tunnel-uuid> "*.<base-domain>"
sudo systemctl restart cloudflared
```

For Cloudflare Zero Trust Access (optional perimeter gate), one self-hosted
Access application over the wildcard `*.<base-domain>` covers every surface, so
no per-service rule is needed. Two bypass policies are required so machine and
discovery traffic, which cannot do interactive login, still works:

```
# OIDC discovery and token exchange (browser PKCE and oauth2-proxy both need it)
Destination: *.<base-domain>/auth/realms/<realm>/*
Action: Bypass

# M2M CAMARA API (client_credentials, no human session)
Destination: api.<base-domain>
Action: Bypass
```

`placement.<base-domain>` does NOT need a bypass: its front-door gate
(oauth2-proxy) performs the interactive Keycloak login itself.

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

- [IAM](iam.md): Keycloak realm structure, clients, roles, token retrieval
- [Phase 08: IAM](https://github.com/Jacobbista/kelt/blob/main/ansible/phases/08-iam/README.md): implementation notes
- [Phase 09: Dashboard](https://github.com/Jacobbista/kelt/blob/main/ansible/phases/09-dashboard/README.md): frontend reverse-proxy layout
- [Phase 11: Front-door](https://github.com/Jacobbista/kelt/blob/main/ansible/phases/11-frontdoor/README.md): single-origin edge, Host-based routing
