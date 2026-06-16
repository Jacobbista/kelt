# Dashboard Modules

The dashboard has 11 modules, reachable from the sidebar. This page describes
what each one does, the data it shows, and the actions it provides. Role gating
follows the two-tier model: read views are open to `dashboard-viewer`, write and
exec actions require `dashboard-admin`. See [security/iam.md](../security/iam.md)
for the per-route matrix.

See [Dashboard Overview](overview.md) for architecture and access details.

---

## Overview

**Area**: Cluster

The landing view. Cluster-wide status at a glance.

- Stat cards: total pods, running, pending, failed, average CPU, average memory
- CPU and memory sparklines (15-minute trend)
- Node cards with status and resource usage
- Network Function status cards; selecting one opens its detail in the 5G Core module

Read-only.

---

## Kubernetes

**Area**: Cluster

Raw Kubernetes resource browser, namespace-scoped.

- Five tabs: Namespaces, Nodes, Storage, Services, Events
- Namespace filter on the Storage, Services, and Events tabs
- Tables: namespace phase and labels; node roles, taints, kubelet version; PVCs; Services with ports and selectors; recent Events
- Manual refresh plus auto-refresh every 15 seconds

Read-only.

---

## 5G Core

**Area**: 5G

Per-NF view of the Open5GS core, grouped into Control Plane, User Plane, Data, and Other.

- NF cards with phase, restart count, and node placement; expandable for detail
- AMF CNI alert banner with a "Manage" action to scale the AMF controllers (repair path for the CNI/replicaset issue)
- "Check updates" compares deployed image tags against a version manifest

Admin actions: restart an NF deployment, scale the AMF controller, trigger a streamed NF image update.

---

## Topology

**Area**: Network

Visual map of the running system.

- Two tabs: Logical (NFs, interfaces, NADs, live traffic) and Infrastructure (cluster nodes)
- Interface and NetworkAttachmentDefinition metadata
- Live traffic indicator driven by a WebSocket stream

Read-only.

---

## RAN

**Area**: 5G

RAN attachment control, with a tab per mode.

- "Physical RAN" tab: detect host bridge interfaces, create `br-ran` and patch it into `br-n2`/`br-n3`, patch the AMF `n2phy` annotation, and show the resulting OVS and annotation state; generates the `PHYSICAL_RAN_BRIDGE=<nic> vagrant reload worker` command
- "UERANSIM" tab: simulated RAN controls

Admin actions: bridge setup and RAN configuration changes. See [Physical RAN Integration](../deployment/physical-ran.md) and [RAN Modes](../deployment/ran-modes-dashboard.md).

---

## Subscribers

**Area**: 5G

CRUD for Open5GS subscriber records in MongoDB.

- Expandable list by IMSI, with slice and session detail (SST, SD, APN, QCI, AMBR)
- Subscriber form: IMSI, K, OP/OPc, AMF, aggregate AMBR, default slice
- Import from JSON, and "Initialize from playbook" to reset to the default subscriber set (phase 5 import)

Admin actions: create, edit, delete, import, initialize.

---

## UE Monitor

**Area**: 5G

Live view of registered UEs and RAN activity.

- Summary cards: connected gNBs, RAN UEs, active sessions, registered subscribers
- Registration counters over a selectable window (1m to 6h), with auth-reject context
- gNB table (id, PLMN, SCTP peer, UE count) and active UE table with per-IMSI nickname and icon personalization
- Event feed (registration, session, attach, detach, errors) with expandable cause and debug guidance
- Connectivity tests (ping, iperf3) from a selected UERANSIM pod

Admin actions: run ping or iperf3, edit UE personalization. UE session data comes from a native Open5GS endpoint.

---

## Diagnostics

**Area**: Network

Connectivity and traffic inspection.

- "Network Health" tab: per-interface health (N2, N3, N4, N6c), latency, live PPS and throughput, on-demand in-pod probes, and an animated data-path diagram driven by OVS counter deltas
- "Packet Sniffer" tab: live packet capture

Admin actions: run health checks and packet captures.

---

## Metrics

**Area**: Cluster

Resource metrics from Prometheus, with a Nodes tab and an NFs tab.

- Nodes: per-node CPU, memory, disk cards plus CPU and memory history charts
- NFs: per-NF CPU (millicores) and memory (MB) bars plus a CPU trend chart
- Range selector: 15m, 30m, 1h, 6h, 24h

Read-only. A "Grafana (advanced)" link in the sidebar opens the full Grafana stack.

---

## Northbound

**Area**: Positioning / CAMARA

