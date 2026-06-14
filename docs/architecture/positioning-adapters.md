# Positioning Adapters

The positioning subsystem is split into two concerns: a thin engine that
fuses measurements into a unified position, and a set of adapters that
each speak to one positioning technology (Wi-Fi RSSI, vendor RTLS, UWB,
or any future source). Phase 11 deploys the engine plus a standalone
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

## What phase 11 provisions

| Resource | Purpose |
|----------|---------|
| `Namespace positioning` | Isolation boundary for engine and all adapters |
| `ConfigMap positioning-config` | Engine env: `ADAPTER_URLS` (name=url), `DEVICE_MAP`, `DEVICE_IDS`, `FUSION_STRATEGY`, `FUSION_COMPARE`, `WEBSOCKET_INTERVAL_MS` |
| `Deployment positioning-engine` | Single replica, image from `5g-northbound`, REST + WebSocket on `8080`, embedded mock fallback when `ADAPTER_URLS` is empty |
| `Service positioning-engine` | ClusterIP plus NodePort `31930` |
| `Deployment/Service mock-positioning` | Standalone reference mock adapter (ClusterIP); `ADAPTER_URLS` is seeded to it so the engine fuses from a real adapter, not just the embedded fallback |
| `PVC positioning-blueprint` + `Deployment/Service placement-editor` | Opt-in (`placement_editor_enabled`): geometry authoring UI on an RWO blueprint PVC, ClusterIP |
| `oauth2-proxy-placement` (Deployment/Service/Secret) | Opt-in: Keycloak gate in front of placement-editor on NodePort `31950`; admits only `g-dashboard-admins` (realm client `placement-editor-proxy`) |

The engine reads `ADAPTER_URLS` at startup only, so adding or removing an
adapter restarts it (the mock fallback covers the gap).

## Adding an adapter

Two paths are supported. The first is available today; the second is the
planned operational workflow.

### Manual provisioning (today)

Create the adapter resources directly with `sudo k3s kubectl` from the
master VM, then append the new URL to `positioning-config` and roll the
engine. The `positioning` namespace already exists.

```bash
sudo k3s kubectl -n positioning apply -f my-adapter-deployment.yaml
sudo k3s kubectl -n positioning apply -f my-adapter-service.yaml
sudo k3s kubectl -n positioning patch configmap positioning-config \
  --type merge \
  -p '{"data":{"ADAPTER_URLS":"http://my-adapter.positioning.svc.cluster.local:8080"}}'
sudo k3s kubectl -n positioning rollout restart deploy/positioning-engine
```

The adapter Deployment must expose `GET /health` (no auth) and
`GET /measurement/{id}` per the public contract.

### Dashboard provisioning

The dashboard Northbound page (`/northbound`, backend
`/api/v1/northbound/*`) implements this workflow. It lists the active
services and adapters, and offers two ways to add a positioning source
without touching Ansible:

1. **Bring your own adapter image.** Deploy-from-image: give a name,
   `image:tag`, port, env vars (secret-marked vars go into a Secret), and an
   optional `imagePullSecret` for private images. The backend creates the
   Deployment + ClusterIP Service in the `positioning` namespace (pinned to
   the worker node), then registers the in-cluster URL in `ADAPTER_URLS` and
   restarts the engine. The catalog pre-fills the reference `wifi-positioning`.

2. **No new code: the generic `rest-adapter`.** Deploy the stock
   `rest-adapter` image, then declare a schema that maps any REST API to the
   `Measurement` shape (the adapter persists the schema; the dashboard can
   write it via the adapter's `PUT /schema`). Credentials are mounted from a
   Secret.

Adapter registry add/remove and the fusion editor patch `positioning-config`
and restart the engine. Deploy-from-image is admin-only and additionally
gated by the backend `allow_workload_create` setting; every write is audited.
See `docs/dashboard/modules.md` and `docs/dashboard/api-reference.md`.

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

- [Phase 11 README](https://github.com/Jacobbista/kelt/blob/main/ansible/phases/11-positioning/README.md) implementation notes
- [Phase 10 CAMARA](https://github.com/Jacobbista/kelt/blob/main/ansible/phases/10-camara/README.md) the consumer of engine output
- [Phase 12 Demo](https://github.com/Jacobbista/kelt/blob/main/ansible/phases/12-positioning-demo/README.md) the visualization layer
