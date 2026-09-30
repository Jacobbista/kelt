# Dashboard Modules

The sidebar groups the modules by domain: Overview; 5G Network (Core, RAN,
Subscribers, UE Monitor); Network (Topology, Isolation, Health, Capture); Platform
(Kubernetes, Services, Metrics, Operations); then Settings and the Manual. The
[header](#header) shows where you are and what holds for the whole dashboard. This page describes
what each module does, the data it shows, and the actions it provides. Role gating
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
- Exposure stack and Edge apps cards (shown only when those layers are deployed), same idiom; selecting one opens Services or Edge apps

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

**Area**: Network · `/network/topology`

Visual map of the running system.

- Two tabs: Logical (NFs, interfaces, NADs, live traffic) and Infrastructure (cluster nodes)
- Interface and NetworkAttachmentDefinition metadata
- Live traffic indicator driven by a WebSocket stream

Read-only.

---

## RAN

**Area**: 5G

Whether the physical gNB reaches the core, where the path breaks, and what fixes
it. Admin only.

- **Path**: five links read by the backend, in order: cable (the worker's RAN
  interface and its carrier), bridge (the interface on `br-ran`), core (the gNB's
  NG Setup with the AMF), user plane, UEs. Each link is ok, broken, idle, or
  blocked by an earlier broken link, and a broken link carries its fix: a piece
  (for example `ran_link`, "Bring the RAN link up"), a CLI command, or a
  checklist. The page follows a started fix until its read-back. Every action is
  a piece and leaves a record on the Operations page.
- **Attach and detach**: Detach (`ran_detach`) sits at the card's foot while the
  RAN is attached, and asks for `detach` to be typed before it runs. Detached
  (`PHYSICAL_RAN_ENABLED=false`), the page reads *Detached* and the bridge link
  offers Attach (`ran_attach`). The worker keeps its RAN adapter either way.
- **gNB**: address, NG Setup, the AMF address and NGAP port (read from the
  running AMF), UE and PDU session counts, and the link to the gNB's own web
  console, which is where the gNB itself is monitored and configured. Drawers
  hold the console address (published at `kelt-gnb.<base>` as an external
  endpoint behind the front-door perimeter plus the appliance login) and the
  settings to enter on the gNB's RAN interface.
- **Simulated RAN** (UERANSIM): not supported yet from this page.

The page polls every 10 s and has no manual refresh. See [Physical RAN Integration](../deployment/physical-ran.md).

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

## Isolation

**Area**: Network · `/network/isolation`

What may talk to what, and what was stopped. Read-only, open to viewers; the rules
change in the playbooks, which the page names.

- 5G planes: a from/to matrix of the plane bridges on the worker. Allowed crossings
  (RAN to N2 and N3, N6c to the internet) show their packets over the last 24 h;
  blocked ones show the packets dropped, counted per ordered pair. Selecting a cell
  shows its filter rule. Beside it: drops from outside the planes and the latest
  packets the filter logged, with interfaces, addresses, protocol and rule.
- Pod network: one row per namespace phase 13 isolates, with what it holds, who may
  enter which app, and whether its outbound traffic is limited; expanding a row
  lists the destinations and the policy names.
- Check a flow: pick a source namespace (or the management network, or a node) and
  a destination Service or the internet; the backend evaluates the live policies and
  answers with the verdict and the policy behind each step. Nothing is sent.

The Overview has an Isolation card and the sidebar shows the blocked count when it
is above zero (one shared poll a minute feeds both). API: `GET /api/v1/isolation/planes`, `/planes/samples`, `/policies`,
`/targets`, `POST /api/v1/isolation/check`.

---

## Health

**Area**: Network · `/network/health`

Per-interface health of the planes (N2, N3, N4, N6c): latency, live PPS and
throughput, on-demand in-pod probes, and an animated data-path diagram driven by OVS
counter deltas. Viewers may run the checks (they only probe).

---

## Capture

**Area**: Network · `/network/capture` · admin only

Live packet capture on a chosen interface. It runs a privileged pod, so the page and
its router are admin-only.

---

## Metrics

**Area**: Cluster

Resource metrics from Prometheus, with a Nodes tab and an NFs tab.

- Nodes: per-node CPU, memory, disk cards plus CPU and memory history charts
- NFs: per-NF CPU (millicores) and memory (MB) bars plus a CPU trend chart
- Range selector: 15m, 30m, 1h, 6h, 24h

Read-only. "Open in Grafana" in the page's toolbar opens the full Grafana stack,
for what these charts do not show: Explore, the Loki logs, long ranges. It is the
only link to Grafana in the dashboard.

---

## Operations

**Area**: Platform · admin only

The 50 newest piece runs, from the dashboard and from `kelt run-piece`: when
it started, what it was, who started it and from where, the piece, the result and
how long it took. Filters: all, running, failed (a failed read-back counts as
failed). A row opens the run's steps (without Ansible's fact gathering) and its
read-back; the tail of the Ansible output is one click away, and open by itself
when the run failed. `/operations#<id>` opens that run. The header's Operations button links here.

