# Positioning Adapters

The positioning subsystem is split into two concerns: a thin engine that
fuses measurements into a unified position, and a set of adapters that
each speak to one positioning technology (Wi-Fi RSSI, vendor RTLS, UWB,
or any future source). The engine is the backbone, deployed by phase 11.
Adapters are provisioned at runtime, not by Ansible.

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

Phase 11 deploys only the engine backbone:

| Resource | Purpose |
|----------|---------|
| `Namespace positioning` | Isolation boundary for engine and all adapters |
| `ConfigMap positioning-config` | Holds `ADAPTER_URLS` (CSV, empty by default) |
| `Deployment positioning-engine` | Single replica, image from `5g-northbound`, REST on `8080`, WebSocket on `8081`, embedded mock fallback |
| `Service positioning-engine` | ClusterIP plus NodePort `31930`/`31931` for cluster-internal and external probing |

No adapter pod, no adapter ConfigMap, no adapter Service. The engine boots
healthy with `ADAPTER_URLS=""` and serves mock data immediately.

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

### Dashboard provisioning (planned)

A future dashboard section under MEC services will list active adapters,
offer a catalog of known images (the reference `wifi-positioning` plus
any user-added image with optional pull secret), create the K8s
resources, patch `positioning-config`, and trigger the engine rollout.
The mechanism is the same; only the front-end interaction changes.

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

- [Phase 11 README](https://github.com/Jacobbista/5g-k3s-kubedge-testbed/blob/main/ansible/phases/11-positioning/README.md) implementation notes
- [Phase 10 CAMARA](https://github.com/Jacobbista/5g-k3s-kubedge-testbed/blob/main/ansible/phases/10-camara/README.md) the consumer of engine output
- [Phase 12 Demo](https://github.com/Jacobbista/5g-k3s-kubedge-testbed/blob/main/ansible/phases/12-positioning-demo/README.md) the visualization layer
