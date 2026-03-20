# Dashboard Modules

The dashboard has 7 modules. This page describes what each one does, what data it shows, and what actions it provides.

See [Dashboard Overview](overview.md) for architecture and access details.

---

## Module 1: Control Room

**Area**: Cluster visibility

The primary day-to-day view for monitoring the 5G namespace.

### Features

- **Live pod monitor**: real-time list of all pods in namespace `5g` with status, restart count, age, and node
- **Log streaming**: WebSocket-based live log tailing equivalent to `kubectl logs -f` — select pod + container and logs appear in the browser terminal
- **Deployment restart**: one-click rolling restart for any deployment (admin token required)
- **ConfigMap viewer**: read any ConfigMap in namespace `5g`; editing is available but disabled by default (`DASHBOARD_ALLOW_CONFIGMAP_WRITE=false`)

### What you need

- Kubernetes API accessible from ansible VM (kubeconfig at `DASHBOARD_KUBECONFIG_PATH`)
- Admin token for restart and ConfigMap write operations

---

## Module 2: Topology Map

**Area**: Cluster visibility

A visual representation of the running 5G system.

### Features

- **React Flow graph**: pod nodes and OVS bridge nodes laid out as a topology diagram
- **Interface metadata**: hover over edges to see IP address, MAC, MTU, and interface name from Multus `network-status` annotations
- **OVS flow inspection**: click on any OVS bridge node to open the bridge inspector, which shows active OpenFlow rules (`ovs-ofctl dump-flows`)
- **Live traffic animation**: edges animate and scale with real-time PPS (packets per second) data from the traffic observer

### What you need

- Kubernetes API (for pod + annotation data)
- SSH access to worker for `ovs-ofctl` commands (via `DASHBOARD_WORKER_SSH_HOST`)

---

## Module 3: Subscriber Management

**Area**: 5G-specific

Full CRUD interface for Open5GS subscriber records in MongoDB.

### Features

- **List subscribers**: table view of all registered UEs with IMSI, APN, key/opc, and status
- **Add subscriber**: form to create a new subscriber record (IMSI, security keys, slice config)
- **Edit subscriber**: modify an existing subscriber's parameters
- **Delete subscriber**: remove a subscriber (requires admin token)
- **Initialize from playbook**: trigger the Ansible subscriber import playbook (`roles/subscriber_import`) to reset to the default subscriber set

### What you need

- Direct MongoDB connection (from ansible VM to worker)
- Admin token for write operations

---

## Module 4: UE Monitoring

**Area**: 5G-specific

Real-time visibility into active UEs and their 5G sessions.

### Features

**Summary panel** (Prometheus-backed):
- Active gNBs (registered with AMF)
- RAN-connected UEs
- Active PDU sessions
- Registration success/failure counters

**UE event feed** (log-parsed):
- Parsed AMF/SMF log stream showing: UE registration, PDU session establishment, gNB attach/detach events
- Timestamped, colour-coded by event type

**Active UE table**:
- IMSI, registration state, PDU session IPs (UE-side), DNN
- Reconstructed from live AMF/SMF logs

**Connectivity tests**:
- For UERANSIM UE pods: run `ping` or `iperf3` from inside a UE pod to a target IP
- For physical UE dongles: manual command hints showing what to run inside the UE namespace

### What you need

- Prometheus accessible from ansible VM (for metrics)
- Kubernetes API (for UE pod access and log reading)
- Admin token for triggering connectivity tests

---

## Module 5: Metrics

**Area**: Cluster visibility

Infrastructure and NF-level resource metrics.

### Features

**Node metrics** (via Prometheus / Node Exporter):
- CPU usage percentage per node (master, worker, edge)
- Memory usage (used / total)
- Disk usage (used / total)

**NF metrics** (via Prometheus):
- CPU usage per NF container
- Memory usage per NF container
- Restart count per NF

**Time-series charts**: scrollable history for all metrics (configurable window).

### What you need

- Prometheus accessible from ansible VM
- Node Exporter deployed (Phase 7)

---

## Module 6: Physical RAN Config

**Area**: Infrastructure control

Automated setup and validation of the physical RAN integration (femtocell connection).

### Features

**Interface detection**:
- Lists available bridge interfaces on the worker node
- Reads `.physical_ran_bridge_applied` to show which NIC was last applied
- Shows ✓ next to the matching host NIC

**OVS bridge setup**:
- Triggers Ansible playbook to create `br-ran` and patch ports into `br-n2`/`br-n3`
- Updates AMF `n2phy` Multus annotation via the K8s API

**Vagrant command generation**:
- Generates the correct `PHYSICAL_RAN_BRIDGE=<nic> vagrant reload worker` command to bridge the host NIC into the worker VM

**Status view**:
- Shows current OVS bridge state (whether `br-ran` exists, patch ports configured)
- Shows whether AMF has the `n2phy` annotation

### What you need

- SSH access to worker (for OVS commands)
- Kubernetes API (for AMF annotation patching)
- Admin token for all operations

See [Physical RAN Integration](../deployment/physical-ran.md) for the full setup guide.

---

## Module 7: Network Health & Traffic Observer

**Area**: Infrastructure control

Per-interface connectivity checks and real-time traffic monitoring.

### Features

**Interface health cards**:
- One card per 5G interface (N2, N3, N4, N6c)
- Status indicator: healthy / degraded / unreachable
- Last latency measurement
- Live PPS (packets per second) and throughput (Bps)

**Health check**:
- "Run Health Check" button triggers immediate in-pod `kubectl exec` probes for each interface
- Results cached for 60 seconds between manual triggers

**Real-time traffic**:
- WebSocket stream of OVS bridge counter deltas (bytes and packets per bridge)
- Animated data path diagram: `UE → gNB → AMF → SMF → UPF → DN`
- Path edges animate and scale with PPS intensity from the live stream

**OVS bridge traffic counters**:
- Per-bridge: rx bytes, tx bytes, rx packets, tx packets
- Updated every second via WebSocket

### What you need

- SSH access to worker (for OVS counter polling)
- Kubernetes API (for in-pod exec probes)

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
- Shows per-VM time readings for all 4 testbed VMs (ansible, master, worker, edge)
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

- [Dashboard Overview](overview.md) — architecture, access, security, deployment
- [API Reference](api-reference.md) — full endpoint listing
- [RAN Modes](../deployment/ran-modes-dashboard.md) — switching between physical and simulated RAN
- [Physical RAN Integration](../deployment/physical-ran.md) — full physical RAN setup guide
