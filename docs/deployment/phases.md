# Deployment Phases

The testbed is deployed in multiple sequential phases, each building on the previous.

## Phase Overview

Phases fall into three classes. **Core** phases always run. **Optional** phases are addons not required for a working core. Some Core phases are **edge-conditional** (behavior depends on `edge_enabled`). See [status.md](../status.md) for the core versus optional split.

| Phase | Name | Class | Description |
| ----- | ---- | ----- | ----------- |
| 1  | Infrastructure     | Core                          | System packages, Open vSwitch |
| 2  | Kubernetes         | Core                          | K3s cluster |
| 3  | KubeEdge           | Core (edge-conditional)       | CloudCore always; EdgeCore when `edge_enabled` |
| 4  | Overlay Network    | Core (edge-conditional)       | Multus, OVS bridges, VXLAN tunnels |
| 5  | 5G Core            | Core                          | Open5GS network functions, MongoDB |
| 6  | UERANSIM & MEC     | Optional (`DEPLOY_MODE=full`) | Simulated gNB and UE |
| 7  | Observability      | Core                          | Prometheus, Loki, Grafana |
| 8  | IAM                | Core                          | Keycloak realm and PostgreSQL |
| 9  | Dashboard          | Core                          | Out-of-band FastAPI + React control plane |
| 10 | Northbound         | Optional addon                | CAMARA Location API gateway, positioning engine, and demo |
| 11 | Front-door         | Core (base-domain-conditional) | Single-origin nginx edge; serves the catalogue at `kelt.<base>` and routes each `kelt-<name>.<base>` by Host. No-op unless `external_base_domain` is set |
| 12 | Apps               | Optional addon (`apps_enabled`) | Edge apps platform: in-cluster local registry plus the namespace for deploy-from-image app pods. Pairs with the phase 11 dynamic `kelt-<name>.<base>` route |

Optional addons are off by default (opt-in). Phase 6 (UERANSIM) is gated by `ueransim_enabled`, set automatically by `DEPLOY_MODE=full` or by `testbed run-phase 06-ueransim-mec`. Phase 10 (Northbound) bundles the CAMARA gateway, positioning engine, and demo into one phase with roles selectable by tag (`camara`, `positioning`, `placement`, `demo`); the parts are gated by `camara_enabled` / `positioning_enabled` / `positioning_demo_enabled` / `placement_editor_enabled` in `all.yml`, and the umbrella `testbed northbound on` enables them together. Phase 12 (Apps) is gated by `apps_enabled`, set by `testbed apps on`. See [gaps.md](../gaps.md) for the remaining CAMARA/positioning rework.

## Running Phases

### Provisioning Flags (`vagrant up`)

| Flag          | Values              | Default     | Behavior                                                                |
| ------------- | ------------------- | ----------- | ----------------------------------------------------------------------- |
| `DEPLOY_MODE` | `core_only`, `full` | `core_only` | `core_only`: all phases except 6; `full`: also phase 6 (UERANSIM + MEC) |
| `TESTBED_PROFILE` | `laptop`, `server` | `laptop` | `server` creates 3 VMs with optimized resources; `laptop` creates 4 VMs including edge |
| `EDGE_ENABLED` | `true`, `false` | `true` (laptop) / `false` (server) | Controls edge VM creation and all edge-related Ansible tasks |

These flags can be set via environment variables or managed with [`testbed-config`](../tools/testbed-config.md). See [Server / NUC Deployment](server-setup.md) for server-specific guidance.

Examples:

```bash
# Core only
vagrant up

# Full stack with UERANSIM
DEPLOY_MODE=full vagrant up
```

### Full Deployment

```bash
# Runs all phases
DEPLOY_MODE=full vagrant up
```

### Core Only (Default)

```bash
# Runs all phases except 6 (UERANSIM)
DEPLOY_MODE=core_only vagrant up
```

### Specific Phases

```bash
# Run a specific phase
testbed run-phase 02-kubernetes

# Run part of a phase by tag
testbed run-phase 04-overlay-network overlay
```

