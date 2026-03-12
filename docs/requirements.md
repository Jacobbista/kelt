# Requirements

Tools and versions needed to run the testbed. Requirements are split into **cluster** (Vagrant, K3s, Ansible) and **UE probe** (host-side physical dongle experiments with `5g-probe`).

---

## Cluster Requirements

For `vagrant up`, Ansible provisioning, and the K3s cluster:

| Tool / Resource | Version / Spec | Notes |
|-----------------|----------------|-------|
| Vagrant | >= 2.3.0 | VM orchestration |
| VirtualBox | >= 6.1.0 | Hypervisor |
| Host RAM | 16 GB recommended | 4 VMs (master, worker, edge, ansible) |
| Host CPU | 4+ cores recommended | |
| OS | Linux, macOS, Windows | Virtualization must be enabled in BIOS/UEFI |
| Python (optional) | 3.8+ | Only for running tests locally (`tests/`) |

The Ansible VM is provisioned with Python, Ansible, and Kubernetes collections; no host-side Ansible is required for deployment.

---

## UE Probe Requirements

For the host-side **5G UE Probe** (`5g-probe/`) when using a physical UE dongle or USB NIC:

| Tool / Resource | Version / Spec | Notes |
|-----------------|----------------|-------|
| OS | Linux | `ip netns` is Linux-specific |
| iproute2 | — | Provides `ip link`, `ip netns`; usually pre-installed |
| isc-dhcp-client | — | Provides `dhclient`; required for DHCP inside the namespace |
| iperf3 | — | Required for throughput benchmarks |
| socat | — | Required for the dongle WebUI tunnel |
| Python | 3.8+ | For the Flask + SocketIO app |
| Root / sudo | — | Required for netns and interface operations |

### Install (Debian/Ubuntu)

```bash
sudo apt install iproute2 isc-dhcp-client iperf3 socat python3 python3-pip python3-venv
```

### Install (Fedora/RHEL)

```bash
sudo dnf install iproute dhclient iperf3 socat python3 python3-pip
```

See [docs/tools/5g-probe.md](tools/5g-probe.md) for the full 5g-probe guide.
