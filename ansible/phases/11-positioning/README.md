# Phase 11 - Positioning Engine

Deploys the thin positioning engine that fuses measurements from one or
more positioning adapters and exposes a unified `Position` to the CAMARA
gateway (phase 10). The engine is the backbone only; concrete adapters
(`wifi-positioning`, vendor-specific) are not provisioned by this phase
and are added at runtime via the dashboard adapter-provisioning
workflow. With no adapters configured the engine runs an embedded mock
random walk so the demo works out of the box.

## Run

```bash
vagrant ssh ansible
cd ~/ansible-ro
ansible-playbook phases/11-positioning/playbook.yml
```

Pre-seed adapter URLs at deploy time:

```bash
ansible-playbook phases/11-positioning/playbook.yml \
  -e engine_adapter_urls='http://wifi-positioning.positioning.svc.cluster.local:8080'
```

Multiple URLs as CSV. Mutating `ADAPTER_URLS` at runtime is supported via
patching the `positioning-config` ConfigMap and rolling the engine
deployment; see [docs/architecture/positioning-adapters.md](../../../docs/architecture/positioning-adapters.md).

## What it does

- Creates the `positioning` namespace.
- Pre-pulls the engine image on the worker.
- Applies the `positioning-config` ConfigMap with `ADAPTER_URLS`
  (empty by default).
- Deploys the engine as a single replica pinned to the worker, exposed
  on NodePort `31930` (REST and WebSocket share the same uvicorn port).
- Waits for the engine readiness probe (`/health`) before returning.

The deployment template carries a `checksum/config` annotation so
edits to the ConfigMap trigger a rolling restart automatically.

## Endpoints exposed

| Route | Method | Purpose |
|-------|--------|---------|
| `/health` | GET | readiness/liveness probe (no auth) |
| `/position/{device_id}` | GET | unified position lookup (called by camara-gateway) |
| `/ws/positions` | WebSocket | live position stream (consumed by phase 12 demo) |

## Image

Built from the `5g-northbound` monorepo and published to
`ghcr.io/jacobbista/5g-northbound/positioning-engine`. The default tag is
pinned in `roles/positioning_setup/defaults/main.yml`. Override at
deploy time with `-e positioning_engine_image=<image>:<tag>`.

## Adapters

Adapter images are not provisioned here. See
[docs/architecture/positioning-adapters.md](../../../docs/architecture/positioning-adapters.md)
for the public HTTP contract, the manual provisioning recipe, and the
reference implementation (`wifi-positioning`) in the upstream repository.

## See also

- [Positioning Adapters](../../../docs/architecture/positioning-adapters.md) contract and runtime workflow
- [Phase 10: CAMARA](../10-camara/README.md) the consumer of engine output
- [Phase 12: Demo](../12-positioning-demo/README.md) the visualization layer
