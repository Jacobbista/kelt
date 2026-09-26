## Phase 6 — UERANSIM & MEC Setup

### Overview

This phase deploys simulated 5G RAN components using UERANSIM and an optional MEC (Multi-Access Edge Computing) application. The deployment is topology-driven (see `vars/topology.yml`).

- **gNB** (simulated 5G base station) on edge node by default
- **UE** (simulated user equipment) on edge node by default
- **MEC application** (optional, disabled by default) on edge node

UERANSIM provides a lightweight, open-source 5G RAN simulator that enables end-to-end testing of the 5G Core deployed in Phase 5.

### Theoretical Background

#### UERANSIM Architecture

UERANSIM consists of two main components:

| Component | Role                                                   | Interfaces         |
| --------- | ------------------------------------------------------ | ------------------ |
| **gNB**   | Simulates a 5G base station (gNodeB)                   | N2 (AMF), N3 (UPF) |
| **UE**    | Simulates a user device (smartphone, IoT sensor, etc.) | NAS (via gNB)      |

**Key protocols**:

- **NGAP** (N2): Control-plane signaling between gNB and AMF (SCTP/port 38412)
- **GTP-U** (N3): User-plane data tunneling between gNB and UPF (UDP/port 2152)
- **NAS**: Non-Access Stratum signaling between UE and AMF (encapsulated in NGAP)

#### Registration & PDU Session Flow

```
1. UE → gNB → AMF: Registration Request (attach to network)
2. AMF ↔ AUSF ↔ UDM: Authentication (5G-AKA)
3. AMF → UE: Registration Accept (UE is now connected)
4. UE → AMF → SMF: PDU Session Establishment Request
5. SMF → UPF: PFCP Session Establishment (setup forwarding rules)
6. SMF → UE: PDU Session Accept (UE gets IP address, TUN interface created)
7. UE: Traffic flows through uesimtun0 → GTP-U tunnel → UPF → Internet
```

#### MEC (Multi-Access Edge Computing)

MEC applications run on the edge node and are accessed via a dedicated UPF (UPF-Edge) with low-latency N6 connectivity.

**Current status**: MEC is **disabled by default** due to the UPF-Edge CNI route conflict (see `docs/known-issues/upf-edge-cni-route-conflict.md`).

### Implementation Details

#### Role Structure

```
roles/
├── infrastructure_setup/       # SCTP module, image pre-pull
├── gnb_deployment/             # gNB ConfigMap, StatefulSet, Headless Service
├── ue_deployment/              # UE StatefulSet (per cell)
└── connectivity_validation/    # UE registration & connectivity tests
```

Each role follows Ansible best practices:

- **defaults/main.yml**: All configurable variables
- **templates/**: Jinja2 templates for Kubernetes manifests and UERANSIM configs
- **tasks/main.yml**: Deployment logic using `kubernetes.core.k8s` API

#### Topology & Templates

**Topology** (`vars/topology.yml`):

- One cell == one gNB
- Per-gNB slices declared as `{ sst, sd, dnn }`
- UEs list with `supi_suffix`, `apn`, `slice` and optional `node`

**gNB Configuration** (`gnb-config.yaml.j2`):

**gNB Configuration** (`gnb-config.yaml.j2`):

- MCC/MNC: Mobile Country/Network Code (001/01 for testing)
- NCI: NR Cell Identity
- AMF address: N2 interface IP from Phase 5
- Slices: SST=1 (eMBB), SST=2 (MEC, optional)

**UE StatefulSet** (`ue-statefulset.yaml.j2`):

- Per-cell StatefulSet with `replicas = len(cell.ues)`
- Init container generates per-replica config from topology (unique SUPI/APN/SST/SD)
- Uses headless service of the gNB via DNS

#### Network Attachments

**gNB**:

- N2 (NGAP): `n2-cell-{id}` → `br-n2-cell-{id}` (per-cell L2 domain)
- N3 (GTP-U): `n3-cell-{id}` → `br-n3-cell-{id}` (per-cell L2 domain)

**UE**:

- N2 secondary interface via `n2-cell-{id}` (same L2 as its gNB)
- Creates `uesimtun0` TUN interface after PDU session establishment

**MEC** (optional):

- N6e: `10.206.0.x/24` (edge data network)

### Expected Results

After Phase 6 (with 1 cell and 2 UEs):

```bash
# gNB and UE pods running (edge placement)
kubectl get pods -n 5g
# NAME                   READY   STATUS    RESTARTS   AGE
# gnb-xxx                1/1     Running   0          2m
# ue-xxx                 1/1     Running   0          2m

# Check UE registration logs
kubectl logs -n 5g ue-xxx | grep -i "registration\|pdu"
# [info] Registration accept
# [info] PDU Session establishment accept

# Check UE TUN interface
kubectl exec -n 5g ue-xxx -- ip addr show uesimtun0
# uesimtun0: inet 10.45.0.2/16 ...

# Test Internet connectivity
kubectl exec -n 5g ue-xxx -- ping -c 3 -I uesimtun0 8.8.8.8
# 64 bytes from 8.8.8.8: icmp_seq=1 ttl=119 time=12.3 ms
```

### Architecture Highlights

1. **Modular roles**: Each component (gNB, UE, MEC) is a separate role
2. **Template-based**: All configs and manifests are Jinja2 templates (topology-driven)
3. **API-driven**: Uses `kubernetes.core.k8s` (no shell/kubectl)
4. **Proper dependencies**: UE init container renders configs; gNB readiness via probes (no hardcoded sleeps)
5. **Readiness probes**: gNB has TCP readinessProbe on NGAP port (38412)
6. **Validation included**: Automatic checks for UE registration and connectivity
7. **MEC-ready**: MEC deployment role exists (disabled by default due to UPF-Edge issue)

### DNS-based gNB Discovery (Endpoints Sync)

This phase includes a minimal controller to keep headless Service Endpoints aligned with the gNB Multus N2 IP:

- Role: `roles/endpoints_sync` (applies RBAC + CronJob)
- Source of truth: Multus annotation `k8s.v1.cni.cncf.io/network-status`
- Schedule: `*/1 * * * *` (configurable via `sync_schedule`)
- Security: namespace-scoped Role with read pods + create/patch Endpoints

How it works:

1. List pods labeled `component=gnb` in namespace `5g`
2. Extract N2 IP from the Multus `network-status` annotation
3. Patch or create Endpoints matching the gNB StatefulSet/Service name
4. UEs resolve `gnb-N.5g.svc.cluster.local` to the real N2 IP (no kube-proxy hop)

Verify:

```bash
# Check CronJob
kubectl -n 5g get cronjob gnb-endpoints-sync