Results: running; done (the read-back passed, or the piece has none); exited 0,
not read back yet (the read-back runs when the run is opened); done, not read
back (opened more than 10 minutes after the run ended); failed (including a
failed read-back); interrupted (the runner stopped before the end).
The record is kept as set in Settings → Operations record.

---

## Northbound

**Area**: Positioning / CAMARA

Service-management console for the northbound positioning stack. Read views are
open to `dashboard-viewer`; all write controls require `dashboard-admin`.

- Services: inventory of the camara/positioning/mec deployments (image, ready
  replicas, pod phases). Each service with a contract offers **info** (what it
  reads and who provides it) and, for admins, **Configure**. Every service also
  offers a plain **restart** (rolls the pod, config and image unchanged), the
  same generic action the 5G Core page offers per NF
- Configure (per service): reads the service's own `/contract` and shows only the
  settings the operator owns, grouped as Connection (required or secret),
  Field mapping / Documents (a file the dashboard can own), Options. Controls
  follow the contract `type` (switch for boolean, number, password for secret).
  Apply writes `<name>-config` / `<name>-secrets` by the contract `sensitive`
  flag and rolls the deployment. Deployment wiring (from `all.yml`), KELT's own
  registration env and storage paths are not offered: they are read in info.
  `ADAPTER_CAPABILITIES` (the bound source's traits, contract `type: json`) is a
  guided editor pre-filled from the registry and the schema; accuracy classes
  come from the gateway's published vocabulary. The transport the adapter uses
  to reach its source is stated from the contract, not chosen.
  A service that exposes no contract degrades to a read-only notice. Sensitive
  current values are never shown, only set/unset. See "Who owns a service's
  env" in [architecture/positioning-adapters.md](../architecture/positioning-adapters.md)
- Adapter registry: the live registry read from the engine (`GET /adapters`),
  showing each adapter's kind, `registeredVia`, last-seen, and derived state
  (live / unreachable / stale). Adapters self-register; admins can force-remove a
  stale entry. No manual name+URL registration
- Deploy adapter from image: pin an `image:tag`, port, optional `kind`, env vars
  (secret-marked vars go into a Secret), optional `imagePullSecret`; the backend
  creates the Deployment + ClusterIP Service and injects the self-registration
  env so the adapter announces itself to the engine. The catalog separates a
  singleton source (`wifi-adapter`, deployed at most once) from the generic
  `vendor-adapter`, a per-vendor template instantiated once per vendor (name it
  after the vendor, point it at the vendor API via env). Gated by the backend
  `allow_workload_create` setting on top of admin.
- Asset Identity Map: CRUD over the gateway `GET/PUT /assets` (Discover devices
  onboarding). Since engine `0.8.19` the live broadcast is derived from the
  adapters' `devices` capability, so an onboarded asset goes live as soon as its
  adapter reports it - no track-list sync needed; see
  [architecture/positioning-adapters.md](../architecture/positioning-adapters.md)
- Update all: re-runs phase 10 to the images pinned in `all.yml` and upgrades the
  catalog adapters that are behind (no manual image rollout: the pin is the intent)
- Adapter contract: the `Measurement` schema, a Python adapter skeleton, an
  `env.contract.yaml` template, and links to the upstream `5g-northbound` docs

See [architecture/positioning-adapters.md](../architecture/positioning-adapters.md).

---

## Edge apps

**Area**: Services hub → Edge apps

Console for operator-deployed application pods (phase 12). Reached from the
Services hub. Read views are open to `dashboard-viewer`; deploy/delete require
`dashboard-admin` plus the backend `allow_workload_create` gate.

- Deployed apps: name, image, ready replicas, the public link (`kelt-<name>.<base>`)
  for exposed apps, and an n6m badge for MEC-attached apps. A **logs** button streams
  the app pod's logs live (same viewer as the 5G core and Northbound services).
  Admins can delete an app, or switch it to another pushed tag from a date-ordered
  version picker; an "update available" hint appears when the registry holds a newer
  image for the tag
- Deploy from image: a registry image (`<host>/name:tag`), port, replicas, env
  vars (secret-marked go into a Secret), optional `imagePullSecret`, an **expose**
  toggle, and an **attach to MEC network (n6m)** toggle (optional fixed IP + extra
  UDP ingest ports) so UEs reach the app over the 5G user plane. The backend creates
  a worker-pinned Deployment and, when exposed, a port-80 Service the front-door
  reaches at `kelt-<name>.<base>`
- Starter kit: admins download a zip (README + `.env.example` + `deploy.sh`,
  prefilled with the registry host) to hand to an app developer

The image must be pushed to the in-cluster local registry first. See
[architecture/edge-apps.md](../architecture/edge-apps.md).

---

## IAM

**Area**: Settings → Identity & Access · admin only

A static reference for the identity model. No write actions; realm changes happen in the Keycloak console.

- Realm info: name, issuer, current user and roles
- Role matrix: `dashboard-admin`, `dashboard-viewer`, `camara-location-read`, with abilities and restrictions
- Seed users (phase 08) and OIDC clients (dashboard, positioning-demo, camara-gateway, dashboard-readonly)
- Links to the Keycloak realm and master admin consoles; M2M `client_credentials` curl snippets are shown to admins only

See [security/iam.md](../security/iam.md) for the full role matrix.

---

## Storage

**Area**: Settings → Storage

Disk state for the worker node, and the actions that reclaim space.

The page leads with a breakdown rather than a percentage, because the intuitive
culprit is usually the wrong one: nearly all of a node's disk is extracted
container image layers, while the in-cluster registry (the thing an operator
tends to blame) holds a fraction of that. Each consumer is listed separately,
with the registry called out as a subset of the volumes so the relative size is
visible, and persistent volumes broken down per claim.

Sizes come from walking the filesystem, so they are measured on request and
cached rather than polled. The filesystem totals are always live.

Reclaim actions, in the order they appear (most effective first):

| Action | Frees | Notes |
|--------|-------|-------|
| Prune unused images | GB | Removes image layers no container references |
| Vacuum journals | MB to GB | Trims systemd logs to the cap phase 01 configures |
| Registry garbage collect | MB | Unreferenced blobs; offers a dry run first |

Each action shows what it would free before it is run, and is disabled when the
answer is nothing, so no button is a blind click. Those figures are estimates
(shared image layers are counted once per image); the amount actually freed is
measured from the filesystem after the action and reported back.

Reading is open to `dashboard-viewer`; every action requires `dashboard-admin`
and an explicit confirmation. See [api-reference.md](api-reference.md) for the
endpoints and [../security/iam.md](../security/iam.md) for the role matrix.

## Operations record

**Area**: Settings → Operations record · admin only

What the record of piece runs holds (runs, size on disk, oldest run) and how long
it is kept: a number of days (1 to 3650, default 30) and a total size in MB (1 to
10240, default 50), whichever is reached first. A running operation is never
deleted. The values are stored in `.testbed.env` (`KELT_OPS_MAX_AGE_DAYS`,
`KELT_OPS_MAX_MB`) and apply at the next run.

## Header

**Area**: Infrastructure visibility

A 48 px bar above every page. Left, the breadcrumb: a top-level page shows its
sidebar group as plain text (groups have no page), a sub-page shows its parents as
links (Services / Northbound / Assets). Right, in order:

- **Status**: one pill for the whole testbed, from `GET /api/v1/status/summary`,
  polled every 30 s. Green "All systems up", or amber/red with the number of
  problems; a click lists them, with a button to Health. Sources: the Kubernetes
  API itself, nodes not Ready, 5G pods Failed, Pending, not ready or stuck
  (CrashLoopBackOff, image pull errors), the last network check run if younger
  than 10 minutes, with its age (the pill never starts one; Health does), the AMF
  CNI alert. Hidden, with the breadcrumb, until there is a session.
- **Operations** (admin): how many pieces are running, with the loader while one
  is. A click lists at most three running operations and the last result, and
  links to the [Operations](#operations) page.
- **Updates** (admin): shown only while a dashboard component has an update
  available; opens the update section of the Manual.
- **Environment**: DEV or PROD, the frontend source in its tooltip.
- **Cluster clock** and the time sync popover (below).
- **Account menu**: user, what the role allows, tenant scope; the frontend source;
  the switch that starts the dev frontend (admin, on prod); log out.

### Cluster clock and time sync

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
- [Physical RAN Integration](../deployment/physical-ran.md): full physical RAN setup guide
