# Getting Started

This guide will get you from zero to a running 5G testbed in under 30 minutes.

## Prerequisites

- **Vagrant** >= 2.3.0
- **VirtualBox** >= 6.1.0
- **gum** (optional, recommended) — interactive TUI for [`testbed-config`](tools/testbed-config.md)
- **Host resources**: 16 GB RAM, 4+ CPU cores recommended
- **OS**: Linux, macOS, or Windows with virtualization enabled

```bash
# VirtualBox
sudo apt install -y virtualbox virtualbox-ext-pack

# Vagrant
wget -O- https://apt.releases.hashicorp.com/gpg | sudo gpg --dearmor -o /usr/share/keyrings/hashicorp-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $(lsb_release -cs) main" \
  | sudo tee /etc/apt/sources.list.d/hashicorp.list
sudo apt update && sudo apt install -y vagrant

# gum — interactive TUI for testbed-config (optional but recommended)
sudo mkdir -p /etc/apt/keyrings
curl -fsSL https://repo.charm.sh/apt/gpg.key | sudo gpg --dearmor -o /etc/apt/keyrings/charm.gpg
echo "deb [signed-by=/etc/apt/keyrings/charm.gpg] https://repo.charm.sh/apt/ * *" | sudo tee /etc/apt/sources.list.d/charm.list
sudo apt update && sudo apt install gum
```

For macOS/Windows install instructions, see [Requirements](requirements.md).

For the full list of tools and versions (including 5G UE Probe requirements for physical dongle experiments), see [Requirements](requirements.md).

## Quick Start

### Deploy 5G Core Only (Default — Laptop)

```bash
git clone https://github.com/Jacobbista/5g-k3s-kubedge-testbed.git
cd 5g-k3s-kubedge-testbed

# (Optional) Configure the testbed interactively
./testbed-config

# Deploy
vagrant up
```

Running `./testbed-config` opens an interactive menu where you can select the deployment profile, toggle the edge VM, configure physical RAN, and more. Without it, the default `laptop` profile is used (4 VMs, edge included). See [`testbed-config`](tools/testbed-config.md) for the full reference.

### Server / NUC Deployment

For headless servers or Intel NUCs with limited CPU, use the `server` profile:

```bash
./testbed-config set-profile server   # 3 VMs, no edge, optimized resources
vagrant up
vagrant provision ansible
```

See [Server / NUC Deployment](deployment/server-setup.md) for hardware requirements, remote access setup, and reverse proxy configuration.

This deploys:
- K3s cluster (master, worker, edge nodes)
- KubeEdge (CloudCore + EdgeCore)
- OVS overlay networks with VXLAN tunnels
- Open5GS 5G Core (AMF, SMF, UPF, NRF, etc.)
- Observability stack (Prometheus, Loki, Grafana)
- Dashboard control plane (out-of-band on ansible VM)

### Deploy Full Stack (with UERANSIM)

```bash
DEPLOY_MODE=full vagrant up
```

Adds UERANSIM simulator (gNB + UEs) for end-to-end testing.

## Startup Flags

Use this flag with `vagrant up`:

| Flag | Values | Default | Behavior |
|------|--------|---------|----------|
| `DEPLOY_MODE` | `core_only`, `full` | `core_only` | `full` includes Phase 6 (UERANSIM + MEC) |
| `TESTBED_PROFILE` | `laptop`, `server` | `laptop` | `server` creates 3 VMs (no edge) with optimized resources. See [Server Setup](deployment/server-setup.md) |
| `EDGE_ENABLED` | `true`, `false` | `true` (laptop) / `false` (server) | Controls edge VM creation and KubeEdge EdgeCore deployment |

Recommended command combinations:

```bash
# Default: core only
vagrant up

# Add UERANSIM
DEPLOY_MODE=full vagrant up
```

## What Gets Deployed

```mermaid
graph LR
    subgraph Master["Master  192.168.56.10"]
        M1["K3s Server"]
    end
    subgraph Worker["Worker  192.168.56.11"]
        W1["K3s Agent"]
        W2["CloudCore (KubeEdge)"]
        W3["5G Core NFs"]
        W4["MongoDB · UPF-Cloud"]
    end
    subgraph Edge["Edge  192.168.56.12"]
        E1["EdgeCore (KubeEdge)"]
        E2["gNB + UEs (UERANSIM)"]
    end
    Worker <-->|"VXLAN N2/N3/N4/N6"| Edge
    Master --- Worker
```

## Verify Deployment

### Check Nodes

```bash
vagrant ssh master
sudo k3s kubectl get nodes
```

Expected output:
```
NAME     STATUS   ROLES                  AGE   VERSION
master   Ready    control-plane,master   10m   v1.30.6+k3s1
worker   Ready    <none>                 8m    v1.30.6+k3s1
edge     Ready    agent,edge             6m    v1.30.6+k3s1
```

> **kubectl on K3s VMs**: K3s does not create a standalone `kubectl` binary — the cluster is managed via `sudo k3s kubectl`. All in-VM kubectl commands throughout these docs use this form.

### Check 5G Core

```bash
sudo k3s kubectl get pods -n 5g
```

All pods should be `Running`.

### Check UERANSIM (if deployed with full mode)

```bash
sudo k3s kubectl get pods -n 5g -l app=gnb-1
sudo k3s kubectl get pods -n 5g -l app=ue
```

## Access the Cluster

### From Host Machine

```bash
# Copy kubeconfig
vagrant ssh master -c "cat /home/vagrant/kubeconfig" > kubeconfig
export KUBECONFIG=$(pwd)/kubeconfig
kubectl get nodes
```

### From Ansible VM

The ansible VM does not have `kubectl` installed. To run kubectl commands, SSH into master:

```bash
vagrant ssh master
sudo k3s kubectl get nodes
```

## Deploy UERANSIM Manually

If you deployed with `core_only` mode (default) and want to add UERANSIM later:

```bash
vagrant ssh ansible
cd ~/ansible-ro
ansible-playbook phases/06-ueransim-mec/playbook.yml -i inventory.ini
```

## Access the Dashboard

The dashboard is deployed automatically in Phase 8. Open it in your browser once provisioning completes:

| Service | URL |
|---------|-----|
| Dashboard UI | http://192.168.56.13:31573 |
| Dashboard API docs | http://192.168.56.13:31880/docs |
| Grafana | http://192.168.56.11:30300 (admin / admin5g) |

See [Dashboard Overview](dashboard/overview.md) for full documentation.

## Optional: 5G UE Probe (Physical UE Dongle)

If you have a physical 5G UE dongle (USB modem) and want to run experiments on your Linux host, use the `5g-probe` web app. It isolates the dongle into a Linux network namespace and lets you benchmark throughput and latency with a live chart UI.

```bash
cd 5g-probe
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
sudo $(which python3) app.py
# Open http://localhost:5000
```

See [docs/tools/5g-probe.md](tools/5g-probe.md) for the full guide including host requirements and API reference.

## Next Steps

- [Architecture Overview](architecture/overview.md) - Understand system design
- [Network Topology](architecture/network-topology.md) - Learn about 5G interfaces
- [Deployment Phases](deployment/phases.md) - Detailed phase documentation
- [Troubleshooting](operations/troubleshooting.md) - Common issues and solutions

## Cleanup

```bash
# Destroy all VMs
vagrant destroy -f

# Or just stop them
vagrant halt
```
