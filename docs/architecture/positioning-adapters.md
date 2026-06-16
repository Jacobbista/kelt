# Positioning Adapters

The positioning subsystem is split into two concerns: a thin engine that
fuses measurements into a unified position, and a set of adapters that
each speak to one positioning technology (Wi-Fi RSSI, vendor RTLS, UWB,
or any future source). Phase 10 (northbound), positioning_engine role,
deploys the engine plus a standalone
`mock-positioning` adapter (so the engine exercises the real adapter HTTP
contract out of the box) and, opt-in, the `placement-editor` geometry UI.
Real-source adapters (`wifi-positioning`, the generic `rest-adapter`, and
bring-your-own images) are provisioned at runtime from the dashboard, not by
Ansible.

## Layers

```
[ Pi scanner ] ──POST scan──► [ wifi-positioning ] ──┐
                              own RSSI math + cfg     │
                              GET /measurement/{id}   │
                                                      │  ADAPTER_URLS
[ vendor source ] ──────────► [ vendor-adapter ] ─────┤  (CSV env)
   (private repo)             GET /measurement/{id}   │
                                                      ▼
                                          [ positioning-engine ]
                                          thin fusion + WGS84
                                                      │
                                                      ▼
                                          [ camara-gateway ]
                                          CAMARA Location API
```

The engine never talks to a measurement source directly. It polls one or
more adapters over HTTP, each implementing the same `GET /measurement/{id}`
contract, and fuses the responses into a single `Position`. When the
adapter list is empty the engine runs an embedded mock random walk so the
CAMARA flow remains functional end to end with no positioning source
attached.

## Public adapter contract

The HTTP contract that adapters must implement, the request/response
schemas, the health probe shape, and the reference implementation
(`wifi-positioning`) all live in the `5g-northbound` monorepo, alongside
the engine code that consumes them. See:

- `5g-northbound/docs/adapter-contract.md` for the protocol spec
- `5g-northbound/wifi-positioning/` for the reference implementation

This testbed pulls the published images and orchestrates them; the
contract itself is owned by the upstream repository so it can evolve
without a testbed release.

## What the positioning_engine role provisions

| Resource | Purpose |
|----------|---------|
| `Namespace positioning` | Isolation boundary for engine and all adapters |
| `ConfigMap positioning-config` | Engine env: `DEVICE_MAP`, `DEVICE_IDS`, `FUSION_STRATEGY`, `FUSION_COMPARE`, `WEBSOCKET_INTERVAL_MS`, `BLUEPRINT_SEED_PATH`. No static `ADAPTER_URLS`: adapters self-register (an optional seed is rendered only if `engine_adapter_urls` is set) |
| `Deployment positioning-engine` | Single replica, image from `5g-northbound`, REST + WebSocket on `8080`, embedded mock fallback when `ADAPTER_URLS` is empty |
| `Service positioning-engine` | ClusterIP plus NodePort `31930` |
| `Deployment/Service mock-positioning` | Standalone reference mock adapter (ClusterIP); `ADAPTER_URLS` is seeded to it so the engine fuses from a real adapter, not just the embedded fallback |
| `PVC positioning-blueprint` | Engine-owned blueprint store, RWO at `/app/data`; only the engine mounts it. The engine serves `GET/PUT /blueprint` |
| `ConfigMap positioning-blueprint-seed` | Cold-start default room + `gps_origin`, read once via `BLUEPRINT_SEED_PATH` when the blueprint PVC is empty |
| `Deployment/Service placement-editor` | Opt-in (`placement_editor_enabled`): geometry authoring UI, ClusterIP. A write-client that PUTs the authored blueprint to the engine (`POSITIONING_ENGINE_URL`); mounts no PVC |
| `oauth2-proxy-placement` (Deployment/Service/Secret) | Opt-in: Keycloak gate in front of placement-editor on NodePort `31950`; admits only `g-dashboard-admins` (realm client `placement-editor-proxy`) |

