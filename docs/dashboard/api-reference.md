# Dashboard API Reference

Base URL: `http://192.168.56.13:31880`

Interactive API docs (Swagger UI): `http://192.168.56.13:31880/docs`

## Authentication

Read operations are open. Mutating operations require a Bearer token:

```
Authorization: Bearer <DASHBOARD_ADMIN_TOKEN>
```

The token is set in `dashboard/backend/.env` (`DASHBOARD_ADMIN_TOKEN`).

---

## Health

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/health` | — | Backend liveness check |

---

## Pods & Deployments (Control Room)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/v1/pods` | — | List pods. Query: `?namespace=5g` |
| GET | `/api/v1/pods/{pod}/describe` | — | Describe a pod. Query: `?namespace=5g` |
| POST | `/api/v1/deployments/{deployment}/restart` | ✅ Admin | Rolling restart a deployment. Query: `?namespace=5g` |

---

## ConfigMaps

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/v1/configmaps/{name}` | — | Read a ConfigMap. Query: `?namespace=5g` |
| PUT | `/api/v1/configmaps/{name}` | ✅ Admin | Update a ConfigMap. Requires `DASHBOARD_ALLOW_CONFIGMAP_WRITE=true` |

---

## Topology Map

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/v1/topology` | — | Pod nodes + OVS bridges with Multus interface metadata. Query: `?namespace=5g` |
| GET | `/api/v1/network/nads` | — | List NetworkAttachmentDefinitions. Query: `?namespace=5g` |
| GET | `/api/v1/ovs/bridges/{bridge}/flows` | — | OVS OpenFlow rules for a bridge (from `ovs-ofctl dump-flows`) |

---

## Subscriber Management

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/v1/subscribers` | — | List all Open5GS subscriber records |
| POST | `/api/v1/subscribers` | ✅ Admin | Create a subscriber. Body: subscriber object |
| PUT | `/api/v1/subscribers/{imsi}` | ✅ Admin | Update a subscriber |
| DELETE | `/api/v1/subscribers/{imsi}` | ✅ Admin | Delete a subscriber |
| POST | `/api/v1/subscribers/init` | ✅ Admin | Trigger Ansible subscriber import (reset to defaults) |

---

## UE Monitoring

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/v1/ue/summary` | — | Prometheus-backed gauges: gNBs, RAN UEs, PDU sessions, registration counts |
| GET | `/api/v1/ue/events` | — | Log-parsed UE events. Query: `?minutes=10` |
| GET | `/api/v1/ue/active` | — | Active UE list reconstructed from AMF/SMF logs |
| GET | `/api/v1/ue/pods` | — | UERANSIM UE pod list |
| POST | `/api/v1/ue/test/ping` | ✅ Admin | Run ping from a UE pod. Body: `{pod, namespace, target_ip}` |
| POST | `/api/v1/ue/test/iperf` | ✅ Admin | Run iperf3 from a UE pod. Body: `{pod, namespace, target_ip, duration}` |

---

## Metrics

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/v1/metrics/nodes` | — | Node-level CPU, memory, disk from Prometheus/Node Exporter |
| GET | `/api/v1/metrics/nf` | — | Per-NF CPU, memory, restart counts |

---

## RAN Mode Control

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/v1/ran/status` | — | Current RAN mode (physical / simulated / coexistence) and resource state |
| POST | `/api/v1/ran/enable` | ✅ Admin | Enable a RAN mode. Body: `{mode: "physical" \| "simulated" \| "coexistence"}` |
| POST | `/api/v1/ran/disable` | ✅ Admin | Disable a RAN mode. Body: `{mode: "physical" \| "simulated"}` |

See [RAN Modes](../deployment/ran-modes-dashboard.md) for the full workflow.

---

## Physical RAN Config

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/v1/physical-ran/interfaces` | — | Available bridge interfaces on worker |
| GET | `/api/v1/physical-ran/status` | — | OVS bridge state, AMF annotation state |
| POST | `/api/v1/physical-ran/setup` | ✅ Admin | Trigger Ansible OVS setup + AMF annotation patch |

---

## Network Health

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/v1/network/health` | — | Cached N-interface connectivity results (N2/N3/N4/N6) |
| POST | `/api/v1/network/health/run` | — | Trigger immediate health check (bypasses cache) |

---

