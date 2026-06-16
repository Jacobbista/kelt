# Phase 10 - Northbound (CAMARA Location API + Positioning)

The northbound platform over Open5GS, deployed as one phase composed of focused
roles. It exposes device location via the official CAMARA Location API, fuses
measurements from pluggable positioning adapters, and ships a geometry-authoring
UI and a 3D demo. Requires phase 08 (Keycloak/IAM). Optional addon, off by
default; the umbrella `NORTHBOUND_ENABLED` (`testbed northbound on`) enables it.

## Roles

| Role | Builds | Flag |
|------|--------|------|
| `positioning_engine` | Fusion engine + standalone `mock-positioning` adapter (NodePort `31930`, REST + WebSocket). Owns the blueprint store (PVC at `/app/data`, `GET/PUT /blueprint`, cold-start seed) | `positioning_enabled` |
| `camara_gateway` | CAMARA Location API gateway, validates Bearer tokens against the realm, forwards to the engine (NodePort `31920`) | `camara_enabled` |
| `placement_editor` | Room-geometry UI, a write-client that PUTs the blueprint to the engine (no PVC), **always** behind its Keycloak front-door gate (includes `frontdoor_gate`) | `placement_editor_enabled` |
| `frontdoor_gate` | Generic oauth2-proxy that gives any no-auth UI a Keycloak login + group authorization (parameterized on `gate_*`) | included by callers |
| `positioning_demo` | 3D demo SPA consuming the CAMARA API, PKCE login (NodePort `31940`, `mec` namespace) | `positioning_demo_enabled` |

## Run

```bash
vagrant ssh ansible
cd ~/ansible-ro
ansible-playbook phases/10-northbound/playbook.yml
```

Run a single piece with tags (`testbed run-phase 10-northbound` then add `--tags`):

```bash
ansible-playbook phases/10-northbound/playbook.yml --tags camara       # gateway only
ansible-playbook phases/10-northbound/playbook.yml --tags positioning  # engine + placement
ansible-playbook phases/10-northbound/playbook.yml --tags demo
```

Common overrides (else read from env, then lab defaults):

```bash
ansible-playbook phases/10-northbound/playbook.yml \
  -e camara_client_secret='<strong>' \
  -e engine_adapter_urls='wifi=http://wifi-positioning.positioning.svc.cluster.local:8080'
```

## Endpoints exposed

| Surface | Route | Auth |
|---------|-------|------|
| CAMARA | `POST /location-retrieval/v0.5/retrieve`, `POST /location-verification/v3/verify` | Bearer (`camara-location-read`) |
| CAMARA | `WS /positions/stream?token=<jwt>` (live feed; token in query) | Bearer (`camara-location-read`) |
| CAMARA | `/health`, `/docs` | none |
| Engine | `GET /position/{device_id}`, `GET /health` | none (in-cluster, called by the gateway) |
| Engine | `/ws/positions` | none (in-cluster, bridged by the gateway's `/positions/stream`) |
| placement-editor | `/` | Keycloak login via the front-door gate (`g-positioning-editors` or `g-dashboard-admins`) |

## Front-door gate (frontdoor_gate role)

`placement-editor` has no native auth, so the `placement_editor` role always
includes the generic `frontdoor_gate` role, which fronts it with an oauth2-proxy
doing the Keycloak OIDC login. The browser is sent to the canonical issuer; the
proxy redeems tokens and fetches JWKS from in-cluster Keycloak (dual-URL split),
so the same config works whether served locally or behind a tunnel. The gate is
generic (`gate_name`, `gate_upstream`, `gate_client_id`, `gate_allowed_groups`,
`gate_external_origin`, ...), reusable for any future no-auth surface. The
confidential realm client it uses is created idempotently in phase 08.
See [docs/security/external-access.md](../../../docs/security/external-access.md).

## Configuration (single mechanism)

Every service takes its config from **one input: pod environment variables**. The
Python services read them directly; the frontends (demo, placement-editor) render
`window.__ENV__` from the same pod env via their image entrypoint at start. So a
ConfigMap (non-sensitive) and a Secret (sensitive) are attached with `envFrom`,
both `optional: true` so a pod still boots degraded and serves `/contract`.

Do NOT mount a ConfigMap as a file over an entrypoint-rendered file (the legacy
anti-pattern): the read-only mount shadows the entrypoint. The demo mounts only
its nginx config as a file (legitimate routing) and keeps a `location = /contract`
so the baked contract stays reachable.

The demo's nginx reverse-proxies `/api/camara/` to the gateway and forwards the
WebSocket upgrade (`Upgrade`/`Connection` headers): the gateway's live position
feed (`/positions/stream`) is a WebSocket, not REST, so without the upgrade the
handshake reaches the gateway as a plain GET on a WS route and 404s.

Each service (0.3.0+) serves `GET /contract`: metadata only (`kind`,
`external_origin` var, required/recommended/optional env with a `sensitive` flag),
served degraded-bootable and auth-exempt like `/health`, never config values. The
dashboard guided setup reads it and routes each value by `sensitive` to the Secret
or ConfigMap. See [docs/dashboard/modules.md](../../../docs/dashboard/modules.md).

## Adapters and contracts

Concrete adapters (`wifi-positioning`, the generic `rest-adapter`, vendor or
bring-your-own images) are not provisioned here; they are added at runtime from
the dashboard Northbound console, following the public HTTP adapter contract. See
[docs/architecture/positioning-adapters.md](../../../docs/architecture/positioning-adapters.md).

## Images

Built from the `5g-northbound` monorepo, published to
`ghcr.io/jacobbista/5g-northbound/<service>`. Default tags pinned in each role's
`defaults/main.yml`; override with `-e <role>_image=<image>:<tag>`.

## See also

- [IAM](../../../docs/security/iam.md) realm structure, role matrix, front-door client
- [Positioning Adapters](../../../docs/architecture/positioning-adapters.md) engine + adapter layer
- [External access](../../../docs/security/external-access.md) routes vs subdomains, front-door auth
- [Phase 08: IAM](../08-iam/README.md) Keycloak prerequisites