Adapters self-register with the engine and heartbeat, so they appear and
disappear in the live registry without an engine restart; the embedded mock
covers any gap before the first self-registration.

### Blueprint distribution

The room geometry (the blueprint) is network-distributed with a single
authority: the **engine** persists it on its own PVC and serves `GET/PUT
/blueprint`. The placement-editor PUTs the authored blueprint; the demo GETs
it through the CAMARA gateway (a MEC app talks only to the gateway); adapters
that need geometry GET it from the engine. There is no shared PVC and no
file-mount across services, which keeps every consumer network-driven (the
same model the edge Wi-Fi scanner already uses). The blueprint schema, the
endpoint contract, and the bindings-vs-blueprint split are owned upstream:
see `5g-northbound/docs/blueprint-vs-bindings.md`.

## Adding an adapter

Adapters **self-register** with the engine (v0.6.0). On boot each adapter POSTs
its name + base URL + kind to the engine (`POST /adapters`, target
`POSITIONING_ENGINE_URL`) and heartbeats; the engine is the registry authority
and evicts a self-registration that stops heartbeating after `ADAPTER_TTL_S`.
There is no static `ADAPTER_URLS` list to maintain and no rollout to trigger:
deploy an adapter and it announces itself within a heartbeat. The registry,
the endpoint contract, and the heartbeat/TTL semantics are owned upstream:
see `5g-northbound/docs/adapter-registry.md`.

`ADAPTER_URLS` survives only as an optional cold-start **seed** the engine reads
once when its registry is empty (for an off-cluster adapter that cannot
self-register); the baseline `mock` self-registers, so the testbed leaves it
unset. The adapter Deployment must expose `GET /health` (no auth) and
`GET /measurement/{id}` per the public contract.

### Dashboard provisioning

The dashboard Northbound page (`/northbound`, backend `/api/v1/northbound/*`)
shows the **live registry** read from the engine (`GET /adapters`): per adapter
its `kind`, `registered_via` (self/seed/manual), `last_seen`, and a derived
`state` (live / unreachable / stale). It does not register adapters by hand;
it deploys adapter images that then self-register, and can force-remove a stale
entry (`DELETE /adapters/{name}`). Two ways to add a positioning source without
touching Ansible:

1. **Bring your own adapter image.** Deploy-from-image: give a name,
   `image:tag`, port, optional `kind`, env vars (secret-marked vars go into a
   Secret), and an optional `imagePullSecret` for private images. The backend
   creates the Deployment + ClusterIP Service in the `positioning` namespace
   (pinned to the worker node) and injects the self-registration env
   (`POSITIONING_ENGINE_URL`, `ADAPTER_NAME`, `ADAPTER_BASE_URL`, and
   `ADAPTER_KIND` when given) so the adapter announces itself. The catalog
   pre-fills the reference `wifi-positioning`.

2. **No new code: the generic `rest-adapter`.** Deploy the stock
   `rest-adapter` image, then declare a schema that maps any REST API to the
   `Measurement` shape (the adapter persists the schema; the dashboard can
   write it via the adapter's `PUT /schema`). Credentials are mounted from a
   Secret.

Deploy-from-image is admin-only and additionally gated by the backend
`allow_workload_create` setting; every write is audited. The fusion editor
still patches `positioning-config` and restarts the engine. See
`docs/dashboard/modules.md` and `docs/dashboard/api-reference.md`.

## Backbone versus catalog: why the split

The split keeps the public testbed reproducible and the operational state
mutable. Ansible phases describe what the backbone looks like at the
start of every deployment, identically across users. Adapters depend on
who is using the testbed and on which hardware is connected; pinning them
in Ansible would push specific vendor or topology choices into a generic
artifact. Moving adapter provisioning to runtime keeps the published
contract vendor-neutral, lets each operator wire only the sources they
have, and supports private adapter images that must not appear in the
public repository.

## See also

- [Phase 10 Northbound README](https://github.com/Jacobbista/kelt/blob/main/ansible/phases/10-northbound/README.md) implementation notes for the CAMARA gateway, positioning engine, and demo
