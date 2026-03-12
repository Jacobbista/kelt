## Phase 4 — Multus-Based 5G Overlay on K3s

### Overview

Builds a secondary overlay network stack for 5G-like data-plane traffic, on top of K3s' primary Flannel CNI.

This phase introduces:

- **Multus CNI**: for multiple network interfaces per pod
- **Whereabouts**: for cluster-wide IP management
- **Open vSwitch (OVS)**: for VXLAN-based transport between nodes

### Theoretical Background

Kubernetes natively assigns each pod one interface (`eth0`) through the primary CNI (Flannel here). To emulate 5G components (AMF, SMF, UPF, etc.) with distinct N-interfaces, we require:

| Layer             | Technology  | Role                                                    |
| ----------------- | ----------- | ------------------------------------------------------- |
| Control-plane CNI | Flannel     | Default pod-to-pod network (K3s-managed)                |
| Meta-CNI          | Multus      | Orchestrates secondary CNIs for extra interfaces        |
| IPAM              | Whereabouts | Manages IP pools cluster-wide via CRDs                  |
| Data-plane CNI    | OVS         | Connects pods to specific OVS bridges and VXLAN tunnels |

### CNI Path Conventions

Different paths for worker (k3s) vs edge (standalone containerd):

| Node Type | CNI Config Path                            | CNI Binary Path | Notes                     |
| --------- | ------------------------------------------ | --------------- | ------------------------- |
| Worker    | `/var/lib/rancher/k3s/agent/etc/cni/net.d` | `/opt/cni/bin`  | K3s-managed paths         |
| Edge      | `/etc/cni/net.d`                           | `/opt/cni/bin`  | Standard containerd paths |

**Why different paths?** Worker uses k3s, which manages its own CNI directory structure. Edge uses standalone containerd with standard Linux CNI conventions.

### Why Multus

Multus enables multi-interface pods via NetworkAttachmentDefinitions (NADs). Each NAD specifies a secondary CNI (e.g., `type: ovs`) and IPAM configuration.

### Why Whereabouts

`host-local` IPAM would reuse IPs across nodes. Whereabouts allocates IPs globally by storing allocations in custom Kubernetes resources, ensuring uniqueness cluster-wide.

### Why OVS

OVS provides the programmable data plane:

- **One bridge per 5G interface**: `br-n1`, `br-n2`, `br-n3`, `br-n4`, `br-n6e`, `br-n6c`, plus per-cell bridges (`br-n2-cell-{id}`, `br-n3-cell-{id}`)
- **VXLAN tunnels**: connecting worker ↔ edge nodes (global + per-cell)
- **Extensible**: for OpenFlow, QoS, or network slicing features
- **Traffic Engineering**: Bandwidth limiting, latency injection, packet loss simulation (see [Network Impairments](#network-impairments-and-qos) below)

### Implementation Flow

#### 1. CNI Binaries Setup

- **Worker**: Downloads Whereabouts + OVS CNI to `/var/lib/rancher/k3s/data/cni/bin`, then copies base plugins from k3s to `/opt/cni/bin`
- **Edge**: Downloads Whereabouts + OVS CNI to `/opt/cni/bin`, downloads standard CNI plugins package

#### 2. OVS Network Setup

- Deploys two OVS DaemonSets (worker + edge) via ConfigMap + hostPath mounts
- Each creates 6 OVS bridges (`br-n1` through `br-n6c/e`) with VXLAN tunnels between nodes
- Uses Alpine container with `openvswitch` package

#### 3. Multus Installation

**Key architectural decision**: Deploy **two separate Multus DaemonSets** instead of one:

- `multus-worker`: targets `kubernetes.io/hostname: worker`, mounts k3s CNI paths
- `multus-edge`: targets `kubernetes.io/hostname: edge`, mounts standard CNI paths

**Why two DaemonSets?** K3s and standalone containerd use different CNI directory structures. A single DaemonSet with node-specific volume mounts isn't supported in Kubernetes.

**Multus as Meta-Plugin Architecture**:

Multus is configured to act as a **meta-plugin** that delegates to the primary CNI (Flannel on worker, edge-cni on edge) while providing secondary interface support:

- Uses **thin image** (`ghcr.io/k8snetworkplumbingwg/multus-cni:v4.1.0`)
- Entrypoint: `/thin_entrypoint`
- **Auto-generates** `00-multus.conflist` with `--multus-conf-file=auto`
- Delegates to primary CNI: Flannel (worker) or edge-cni (edge)
- Pods **without** `k8s.v1.cni.cncf.io/networks` annotation → only primary CNI interface
- Pods **with** annotation → primary CNI + additional interfaces via NADs

**Critical Configuration Parameters**:

- `--multus-kubeconfig-file-host`: Points to **host filesystem path** (not container path)
  - Worker: `/var/lib/rancher/k3s/agent/etc/cni/net.d/multus.d/multus.kubeconfig`
  - Edge: `/etc/cni/net.d/multus.d/multus.kubeconfig`
- `--multus-conf-file=auto`: Auto-discovers and wraps the primary CNI configuration
- Volume mounts: `/host/etc/cni/net.d` and `/host/opt/cni/bin` (container paths, mapped to host-specific paths)

**Generated Configuration Files**:

- Worker: `/var/lib/rancher/k3s/agent/etc/cni/net.d/00-multus.conflist` (wraps `10-flannel.conflist`)
- Edge: `/etc/cni/net.d/00-multus.conflist` (wraps `10-edge.conflist`)

**Edge-specific requirement**: Creates a primary CNI config (`/etc/cni/net.d/10-edge.conflist`) for EdgeCore, which requires at least one CNI config to initialize. Multus then wraps this with `00-multus.conflist`.

#### 4. Whereabouts IPAM

- Installs Whereabouts CRDs
- Writes `whereabouts.conf` and `whereabouts.kubeconfig` to both worker and edge CNI directories
- Enables cluster-wide IP allocation across nodes

#### 5. NetworkAttachmentDefinitions (NADs)

Creates 6 NADs for 5G interfaces:

- `5g/n1-net`, `n2-net`, `n3-net`, `n4-net`, `n6c-net` (cloud)
- `mec/n6e-net` (edge MEC)

Each NAD specifies OVS bridge, Whereabouts IPAM range, and MTU.
For AMF-facing networks (`n1-net`, `n2-net`), the AMF static IPs are also added to
Whereabouts `exclude` so dynamic allocation cannot claim the same addresses.

#### 6. Per-Cell NADs (Phase 4 Extension)

When Phase 6 is enabled, this phase also creates one NAD per cell for N2 and N3 based on the topology declared in `ansible/phases/06-ueransim-mec/vars/topology.yml`.

- Role: `roles/cell_network_setup`
- Creates: `5g/n2-cell-{id}`, `5g/n3-cell-{id}`
- Bridges: `br-n2-cell-{id}`, `br-n3-cell-{id}` (VXLAN between `worker` ↔ `edge`)

This provides L2 isolation per cell and is consumed by gNB/UE StatefulSets in Phase 6.

### Security Note

For simplicity, the lab setup reuses the admin kubeconfig for Whereabouts. In production, this should be replaced with a restricted kubeconfig granting only CRD access for IP management.

### Expected Results

After Phase 4:

```bash
# Two Multus DaemonSets running
kubectl get ds -n kube-system multus-worker multus-edge
# NAME            DESIRED   CURRENT   READY   UP-TO-DATE   AVAILABLE
# multus-worker   1         1         1       1            1
# multus-edge     1         1         1       1            1

# Six NetworkAttachmentDefinitions created
kubectl get network-attachment-definitions -A
# NAMESPACE   NAME      AGE
# 5g          n1-net    5m
# 5g          n2-net    5m
# 5g          n3-net    5m
# 5g          n4-net    5m
# 5g          n6c-net   5m
# mec         n6e-net   5m

# Per-cell NADs (if Phase 6 topology present)
kubectl get net-attach-def -n 5g | grep -E "n[23]-cell-"
# n2-cell-1   2m
# n3-cell-1   2m

# OVS bridges on nodes
vagrant ssh worker -c "sudo ovs-vsctl show"
# Bridge br-n1, br-n2, br-n3, br-n4, br-n6c with vxlan ports

vagrant ssh edge -c "sudo ovs-vsctl show"
# Bridge br-n1, br-n2, br-n3, br-n4, br-n6e with vxlan ports
```

---

## Network Impairments and Link Characteristics

The OVS bridges and VXLAN tunnels created in this phase define the **physical network topology** of the 5G testbed. These links can be configured with realistic network characteristics to emulate real-world deployment scenarios.

**Supported Link Characteristics** (planned automation via `topology.yml`):

- **Bandwidth**: Configurable throughput limits per link/cell
- **Latency**: Variable delay to simulate distance (edge/cloud) or backhaul congestion
- **Packet Loss**: Unreliable wireless link simulation
- **Jitter**: Latency variation for realism

**Implementation**: Network impairments will be applied automatically based on topology configuration using Linux Traffic Control (TC) and OVS QoS policies. Per-cell configuration allows heterogeneous scenarios (e.g., Cell-1 with good connectivity, Cell-2 with degraded link).

**Use Cases**: Remote edge testing, congested backhaul, unreliable wireless links, multi-cell mobility scenarios.

> **Note**: This capability is documented for future implementation. Current deployment creates links without impairments (ideal conditions).

---

### Troubleshooting Checklist

| Check                   | Expected                                                                                       |
| ----------------------- | ---------------------------------------------------------------------------------------------- |
| **Multus pods**         | `multus-worker` and `multus-edge` both Running                                                 |
| **Multus DS mounts**    | Worker: k3s paths, Edge: standard paths (see CNI Path Conventions table)                       |
| **Multus config files** | `00-multus.conflist` exists on both nodes, wrapping primary CNI (Flannel or edge-cni)          |
| **Multus kubeconfig**   | Exists with correct host path in `multus.d/multus.kubeconfig` on both nodes (mode 0644)        |
| **Primary CNI order**   | `00-multus.conflist` comes before `10-flannel.conflist` or `10-edge.conflist` (lexical order)  |
| **Edge primary CNI**    | `/etc/cni/net.d/10-edge.conflist` exists (required for EdgeCore to initialize)                 |
| **Whereabouts**         | `whereabouts.conf` and kubeconfig in `/var/lib/.../net.d` (worker) and `/etc/cni/net.d` (edge) |
| **OVS bridges**         | `br-n*` and `vxlan-*` present on both nodes, MTU = 1450                                        |
| **NAD CRD**             | `kubectl get crd network-attachment-definitions.k8s.cni.cncf.io` exists                        |

**Verification Commands**:

```bash
# Check Multus config delegation (worker)
vagrant ssh worker -c "sudo cat /var/lib/rancher/k3s/agent/etc/cni/net.d/00-multus.conflist | jq '.plugins[0].delegates[0].type'"
# Expected: "flannel"

# Check Multus config delegation (edge)
vagrant ssh edge -c "sudo cat /etc/cni/net.d/00-multus.conflist | jq '.plugins[0].delegates[0].type'"
# Expected: "bridge" (edge-cni)

# Check Multus kubeconfig path (worker)
vagrant ssh worker -c "sudo cat /var/lib/rancher/k3s/agent/etc/cni/net.d/00-multus.conflist | jq '.plugins[0].kubeconfig'"
# Expected: "/var/lib/rancher/k3s/agent/etc/cni/net.d/multus.d/multus.kubeconfig"

# Check Multus kubeconfig path (edge)
vagrant ssh edge -c "sudo cat /etc/cni/net.d/00-multus.conflist | jq '.plugins[0].kubeconfig'"
# Expected: "/etc/cni/net.d/multus.d/multus.kubeconfig"
```

### Assumptions & Limits

- Inventory groups define one `worker` and one `edge` host (see `ansible/inventory.ini`).
- Node placement uses `kubernetes.io/hostname: worker` and `kubernetes.io/hostname: edge`.
- Current version targets 1 worker + 1 edge. Multi-worker/edge support will be added by iterating over group members.

### Common Issues

**Issue**: Pods fail to create with "stat /host/etc/cni/net.d/multus.d/multus.kubeconfig: no such file or directory"  
**Cause**: Multus kubeconfig path uses container mount path instead of host filesystem path  
**Fix**: The `--multus-kubeconfig-file-host` parameter must specify the **host filesystem path**, not the container mount path:

- ❌ Wrong: `/host/etc/cni/net.d/multus.d/multus.kubeconfig`
- ✅ Correct (worker): `/var/lib/rancher/k3s/agent/etc/cni/net.d/multus.d/multus.kubeconfig`
- ✅ Correct (edge): `/etc/cni/net.d/multus.d/multus.kubeconfig`

**Issue**: Pods without Multus annotations fail to get network  
**Cause**: Multus not configured as meta-plugin (acting as primary CNI without delegation)  
**Fix**: Ensure `--multus-conf-file=auto` is set so Multus auto-discovers and wraps the primary CNI

**Issue**: Multus edge pod in CrashLoopBackOff  
**Cause**: EdgeCore reports "CNI plugin not initialized"  
**Fix**: Ensure `/etc/cni/net.d/10-edge.conflist` exists (primary CNI config for EdgeCore)