`testbed run-phase` runs from the host and loads the persisted configuration and
secrets, which a hand-run `ansible-playbook` inside the VM does not. Extra
positional arguments select tags and set variables; see
[QUICKSTART](https://github.com/Jacobbista/kelt/blob/main/QUICKSTART.md#two-interfaces-one-behavior).

---

## Phase 1: Infrastructure

**Location**: `ansible/phases/01-infrastructure/`

### What it does

- Installs system packages (curl, wget, jq, etc.)
- Installs and configures Open vSwitch
- Configures kernel parameters (IP forwarding, etc.)
- Sets up systemd-networkd

### Key files

- `roles/infra_setup/tasks/main.yml`
- `roles/infra_setup/templates/ovs-system.conf.j2`

### Verify

```bash
vagrant ssh worker
sudo ovs-vsctl --version
```

---

## Phase 2: Kubernetes

**Location**: `ansible/phases/02-kubernetes/`

### What it does

- Deploys K3s server on master node
- Deploys K3s agents on worker and edge nodes
- Configures kubeconfig
- Verifies cluster health

### Key files

- `roles/k3s_master/tasks/main.yml`
- `roles/k3s_agent/tasks/main.yml`

### Verify

```bash
vagrant ssh master
sudo k3s kubectl get nodes
```

---

## Phase 3: KubeEdge

**Location**: `ansible/phases/03-kubeedge/`

> **Edge-conditional**: EdgeCore deployment and edge node verification are skipped when `edge_enabled=false` (server profile without edge). CloudCore on the worker is always deployed.

### What it does

- Deploys CloudCore on worker node
- Deploys EdgeCore on edge node (when edge enabled)
- Configures edge-cloud communication (WebSocket port 10000)
- Labels edge node

### Key files

- `roles/kubeedge_cloudcore/tasks/main.yml`
- `roles/kubeedge_edgecore/tasks/main.yml`

### Verify

```bash
sudo k3s kubectl get nodes
# Edge node should show: agent,edge roles
```

---

## Phase 4: Overlay Network

**Location**: `ansible/phases/04-overlay-network/`

> **Edge-conditional**: When `edge_enabled=false`, OVS bridges are created locally on the worker without VXLAN tunnels. Multus NADs and the OVS CNI still function for pods running on the worker. Edge-specific DaemonSets and Multus configuration are skipped.

### What it does

- Installs CNI binaries
- Deploys Multus CNI DaemonSets
- Creates OVS bridges (br-n1, br-n2, br-n3, br-n4, br-n6e, br-n6c, br-n6m)
- Establishes VXLAN tunnels between worker and edge (when edge enabled)
- Creates NetworkAttachmentDefinitions (NADs), including `n6m-net` in the `mec` namespace for MEC services
- Creates per-cell networks

### Key files

- `roles/ovs_network_setup/tasks/main.yml`
- `roles/multus_install/tasks/main.yml`
- `roles/cell_network_setup/tasks/main.yml`
- `scripts/ovs-setup.sh`

### Verify

```bash
# Check OVS bridges
vagrant ssh worker
sudo ovs-vsctl show

# Check NADs
sudo k3s kubectl get net-attach-def -A
```

---

## Phase 5: 5G Core

**Location**: `ansible/phases/05-5g-core/`

### What it does

- Creates 5g namespace
- Deploys MongoDB
- Deploys Open5GS Network Functions:
  - NRF (NF Repository Function)
  - AMF (Access and Mobility Management)
  - SMF (Session Management Function)
  - UPF (User Plane Function) - cloud and edge
  - UDM, UDR, AUSF, PCF, BSF, NSSF
- Imports subscriber data
- Validates NF connectivity

### Key files

- `roles/infrastructure_setup/tasks/main.yml`
- `roles/nf_deployments/tasks/main.yml`
- `roles/subscriber_import/tasks/main.yml`
- `configs/*.yaml` - NF configurations

### Verify

```bash
sudo k3s kubectl get pods -n 5g
sudo k3s kubectl logs -n 5g deploy/amf -c amf --tail=20
```

---

## Phase 6: UERANSIM

**Location**: `ansible/phases/06-ueransim-mec/`

Optional. Runs only with `DEPLOY_MODE=full`.

### What it does

- Creates discovery token for edge pods
- Deploys gNB (base station simulator)
- Deploys UE (user equipment simulator)
- Configures dynamic IP discovery via init containers
- Optionally deploys MEC applications

### Key files

- `roles/infrastructure_setup/tasks/main.yml` - Discovery token
- `roles/gnb_deployment/tasks/main.yml`
- `roles/ue_deployment/tasks/main.yml`
- `vars/topology.yml` - Cell and UE configuration

### Topology Configuration

```yaml
# vars/topology.yml
ueransim_topology:
  cells:
    - id: 1
      gnb:
        name: "gnb-1"
        node: "edge"
        nci: "0x000000010"
        tac: 1
      ues:
        - { id: 1, supi_suffix: "895", apn: "internet" }
        - { id: 2, supi_suffix: "896", apn: "internet" }
```

### Verify

```bash
# Check gNB
sudo k3s kubectl get pods -n 5g -l app=gnb-1

# Check UEs
sudo k3s kubectl get pods -n 5g -l app=ue

# Check AMF logs for registrations
sudo k3s kubectl logs -n 5g deploy/amf -c amf | grep -i "registered"
```

---

## Phase 7: Observability Stack

**Purpose**: Deploy monitoring and logging infrastructure.

**Components**:

- Prometheus (metrics collection)
- Loki (log aggregation)
- Grafana (visualization)
- Node Exporter (node metrics)
- Promtail (log shipping)
- Traffic Capture (optional, PCAP)

### Deploy

```bash
# Deploy observability stack
testbed run-phase 07-observability

# With traffic capture enabled
testbed run-phase 07-observability deploy_traffic_capture=true
```

### Access

| Service    | URL                        | Credentials     |
| ---------- | -------------------------- | --------------- |
| Grafana    | http://192.168.56.11:30300 | admin / admin5g |
| Prometheus | http://192.168.56.11:30090 | -               |

### Pre-built Dashboards

- **Cluster Overview** - Node health, resource usage
- **5G Core** - NF status, logs, metrics

### Verify

```bash
# Check pods
sudo k3s kubectl get pods -n monitoring

# Check Grafana
curl http://192.168.56.11:30300/api/health
```

---

## Phase 8: IAM (Keycloak)

**Location**: `ansible/phases/08-iam/`

### What it does

- Deploys Keycloak and its PostgreSQL backend
- Creates the `5g-testbed` realm with the dashboard and CAMARA OIDC clients
- Defines the role model (`dashboard-admin`, `dashboard-viewer`, `camara-location-read`)
- Seeds two end users (`admin`, `viewer`) with temporary passwords
- Realm reconcile behavior is gated (`ask` / `on` / `off`) via `testbed iam reconcile`

### Verify

```bash
sudo k3s kubectl get pods -n iam
```

See [security/iam.md](../security/iam.md) for the realm structure and role matrix.

---

## Phase 9: Dashboard Control Plane

**Purpose**: Prepare an out-of-band dashboard on the ansible VM without impacting namespace `5g` runtime.

### Deploy / Reconcile

```bash
# Prepare dashboard workspace and dependencies
testbed run-phase 09-dashboard
```

### Development mode (live reload)

```bash
testbed run-phase 09-dashboard dashboard_mode=dev
```

### What it does

- In `prod` mode, synchronizes `dashboard/` into writable `/home/vagrant/dashboard-work`
- Installs backend and frontend dependencies
- Builds frontend bundle with backend API URL
- Creates and starts systemd services on ansible VM
- Keeps dashboard outside `5g` namespace workload path
- Supports a single active runtime profile:
  - `prod` (default): stable runtime from `/home/vagrant/dashboard-work`
  - `dev`: live reload runtime from `/vagrant/dashboard`

### Services

```bash
sudo systemctl status dashboard-backend
sudo systemctl status dashboard-frontend
sudo systemctl restart dashboard-backend dashboard-frontend
```

### Access

Access URLs (cluster baseline, dev frontend, API): see [Dashboard Overview](../dashboard/overview.md#access).

---

## Phase 10: Northbound (CAMARA + Positioning) (optional)

**Location**: `ansible/phases/10-northbound/`

Optional addon. A single phase that bundles the CAMARA Location API gateway, the positioning engine, and the positioning demo. Images are built in the `5g-northbound` companion repository and pulled by tag. The umbrella `testbed northbound on` enables the parts together; each is independently gated by its flag in `all.yml`.

Roles under this phase:

| Role | Tag | Gated by | Purpose |
| ---- | --- | -------- | ------- |
| `camara_gateway` | `camara` | `camara_enabled` | CAMARA Location API gateway in front of the 5G core |
| `positioning_engine` | `positioning` | `positioning_enabled` | Positioning engine with a pluggable adapter model |
| `placement_editor` | `placement` | `placement_editor_enabled` | Placement-editor geometry UI and its oauth2-proxy gate (`frontdoor_gate`) |
| `positioning_demo` | `demo` | `positioning_demo_enabled` | Positioning demo SPA that exercises the CAMARA Location API end to end |

Run a single piece with a tag:

```bash
testbed run-phase 10-northbound camara
```

See [architecture/positioning-adapters.md](../architecture/positioning-adapters.md).

---

## Troubleshooting Phases

### Re-run a Phase

```bash
testbed run-phase 04-overlay-network
```

### Skip Phases

A phase is skipped by turning off its feature flag rather than by skipping a tag,
so the choice persists across runs: see the toggles above and `testbed help`.

### Debug Mode

Verbose output needs the playbook run directly, which is the one case where the
manual invocation is still the right tool. Export the secrets first so the run
behaves like a normal one:

```bash
vagrant ssh ansible
cd ~/ansible-ro
set -a; . /vagrant/.testbed.env; . /vagrant/.testbed.secrets; set +a
ansible-playbook phases/05-5g-core/playbook.yml -vvv
```

## Related Documentation

- [Architecture Overview](../architecture/overview.md)
- [Network Topology](../architecture/network-topology.md)
- [Physical RAN Integration](physical-ran.md)
- [Troubleshooting](../operations/troubleshooting.md)