## Northbound (positioning / CAMARA)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/v1/northbound/services` | — | Inventory of the camara/positioning/mec deployments (image, ready replicas, pods) |
| GET | `/api/v1/northbound/adapters` | — | Live adapter registry from the engine (`GET /adapters` via the API-server service proxy): per adapter `kind`, `registered_via`, `last_seen_s_ago`, and derived `state` (live/unreachable/stale) |
| GET | `/api/v1/northbound/contract` | — | Adapter contract guidance: `Measurement` schema, Python skeleton, `env.contract.yaml` template, doc links |
| GET | `/api/v1/northbound/contract/{service}` | — | Live per-service contract fetched from the service's own `/contract` (kind, `external_origin` var, required/recommended/optional env). Degrades to `{available: false}` when the service exposes none |
| GET | `/api/v1/northbound/config/{service}` | — | Guided-setup read: the contract plus current state (non-sensitive values; sensitive reported set/unset only, never the value) |
| PUT | `/api/v1/northbound/config/{service}` | ✅ Admin | Guided-setup apply. Body: `{values: {VAR: value}}`. Routes each var by the contract `sensitive` flag (Secret vs ConfigMap, both via envFrom), then rolls the deployment |
| DELETE | `/api/v1/northbound/adapters/{name}` | ✅ Admin | Force-remove a stale registry entry (engine `DELETE /adapters/{name}`). Adapters self-register, so there is no manual register endpoint |
| POST | `/api/v1/northbound/deploy` | ✅ Admin | Deploy a custom adapter image. Requires `DASHBOARD_ALLOW_WORKLOAD_CREATE=true`. Body: `{name, image, port, env[], image_pull_secret?, kind?}`. The backend injects the self-registration env so the adapter announces itself to the engine |
| DELETE | `/api/v1/northbound/workloads/{name}` | ✅ Admin | Delete a deploy-from-image adapter (Deployment, Service, Secret) and unregister it |
| PUT | `/api/v1/northbound/fusion` | ✅ Admin | Update engine fusion config. Body: `{strategy?, compare?, device_map?}` |
| POST | `/api/v1/northbound/managed/{deployment}/image` | ✅ Admin | Retarget a managed deployment (gateway/engine/demo) to a new image. Body: `{image}` |

---

## Edge apps (phase 12)

Operator-deployed application pods in the `mec` namespace; the local registry lives in the `apps` namespace. See [architecture/edge-apps.md](../architecture/edge-apps.md).

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/v1/apps` | — | Inventory of deployed apps (name, image, replicas, ready, exposed, public URL `kelt-<name>.<base>`, `mec_attached`, `mec_ip`) |
| GET | `/api/v1/apps/public` | — | Unauthenticated (no token even in auth mode): names + public URLs of exposed apps, for the front-door welcome page |
| GET | `/api/v1/apps/updates` | — | Per app: whether the registry digest for its tag is newer than the running pod's |
| POST | `/api/v1/apps` | ✅ Admin | Deploy an app. Requires `DASHBOARD_ALLOW_WORKLOAD_CREATE=true`. Body: `{name, image, port?, replicas?, env[], image_pull_secret?, expose?, attach_mec?, mec_ip?, udp_ports[]}`. Creates a Deployment (+ Service on port 80 when exposed) pinned to the worker; `attach_mec` adds an `n6m-net` interface so UEs reach it over the user plane |
| PUT | `/api/v1/apps/{name}/image` | ✅ Admin | Retarget a deployed app to a chosen registry tag (date-ordered picker), preserving its other settings. Body: `{image}`. Requires `DASHBOARD_ALLOW_WORKLOAD_CREATE=true` |
| DELETE | `/api/v1/apps/{name}` | ✅ Admin | Delete an app (Deployment, Service, config ConfigMap/Secret) |
| GET | `/api/v1/apps/registry-credentials` | ✅ Admin | Local-registry host + basic-auth credentials, so an admin can `docker login` and push |
| GET | `/api/v1/apps/registry/images` | ✅ Admin | Repos and tags in the local registry, each tag with its build timestamp (newest first) |
| GET | `/api/v1/apps/starter-kit` | ✅ Admin | Zip (README + `.env.example` + `deploy.sh`) prefilled with the registry host, for an app developer |
| POST | `/api/v1/apps/provision` | ✅ Admin | Deploy the platform from the UI (phases 12 + 11), streaming progress as NDJSON |
| GET | `/api/v1/apps/gnb/console` | — | gNB management console state (configured, origin `host:port`, public URL) |
| PUT | `/api/v1/apps/gnb/console` | ✅ Admin | Expose the gNB web UI at `kelt-gnb.<base>`. Body: `{host, port?}`. Registers a selectorless Service+Endpoints `gnb` reached via the dynamic apps route. Requires `DASHBOARD_ALLOW_WORKLOAD_CREATE=true` |
| DELETE | `/api/v1/apps/gnb/console` | ✅ Admin | Remove the gNB console Service+Endpoints |

---

## WebSocket Endpoints

WebSocket connections are made to the same host on port 31880.

### Log streaming

```
WS /api/v1/ws/logs/{namespace}/{pod}?container=<name>
```

Streams live container logs. Equivalent to `kubectl logs -f`. The server closes the connection when the pod disappears or the client disconnects.

**Messages** (server → client): JSON `{line: "...", timestamp: "..."}` per log line.

### Traffic intensity

```
WS /api/v1/ws/traffic/intensity
```

Streams real-time OVS bridge counter deltas every second.

**Messages** (server → client):
```json
{
  "bridges": {
    "br-n2": {"rx_packets": 42, "tx_packets": 38, "rx_bytes": 5400, "tx_bytes": 4800},
    "br-n3": {"rx_packets": 120, ...},
    ...
  },
  "timestamp": "2026-03-11T10:00:01Z"
}
```

---

## Planned / Stubbed

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/experiments/run` | Run a test scenario |
| POST | `/api/v1/snapshot/create` | Create a testbed state snapshot |

---

## Related Documentation

- [Dashboard Overview](overview.md): architecture and security model
- [Dashboard Modules](modules.md): what each module does
- [RAN Modes](../deployment/ran-modes-dashboard.md): RAN mode switching workflow
