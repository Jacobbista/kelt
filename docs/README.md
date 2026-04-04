# Documentation

This documentation covers the full lifecycle of the 5G KubeEdge Testbed: from first deployment to day-2 operations, network debugging, physical RAN integration, and contributing new features. It is written for researchers, developers, and operators working with the testbed directly.

---

## Where to Start

| Goal | Path |
|------|------|
| **Deploy the testbed for the first time** | [Requirements](requirements.md) → [Getting Started](getting-started.md) |
| **Understand the system design** | [Architecture Overview](architecture/overview.md) → [Virtualization Layers](architecture/virtualization-layers.md) → [Network Topology](architecture/network-topology.md) |
| **Debug a running testbed** | [Troubleshooting](operations/troubleshooting.md) → relevant [Runbook](runbooks/) |
| **Contribute code or tests** | [Contributing](development/contributing.md) → [Testing Guide](development/testing.md) |

---

## Architecture

These documents explain how the system is designed. Read them in order — each builds on the previous.

| Document | Description |
|----------|-------------|
| [Overview](architecture/overview.md) | Node roles, component placement, deployment flow |
| [Virtualization Layers](architecture/virtualization-layers.md) | The 5 abstraction layers: host → VMs → K8s → overlay → 5G NFs |
| [Network Topology](architecture/network-topology.md) | OVS bridges, VXLAN tunnels, Multus CNI — explained from first principles |
| [5G Interfaces](architecture/5g-interfaces.md) | N1/N2/N3/N4/N6 subnets, static IPs, protocols, and verification commands |

---

## Deployment

Instructions for deploying and configuring the testbed in different environments and modes.

| Document | Description |
|----------|-------------|
| [Deployment Phases](deployment/phases.md) | What each of the 8 phases does and how to run them individually |
| [Server / NUC Deployment](deployment/server-setup.md) | Deploy on a headless server with optimized profiles and remote access |
| [Physical RAN Integration](deployment/physical-ran.md) | Connect a real femtocell instead of, or alongside, UERANSIM |
| [RAN Mode Switching](deployment/ran-modes-dashboard.md) | Switch between physical and simulated RAN using the dashboard |

Each deployment phase also has an implementation-focused README in its Ansible source directory:

| Phase | Notes |
|-------|-------|
| [Phase 1: Infrastructure](../ansible/phases/01-infrastructure/README.md) | VM provisioning, networking bootstrap |
| [Phase 2: Kubernetes](../ansible/phases/02-kubernetes/README.md) | K3s cluster bring-up |
| [Phase 3: KubeEdge](../ansible/phases/03-kubeedge/README.md) | CloudCore and EdgeCore configuration |
| [Phase 4: Overlay Network](../ansible/phases/04-overlay-network/README.md) | OVS bridges, VXLAN tunnel setup |
| [Phase 5: 5G Core](../ansible/phases/05-5g-core/README.md) | Open5GS NF deployment and configuration |
| [Phase 6: UERANSIM + MEC](../ansible/phases/06-ueransim-mec/README.md) | gNB, UE simulators, MEC workloads |
| [Phase 8: Dashboard](../ansible/phases/08-dashboard/README.md) | Dashboard deployment and access |

---

## Dashboard

The testbed includes an out-of-band operations dashboard (FastAPI + React) that provides real-time visibility and control without depending on the 5G core being healthy.

| Document | Description |
|----------|-------------|
| [Overview](dashboard/overview.md) | Architecture, access URLs, security model, deployment |
| [Modules](dashboard/modules.md) | All 7 modules: Control Room, Topology Map, Subscribers, UE Monitoring, Metrics, Physical RAN Config, Network Health |
| [API Reference](dashboard/api-reference.md) | Full REST and WebSocket endpoint listing |

---

## Operations

Reference material and diagnostic procedures for running the testbed day-to-day.

| Document | Description |
|----------|-------------|
| [Troubleshooting](operations/troubleshooting.md) | Common issues and solutions — start here when something is wrong |
| [Handbook](operations/handbook.md) | Canonical IP reference, interface matrix, VXLAN keys, operational procedures |

### Runbooks

Detailed step-by-step diagnostics for specific subsystems. Use these when the troubleshooting guide points you to a specific area.

| Runbook | Covers |
|---------|--------|
| [NGAP Diagnostics](runbooks/ngap-diagnostics.md) | N2 control plane — gNB ↔ AMF |
| [PFCP Diagnostics](runbooks/pfcp-diagnostics.md) | N4 control plane — SMF ↔ UPF |
| [GTP-U Path](runbooks/gtpu-path.md) | N3 user plane — gNB ↔ UPF data path |
| [OVS VXLAN Health](runbooks/ovs-vxlan-health.md) | Overlay network infrastructure |
| [Multus NAD IPAM](runbooks/multus-nad-ipam.md) | Network attachment and IP allocation |

---

## Tools

Host-side utilities that complement the testbed.

| Document | Description |
|----------|-------------|
| [5G UE Probe](tools/5g-probe.md) | Manage and benchmark physical UE dongles from the host |
| [testbed-config](tools/testbed-config.md) | Interactive CLI for deployment profiles, edge toggle, and RAN configuration |

---

## Development

| Document | Description |
|----------|-------------|
| [Testing Guide](development/testing.md) | Run and write automated tests (e2e, protocols, RAN) |
| [Contributing](development/contributing.md) | Coding standards, workflow, and PR guidelines |

The test suite is documented separately: [tests/README.md](../tests/README.md).

---

## Known Issues

Platform-specific limitations with documented workarounds. These are bugs or constraints that have no upstream fix yet.

| Issue | Description |
|-------|-------------|
| [KubeEdge Edge Discovery](known-issues/kubeedge-edge-discovery.md) | No CoreDNS + no ConfigMap sync on edge — workarounds for pod DNS and API access |
| [KubeEdge Multus Env Injection](known-issues/kubeedge-multus-env-injection.md) | Empty K8s env vars injected by KubeEdge break Multus auto-config |
| [KubeEdge ServiceAccount Tokens](known-issues/kubeedge-serviceaccount-token.md) | Token projection bugs — use `automountServiceAccountToken: false` |
| [UPF-Edge CNI Route Conflict](known-issues/upf-edge-cni-route-conflict.md) | UPF-Edge stuck in ContainerCreating — open issue, investigation roadmap |

---

## Coverage Tracker

[gaps.md](gaps.md) tracks documentation gaps, incomplete features, and areas planned for future work. Check it before opening an issue or starting a new contribution.

## Roadmap

[roadmap.md](roadmap.md) describes the project's direction across four phases: thesis consolidation, O-RAN integration, intelligence layer, and 6G research positioning. It also covers monetization paths and what to study at each stage.
