# Requirements

Tools and versions needed to run the testbed. Requirements are split into **cluster** (Vagrant, K3s, Ansible) and **UE probe** (host-side physical dongle experiments with `5g-probe`).

---

## Cluster Requirements

For `vagrant up`, Ansible provisioning, and the K3s cluster:

| Tool / Resource | Version / Spec | Notes |
|-----------------|----------------|-------|
| Vagrant | >= 2.3.0 | VM orchestration |
| VirtualBox | >= 6.1.0 | Hypervisor |
| gum (optional) | >= 0.13 | Interactive TUI for [`testbed-config`](tools/testbed-config.md). Without it, the tool falls back to basic prompts |
| Host RAM | 16 GB recommended | `laptop` profile: 4 VMs (17 GB); `server` profile: 3 VMs (14 GB). See [Server Setup](deployment/server-setup.md) for NUC/server deployments |
| Host CPU | 4+ cores recommended | 8+ threads for laptop profile; 4c/8t sufficient for server profile |
| OS | Ubuntu 24.04 LTS | Tested on Server and Desktop. macOS and Windows are a v1 target, currently untested. Virtualization must be enabled in BIOS/UEFI |
| Python (optional) | 3.8+ | Only for running tests locally (`tests/`) |

The Ansible VM is provisioned with Python, Ansible, and Kubernetes collections; no host-side Ansible is required for deployment.

> **Platform support**: Tested on Ubuntu 24.04 LTS (Server and Desktop). macOS and Windows are a v1 target and currently untested. The commands below are a starting point, not a validated path.

### Install VirtualBox

```bash
# Debian/Ubuntu
sudo apt update
sudo apt install -y virtualbox virtualbox-ext-pack
```

On macOS: `brew install --cask virtualbox`. On Windows: download the installer from [virtualbox.org](https://www.virtualbox.org/wiki/Downloads).

> **BIOS**: Ensure VT-x (Intel) or AMD-V is enabled. On NUC/server hardware, also enable VT-d for direct I/O.

### Install Vagrant

```bash
# Debian/Ubuntu
wget -O- https://apt.releases.hashicorp.com/gpg | sudo gpg --dearmor -o /usr/share/keyrings/hashicorp-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $(lsb_release -cs) main" \
  | sudo tee /etc/apt/sources.list.d/hashicorp.list
sudo apt update && sudo apt install -y vagrant
```

On macOS: `brew install --cask vagrant`. On Windows: download from [vagrantup.com](https://developer.hashicorp.com/vagrant/install).

### Install gum (Recommended)

[gum](https://github.com/charmbracelet/gum) provides the interactive TUI for [`testbed-config`](tools/testbed-config.md). Without it, the tool still works but uses basic terminal prompts.

```bash
# Debian/Ubuntu
sudo mkdir -p /etc/apt/keyrings
curl -fsSL https://repo.charm.sh/apt/gpg.key | sudo gpg --dearmor -o /etc/apt/keyrings/charm.gpg
echo "deb [signed-by=/etc/apt/keyrings/charm.gpg] https://repo.charm.sh/apt/ * *" | sudo tee /etc/apt/sources.list.d/charm.list
sudo apt update && sudo apt install gum
```

See [testbed-config docs](tools/testbed-config.md) for the full CLI reference.

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
