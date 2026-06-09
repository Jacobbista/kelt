# Documentation

KELT (Kubernetes-Edge Lightweight Testbed) is a lightweight, reproducible, cloud-native 5G core and cloud-edge testbed. This documentation covers its full lifecycle: from first deployment to day-2 operations, network debugging, physical RAN integration, and contributing new features. It is written for researchers, developers, and operators working with the testbed directly.

---

## Where to Start

| Goal | Path |
|------|------|
| **Deploy the testbed for the first time** | [Requirements](requirements.md) → [Getting Started](getting-started.md) |
| **Understand the system design** | [Architecture Overview](architecture/overview.md) → [Virtualization Layers](architecture/virtualization-layers.md) → [Network Topology](architecture/network-topology.md) |
| **Debug a running testbed** | [Troubleshooting](operations/troubleshooting.md) → relevant [Runbook](runbooks/) |
| **Contribute code or tests** | [Contributing](development/contributing.md) → [Testing Guide](development/testing.md) |
| **Check what is validated vs experimental** | [Feature Maturity](status.md) |

---

## Architecture

These documents explain how the system is designed. Read them in order — each builds on the previous.

| Document | Description |
|----------|-------------|
| [Overview](architecture/overview.md) | Node roles, component placement, deployment flow |
| [Virtualization Layers](architecture/virtualization-layers.md) | The 5 abstraction layers: host → VMs → K8s → overlay → 5G NFs |
| [Network Topology](architecture/network-topology.md) | OVS bridges, VXLAN tunnels, Multus CNI — explained from first principles |
| [5G Interfaces](architecture/5g-interfaces.md) | N1/N2/N3/N4/N6 subnets, static IPs, protocols, and verification commands |
| [Subscriber Persistence](architecture/subscriber-persistence.md) | MongoDB PVC and `subscribers-snapshot` ConfigMap — how UE records survive restarts |
| [Positioning Adapters](architecture/positioning-adapters.md) | Pluggable positioning engine adapter model behind the CAMARA Location API |
| [NF Platform](architecture/nf-platform.md) | Companion `5g-nf-platform` repo design: per-NF image builds, patches, versioning ([dev plan](architecture/nf-platform-dev-plan.md)) |

---

## Deployment

Instructions for deploying and configuring the testbed in different environments and modes.

| Document | Description |
|----------|-------------|
| [Deployment Phases](deployment/phases.md) | What each of the 12 phases does and how to run them individually |
| [Server / NUC Deployment](deployment/server-setup.md) | Deploy on a headless server with optimized profiles and remote access |
| [Physical RAN Integration](deployment/physical-ran.md) | Connect a real femtocell instead of, or alongside, UERANSIM |
| [RAN Mode Switching](deployment/ran-modes-dashboard.md) | Switch between physical and simulated RAN using the dashboard |

Each deployment phase also has an implementation-focused README in its Ansible source directory:

| Phase | Notes |
|-------|-------|
| [Phase 1: Infrastructure](https://github.com/Jacobbista/kelt/blob/main/ansible/phases/01-infrastructure/README.md) | VM provisioning, networking bootstrap |
| [Phase 2: Kubernetes](https://github.com/Jacobbista/kelt/blob/main/ansible/phases/02-kubernetes/README.md) | K3s cluster bring-up |
| [Phase 3: KubeEdge](https://github.com/Jacobbista/kelt/blob/main/ansible/phases/03-kubeedge/README.md) | CloudCore and EdgeCore configuration |
| [Phase 4: Overlay Network](https://github.com/Jacobbista/kelt/blob/main/ansible/phases/04-overlay-network/README.md) | OVS bridges, VXLAN tunnel setup |
| [Phase 5: 5G Core](https://github.com/Jacobbista/kelt/blob/main/ansible/phases/05-5g-core/README.md) | Open5GS NF deployment and configuration |
| [Phase 6: UERANSIM + MEC](https://github.com/Jacobbista/kelt/blob/main/ansible/phases/06-ueransim-mec/README.md) | gNB, UE simulators, MEC workloads |
| [Phase 7: Observability](https://github.com/Jacobbista/kelt/blob/main/ansible/phases/07-observability/README.md) | Prometheus, node-exporter, Grafana |
| [Phase 8: IAM](https://github.com/Jacobbista/kelt/blob/main/ansible/phases/08-iam/README.md) | Keycloak realm with shared clients for dashboard and CAMARA |
| [Phase 9: Dashboard](https://github.com/Jacobbista/kelt/blob/main/ansible/phases/09-dashboard/README.md) | Dashboard deployment and access |
| [Phase 10: CAMARA](https://github.com/Jacobbista/kelt/blob/main/ansible/phases/10-camara/README.md) | CAMARA Location API gateway (optional addon) |
| [Phase 11: Positioning](https://github.com/Jacobbista/kelt/blob/main/ansible/phases/11-positioning/README.md) | Positioning engine with pluggable adapters (optional addon) |
| [Phase 12: Positioning Demo](https://github.com/Jacobbista/kelt/blob/main/ansible/phases/12-positioning-demo/README.md) | Positioning demo SPA (optional addon) |

---

## Dashboard

The testbed includes an out-of-band operations dashboard (FastAPI + React) that provides real-time visibility and control without depending on the 5G core being healthy.

| Document | Description |
|----------|-------------|
| [Overview](dashboard/overview.md) | Architecture, access URLs, security model, deployment |
| [Modules](dashboard/modules.md) | All 10 modules: Overview, Kubernetes, 5G Core, Topology, RAN, Subscribers, UE Monitor, Diagnostics, Metrics, IAM |
| [API Reference](dashboard/api-reference.md) | Full REST and WebSocket endpoint listing |

---

## Operations

Reference material and diagnostic procedures for running the testbed day-to-day.

| Document | Description |
|----------|-------------|
| [Troubleshooting](operations/troubleshooting.md) | Common issues and solutions — start here when something is wrong |
| [Handbook](operations/handbook.md) | Operator cheat-sheet: consolidated IPs, ports, and commands, linking the canonical references |

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

## Security

| Document | Description |
|----------|-------------|
| [IAM](security/iam.md) | Keycloak realm: roles, OIDC clients, per-route authorization matrix, user provisioning |
| [External Access](security/external-access.md) | Exposing the dashboard, CAMARA, and demo over public domains |
| [External Tunnel Setup](deployment/external-tunnel.md) | Zero-Trust gateway bypass apps for Keycloak, dashboard WebSockets, and dev HMR |

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

The test suite is documented separately: [tests/README.md](https://github.com/Jacobbista/kelt/blob/main/tests/README.md).

---

## Known Issues

Platform-specific limitations with documented workarounds. These are bugs or constraints that have no upstream fix yet.

| Issue | Description |
|-------|-------------|
| [KubeEdge Edge Discovery](known-issues/kubeedge-edge-discovery.md) | No CoreDNS + no ConfigMap sync on edge — workarounds for pod DNS and API access |
| [KubeEdge Multus Env Injection](known-issues/kubeedge-multus-env-injection.md) | Empty K8s env vars injected by KubeEdge break Multus auto-config |
| [KubeEdge ServiceAccount Tokens](known-issues/kubeedge-serviceaccount-token.md) | Token projection bugs — use `automountServiceAccountToken: false` |
| [UPF-Edge CNI Route Conflict](known-issues/upf-edge-cni-route-conflict.md) | UPF-Edge stuck in ContainerCreating — open issue, investigation roadmap |
| [TCP Performance over 5G DRX](known-issues/tcp-performance-5g-drx.md) | TCP throughput degradation on the radio path from DRX latency variance |

---

## Coverage Tracker

[gaps.md](gaps.md) tracks documentation gaps, incomplete features, and areas planned for future work. Check it before opening an issue or starting a new contribution.

## Roadmap

[roadmap.md](roadmap.md) lists Near Term items tied to the current codebase and longer-term Planned directions, with positioning context against comparable open tools.
