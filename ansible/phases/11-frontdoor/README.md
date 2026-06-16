# Phase 11 - Front-door (single-origin edge)

In-cluster nginx that fronts every HTTP surface under one base domain, routing
`<subdomain>.<base>` by Host header to the matching Service. A single Cloudflare
wildcard tunnel (`*.<base>`) and one Access app then cover all surfaces, replacing
the per-service tunnel route + Access app of the earlier model.

## Routing

| Subdomain (default) | Upstream Service | When |
|---------------------|------------------|------|
| `kelt.<base>` | `dashboard-frontend.dashboard:80` (also serves `/auth` `/api` `/docs`) | always |
| `api.<base>` | `camara-gateway.camara:8080` | `camara_enabled` |
| `demo.<base>` | `positioning-demo.mec:80` | `positioning_demo_enabled` |
| `placement.<base>` | `oauth2-proxy-placement.positioning:4180` (Keycloak gate) | `placement_editor_enabled` |
| `dev.<base>` | `192.168.56.13:31573` (Vite on the ansible VM) | `DASHBOARD_DEV_ENABLED` |

Subdomains, the base (`external_base_domain`), scheme, NodePort (`frontdoor_nodeport`),
and the feature flags are all defined in `ansible/group_vars/all.yml`; the realm
redirect URIs (phase 08) and the dashboard public links derive from the same vars.

## Implementation notes

- The phase is a no-op unless `external_base_domain` is set. With no base the
  testbed uses per-service LAN NodePorts and needs no front-door.
- Upstreams use a cluster-DNS `resolver` + variable `proxy_pass`, so a
  disabled-or-not-yet-deployed service returns 502 instead of crashing nginx at
  startup. Northbound server blocks are emitted only when their flag is on.
- `X-Forwarded-Proto` is set to `external_scheme` (https) so Keycloak and
  oauth2-proxy see the real edge scheme behind the tunnel.
- WebSocket upgrade is wired (Vite HMR on the dev surface, live log streams).
- TLS terminates at Cloudflare; the tunnel reaches this NodePort over HTTP,
  consistent with the rest of the testbed.

Traefik (k3s ships it, disabled here via `--disable traefik`) is a possible
future replacement that would express this routing as native Ingress objects;
tracked in `docs/roadmap.md`.