Service-management console for the northbound positioning stack. Read views are
open to `dashboard-viewer`; all write controls require `dashboard-admin`.

- Services: inventory of the camara/positioning/mec deployments (image, ready
  replicas, pod phases). Each service offers a guided **Configure** action (admin)
- Guided setup (per service): reads the service's own `/contract` and walks the
  operator through its fields in order, required then recommended then optional,
  each with description and example. Apply routes every value by the contract
  `sensitive` flag (Secret for sensitive, ConfigMap otherwise, both via envFrom)
  and rolls the deployment. A service that exposes no contract degrades to a
  read-only notice. Sensitive current values are never shown, only set/unset
- Adapter registry: the live registry read from the engine (`GET /adapters`),
  showing each adapter's kind, `registered_via`, last-seen, and derived state
  (live / unreachable / stale). Adapters self-register; admins can force-remove a
  stale entry. No manual name+URL registration
- Deploy adapter from image: pin an `image:tag`, port, optional `kind`, env vars
  (secret-marked vars go into a Secret), optional `imagePullSecret`; the backend
  creates the Deployment + ClusterIP Service and injects the self-registration
  env so the adapter announces itself to the engine. The catalog separates a
  singleton source (`wifi-positioning`, deployed at most once) from the generic
  `rest-adapter`, a per-vendor template instantiated once per vendor (name it
  after the vendor, point it at the vendor API via env). Gated by the backend
  `allow_workload_create` setting on top of admin.
- Fusion config: edit `FUSION_STRATEGY` / `FUSION_COMPARE` / `DEVICE_MAP`
- Managed image rollout: retarget gateway / engine / demo to a new image
- Adapter contract: the `Measurement` schema, a Python adapter skeleton, an
  `env.contract.yaml` template, and links to the upstream `5g-northbound` docs

See [architecture/positioning-adapters.md](../architecture/positioning-adapters.md).

---

## IAM

**Area**: Access · admin only

A static reference for the identity model. No write actions; realm changes happen in the Keycloak console.

- Realm info: name, issuer, current user and roles
- Role matrix: `dashboard-admin`, `dashboard-viewer`, `camara-location-read`, with abilities and restrictions
- Seed users (phase 08) and OIDC clients (dashboard, positioning-demo, camara-gateway, dashboard-readonly)
- Links to the Keycloak realm and master admin consoles; M2M `client_credentials` curl snippets are shown to admins only

See [security/iam.md](../security/iam.md) for the full role matrix.

---

## Sidebar: Cluster Clock & Time Sync

**Area**: Infrastructure visibility

The sidebar footer includes a live clock and a time synchronization monitor.

### Features

**Live clock**:
- Displays current time in the user's local timezone (e.g. `01:32:05 CET`)
- Synced to the backend server time (corrected for browser-server drift via the `/health` endpoint, polled every 5 seconds)
- Starts ticking immediately on page load using the browser clock, then silently corrects when the first server response arrives

**Time Sync popover** (click the clock to open):
- Shows per-VM time readings for all testbed VMs (ansible, master, worker, edge)
- Times tick forward live in the browser
- Offset column shows drift relative to the ansible VM (reference clock)
- Color-coded: green (< 500ms), amber (500ms-2s), red (> 2s)
- Max drift summary and IN SYNC / DRIFT DETECTED badge
- Auto-refreshes every 30 seconds while open

**Automatic drift correction**:
- When the popover detects drift (> 1 second), it automatically triggers `chronyc makestep` on all VMs via `POST /api/v1/time/force-sync`
- Auto-correction fires once per popover open to prevent loops
- A manual "Force Sync" button also appears when drift is detected, for on-demand correction
- The endpoint SSHs to each VM, runs the sync command, and returns updated time readings

### What you need

- SSH access from ansible VM to all nodes (for time reads and force-sync)
- `chrony` installed on all VMs (deployed by Phase 1)
- `sudo` access for `chronyc` on remote nodes (configured by Phase 1)

---

## Planned / Stubbed

The following endpoints are stubbed for future modules:

| Endpoint | Planned purpose |
|----------|----------------|
| `POST /api/v1/experiments/run` | Run automated test scenarios (E2E, performance) |
| `POST /api/v1/snapshot/create` | Create a point-in-time snapshot of the testbed state |

---

## Related Documentation

- [Dashboard Overview](overview.md): architecture, access, security, deployment
- [API Reference](api-reference.md): full endpoint listing
- [RAN Modes](../deployment/ran-modes-dashboard.md): switching between physical and simulated RAN
- [Physical RAN Integration](../deployment/physical-ran.md): full physical RAN setup guide