# Inspect Endpoints for gNB-1
kubectl -n 5g get endpoints gnb-1 -o yaml | grep -A2 addresses:
# addresses:
# - ip: 10.202.1.11  # Multus N2 IP
```

Notes:

- Enabled in playbook with tag `endpoints_sync`
- Assumes 1 worker + 1 edge (see inventory groups); multi-node support will follow

### Assumptions & Limits

- Inventory groups include exactly one `worker` and one `edge` host (generated by the Vagrantfile from `node_ips` in `group_vars/all.yml`).
- Node placement uses `kubernetes.io/hostname: worker|edge`.
- Current version targets 1 worker + 1 edge; multi-node per group will be added later.

### Troubleshooting Checklist

| Check                | Expected                                              |
| -------------------- | ----------------------------------------------------- | ---------------------------------------- |
| **SCTP module**      | `lsmod                                                | grep sctp` on worker shows module loaded |
| **gNB registration** | `kubectl logs -n 5g gnb-xxx                           | grep "NG Setup"` succeeds                |
| **UE registration**  | `kubectl logs -n 5g ue-xxx                            | grep "Registration accept"`              |
| **PDU session**      | `kubectl logs -n 5g ue-xxx                            | grep "PDU Session"` shows success        |
| **TUN interface**    | `kubectl exec -n 5g ue-xxx -- ip addr show uesimtun0` |
| **Connectivity**     | `ping -I uesimtun0 8.8.8.8` from UE pod works         |

### Common Issues

**Issue**: gNB fails to register with AMF  
**Cause**: SCTP module not loaded or N2 network not reachable  
**Fix**:

```bash
# On worker node
sudo modprobe sctp
kubectl exec -n 5g gnb-xxx -- ping -c 3 10.202.0.100  # AMF N2 IP
```

**Issue**: UE fails PDU session establishment  
**Cause**: Subscriber not in MongoDB or authentication keys mismatch  
**Fix**: Verify subscriber import (Phase 5) and check keys in `ue-config.yaml`

**Issue**: UE has no Internet connectivity  
**Cause**: UPF not forwarding traffic or NAT not configured  
**Fix**:

```bash
# Check UPF logs
kubectl logs -n 5g upf-cloud-xxx | grep -i "session\|tun"
# Verify UPF ogstun interface
kubectl exec -n 5g upf-cloud-xxx -- ip addr show ogstun
```

### Customization

#### Change UE subscriber

Edit `roles/ue_deployment/defaults/main.yml`:

```yaml
ue_config:
  supi: "imsi-001011234567896" # New IMSI
  key: "your-key-here"
  op: "your-op-here"
```

Then re-import subscriber in Phase 5 and redeploy UE.

#### MEC apps

MEC applications are deployed by phase 12 through the dashboard Apps page, not
by this phase. A UE reaches them from its default `internet` session; to also open
a `mec` session, uncomment it in `roles/ue_deployment/defaults/main.yml`:

```yaml
sessions:
  - { type: "IPv4", apn: "internet", sst: 1, sd: 1 }
  - { type: "IPv4", apn: "mec", sst: 2, sd: 1 } # MEC session
```

See docs/architecture/5g-interfaces.md (Data Networks).

### Next Steps

After Phase 6, the testbed is fully operational:

- Run end-to-end tests (`tests/` directory)
- Monitor metrics (Prometheus endpoints on port 9090)
- Experiment with network slicing, QoS policies, etc.

### References

- **UERANSIM**: https://github.com/aligungr/UERANSIM
- **3GPP TS 38.413**: NG-RAN; NG Application Protocol (NGAP)
- **3GPP TS 38.300**: NR and NG-RAN Overall Description
