# 5G KubeEdge Testbed

A production-ready 5G testbed for research and development, featuring cloud-edge distribution with Kubernetes, KubeEdge, and Open5GS.

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

## Overview

This testbed provides a complete 5G network infrastructure for:

- **Edge Computing Research** - Cloud-edge workload distribution with KubeEdge
- **5G Protocol Testing** - Full Open5GS core with NGAP, GTP-U, PFCP interfaces
- **MEC Applications** - Multi-access Edge Computing scenarios
- **Network Simulation** - UERANSIM gNB/UE or physical femtocell integration

## Architecture

```
+------------------+     +------------------+     +------------------+
|   MASTER NODE    |     |   WORKER NODE    |     |    EDGE NODE     |
|                  |     |                  |     |                  |
|  K3s Server      |     |  5G Core         |     |  EdgeCore        |
|                  |     |  (Open5GS)       |     |  gNB + UEs       |
|                  |     |  CloudCore       |     |  (UERANSIM)      |
|                  |     |  MongoDB         |     |                  |
+------------------+     +------------------+     +------------------+
                               |                        |
                               +--- VXLAN Tunnels ------+
                                   (N2, N3, N4, N6)
```

## Quick Start

### Prerequisites

- Vagrant >= 2.3.0
- VirtualBox >= 6.1.0
- 16GB RAM recommended

### Deploy

```bash
git clone https://github.com/Jacobbista/5g-k3s-kubedge-testbed.git
cd 5g-k3s-kubedge-testbed

# Deploy default stack: core + observability + dashboard
vagrant up

# Deploy full stack with UERANSIM
DEPLOY_MODE=full vagrant up
```

### Startup Flags (authoritative)

Use only this flag with `vagrant up`:

| Flag | Values | Default | Effect |
|------|--------|---------|--------|
| `DEPLOY_MODE` | `core_only`, `full` | `core_only` | `full` adds Phase 6 (UERANSIM + MEC) |

Examples:

```bash
# Default (phases 1-5 + phase 7 + phase 8)
vagrant up

# Core + UERANSIM (phase 6)
DEPLOY_MODE=full vagrant up
```

### Verify

```bash
vagrant ssh master
kubectl get nodes
kubectl get pods -n 5g
```

## Features

| Component | Technology | Purpose |
|-----------|------------|---------|
| Kubernetes | K3s | Container orchestration |
| Edge Computing | KubeEdge | Cloud-edge communication |
| 5G Core | Open5GS | AMF, SMF, UPF, NRF, etc. |
| RAN Simulation | UERANSIM | gNB and UE simulators |
| Networking | OVS + VXLAN | Isolated overlay networks |
| CNI | Multus | Multi-homed pods |

## 5G Interfaces

| Interface | Network | Protocol | Purpose |
|-----------|---------|----------|---------|
| N1 | 10.201.0.0/24 | NAS/SCTP | UE - AMF signaling |
| N2 | 10.202.0.0/24 | NGAP/SCTP | gNB - AMF control |
| N3 | 10.203.0.0/24 | GTP-U/UDP | gNB - UPF user plane |
| N4 | 10.204.0.0/24 | PFCP/UDP | SMF - UPF control |

## Documentation

| Topic | Link |
|-------|------|
| Requirements | [docs/requirements.md](docs/requirements.md) |
| Getting Started | [docs/getting-started.md](docs/getting-started.md) |
| Architecture | [docs/architecture/overview.md](docs/architecture/overview.md) |
| Network Topology | [docs/architecture/network-topology.md](docs/architecture/network-topology.md) |
| Deployment Phases | [docs/deployment/phases.md](docs/deployment/phases.md) |
| Dashboard | [docs/dashboard/overview.md](docs/dashboard/overview.md) |
| Physical RAN | [docs/deployment/physical-ran.md](docs/deployment/physical-ran.md) |
| Troubleshooting | [docs/operations/troubleshooting.md](docs/operations/troubleshooting.md) |
| Testing | [docs/development/testing.md](docs/development/testing.md) |

## Testing

```bash
cd tests
make e2e        # End-to-end tests
make protocols  # Protocol validation
make ran        # Physical RAN tests
```

## Use Cases

- **Academic Research**: 5G network behavior, edge computing algorithms
- **Development**: Test applications against real 5G interfaces
- **Training**: Learn 5G architecture hands-on
- **Integration Testing**: Validate physical RAN equipment

## License

Copyright 2024-2026 Jacopo Bennati

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) for details.

## Acknowledgements

Built with [K3s](https://k3s.io), [KubeEdge](https://kubeedge.io), [Open5GS](https://open5gs.org), [UERANSIM](https://github.com/aligungr/UERANSIM), and [Multus CNI](https://github.com/k8snetworkplumbingwg/multus-cni).
