## Phase 2 — Kubernetes Cluster Setup (K3s)

### Overview

This phase provisions a **two-node** Kubernetes cluster using K3s, a lightweight distribution designed for edge and lab environments.

It installs and configures:

- **One control-plane node** (master)
- **One worker node**

The edge node is intentionally **excluded** from this phase and will join the cluster in Phase 3 via **KubeEdge EdgeCore** (without k3s-agent).

The resulting cluster provides a working Flannel overlay network, kube-proxy, and CoreDNS — forming the baseline for KubeEdge integration (Phase 3) and overlay networking (Phase 4).

### Theoretical Background

#### K3s

K3s bundles all essential Kubernetes components (API server, controller, scheduler, kubelet, kube-proxy, and Flannel) into a single binary. It's designed for small clusters, requiring less memory and simplifying networking setup. Internally, K3s uses containerd as its runtime and deploys Flannel as the default CNI.

#### Flannel (CNI)

Flannel creates a flat Layer-3 network between Pods across nodes. It allocates each node a `/24` subnet within the cluster CIDR (default `10.42.0.0/16`) and uses a VXLAN overlay to connect them.

Each node gets two virtual interfaces:

- **`cni0`**: bridge connecting local Pods
- **`flannel.1`**: VXLAN interface carrying encapsulated packets between nodes

#### Network Interface Selection

In Vagrant multi-VM setups, each node has two network interfaces:

| Interface | Purpose                   | IP Assignment         | Used For                    |
| --------- | ------------------------- | --------------------- | --------------------------- |
| `enp0s3`  | NAT (Internet access)     | 10.0.2.15 (all nodes) | Package downloads, images   |
| `enp0s8`  | Private network (cluster) | 192.168.56.x (unique) | Flannel VXLAN, node-to-node |

**Key point**: Flannel VXLAN requires **unique IPs** per node. The playbook automatically detects the interface matching `ansible_host` (192.168.56.x) from the inventory and binds Flannel to it.

**Routing example (worker node after Phase 2):**

```
default via 10.0.2.2 dev enp0s3        # Internet traffic
192.168.56.0/24 dev enp0s8             # Inter-node cluster traffic
10.42.0.0/16 via flannel.1             # Pod network (VXLAN over enp0s8)
```

This design ensures:

- ✅ VXLAN tunnels function correctly (unique src/dst IPs)
- ✅ Internet access remains functional (NAT via enp0s3)
- ✅ Low-latency inter-node traffic (direct via enp0s8)

#### kube-proxy

kube-proxy maintains the translation rules that allow Kubernetes Services to route to backend Pods. K3s usually deploys it automatically; however, in some releases or misconfigurations it may be absent — so a small guard role ensures it's always available.

#### CoreDNS

CoreDNS provides internal DNS resolution for Services and Pods. It's deployed as a Kubernetes Deployment with usually 1–2 replicas in the `kube-system` namespace.

### Implementation Details

| Role               | Purpose                                                                                                      |
| ------------------ | ------------------------------------------------------------------------------------------------------------ |
| `k3s_master`       | Installs the K3s control-plane, configures Flannel networking, exports the join token, and copies kubeconfig |
| `k3s_agent`        | Installs K3s agent on the **worker node only**, joins it to the master, and labels it as `node-type=worker`  |
| `kube_proxy_guard` | Verifies that the kube-proxy DaemonSet exists; if not, creates it with `nodeSelector: node-type: worker`     |
| `cluster_smoke`    | Performs health checks via the Kubernetes API: node readiness, Flannel DaemonSet, kube-proxy, and CoreDNS    |

All operations use idempotent API-based modules (`kubernetes.core.k8s` and `kubernetes.core.k8s_info`) instead of shell commands.

Re-running the phase restarts k3s on a node only when something it reads at start
changed: the binary (`k3s_version`), the master's `config.yaml`, the systemd unit or
its environment file. The units use `KillMode=process`, so a restart leaves the
running pods in place.

### Architecture Decision: Why No k3s-agent on Edge?

The edge node runs **KubeEdge EdgeCore only** (Phase 3), not k3s-agent. This is the recommended KubeEdge architecture to avoid:

- **Dual kubelet conflict**: k3s-agent and EdgeCore both try to manage the node
- **Double registration**: same node name registered twice in Kubernetes
- **Runtime conflicts**: competing CRI connections to containerd

EdgeCore acts as a lightweight kubelet replacement, optimized for edge scenarios with unreliable connectivity.

### Order of Execution

#### Master Setup

1. Installs and starts `k3s` server
2. Waits for API to become reachable
3. Exports node token and kubeconfig

#### Agents Setup

1. Fetch join token
2. Install and start `k3s-agent`
3. Join cluster via API and apply node labels

#### kube-proxy Guard

- Ensures kube-proxy DaemonSet and ConfigMap exist (only runs if missing)

#### Smoke Tests

1. Check Flannel DaemonSet readiness
2. Check kube-proxy DaemonSet (if any)
3. Check CoreDNS Deployment
4. Summarize results

### Expected Results

After Phase 2:

- `kubectl get nodes` → **2 nodes** show `Ready` (master + worker)
- `kubectl get pods -n kube-system` → `coredns`, `kube-proxy` (on worker), and Flannel are `Running`
- Pod networking between master and worker works via Flannel
- **Edge node not yet visible** — it will join in Phase 3 via KubeEdge

This phase establishes the baseline Kubernetes cluster required for KubeEdge integration (Phase 3) and overlay networking (Phase 4).
