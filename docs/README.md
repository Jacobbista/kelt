# Documentation

## Start Here

| Document | Description |
|----------|-------------|
| [Getting Started](getting-started.md) | Deploy the testbed from scratch in ~30 minutes |
| [Requirements](requirements.md) | Hardware, software, and version prerequisites |

---

## Architecture

Read these in order — each document builds on the previous.

| Document | Description |
|----------|-------------|
| [Overview](architecture/overview.md) | Node roles, component placement, deployment flow |
| [Virtualization Layers](architecture/virtualization-layers.md) | The 5 abstraction layers stacked on top of each other: host → VMs → K8s → overlay → 5G NFs |
| [Network Topology](architecture/network-topology.md) | OVS bridges, VXLAN tunnels, Multus CNI — explained from first principles |
| [5G Interfaces](architecture/5g-interfaces.md) | N1/N2/N3/N4/N6 subnets, static IPs, protocols, and verification commands |

---

## Deployment

| Document | Description |
|----------|-------------|
| [Deployment Phases](deployment/phases.md) | What each of the 8 phases does and how to run them individually |
| [Physical RAN Integration](deployment/physical-ran.md) | Connect a real femtocell instead of, or alongside, UERANSIM |
| [RAN Mode Switching](deployment/ran-modes-dashboard.md) | Switch between physical and simulated RAN using the dashboard |

---

## Dashboard

| Document | Description |
|----------|-------------|
| [Overview](dashboard/overview.md) | Why out-of-band, architecture, access URLs, security model, deployment |
| [Modules](dashboard/modules.md) | All 7 modules: Control Room, Topology Map, Subscribers, UE Monitoring, Metrics, Physical RAN Config, Network Health |
| [API Reference](dashboard/api-reference.md) | Full REST and WebSocket endpoint listing |

---

## Operations

| Document | Description |
|----------|-------------|
| [Troubleshooting](operations/troubleshooting.md) | Common issues and solutions — start here when something is wrong |
| [Handbook](operations/handbook.md) | Canonical IP reference, interface matrix, VXLAN keys, operational procedures |

### Runbooks

Detailed step-by-step diagnostics for specific subsystems:

| Runbook | Description |
|---------|-------------|
| [NGAP Diagnostics](runbooks/ngap-diagnostics.md) | N2 control plane — gNB ↔ AMF |
| [PFCP Diagnostics](runbooks/pfcp-diagnostics.md) | N4 control plane — SMF ↔ UPF |
| [GTP-U Path](runbooks/gtpu-path.md) | N3 user plane — gNB ↔ UPF data path |
| [OVS VXLAN Health](runbooks/ovs-vxlan-health.md) | Overlay network infrastructure |
| [Multus NAD IPAM](runbooks/multus-nad-ipam.md) | Network attachment and IP allocation issues |

---

## Tools

| Document | Description |
|----------|-------------|
| [5G UE Probe](tools/5g-probe.md) | Host-side tool for managing and benchmarking physical UE dongles |

---

## Development

| Document | Description |
|----------|-------------|
| [Testing Guide](development/testing.md) | Run and write automated tests |
| [Contributing](development/contributing.md) | Coding standards, workflow, and PR guidelines |

---

## Known Issues

Platform-specific limitations with documented workarounds:

| Issue | Description |
|-------|-------------|
| [KubeEdge Edge Discovery](known-issues/kubeedge-edge-discovery.md) | No CoreDNS + no ConfigMap sync on edge — workarounds for pod DNS and API access |
| [KubeEdge Multus Env Injection](known-issues/kubeedge-multus-env-injection.md) | Empty K8s env vars injected by KubeEdge break Multus auto-config |
| [KubeEdge ServiceAccount Tokens](known-issues/kubeedge-serviceaccount-token.md) | Token projection bugs — use `automountServiceAccountToken: false` |
| [UPF-Edge CNI Route Conflict](known-issues/upf-edge-cni-route-conflict.md) | UPF-Edge stuck in ContainerCreating — open issue, investigation roadmap |

---

## Additional Resources

### Phase-specific Implementation Notes

Each deployment phase has an implementation-focused README in its source directory:

- [Phase 1: Infrastructure](../ansible/phases/01-infrastructure/README.md)
- [Phase 2: Kubernetes](../ansible/phases/02-kubernetes/README.md)
- [Phase 3: KubeEdge](../ansible/phases/03-kubeedge/README.md)
- [Phase 4: Overlay Network](../ansible/phases/04-overlay-network/README.md)
- [Phase 5: 5G Core](../ansible/phases/05-5g-core/README.md)
- [Phase 6: UERANSIM + MEC](../ansible/phases/06-ueransim-mec/README.md)
- [Phase 8: Dashboard](../ansible/phases/08-dashboard/README.md)

### Test Suite

- [Test Framework](../tests/README.md) — test runner, suites, and configuration
