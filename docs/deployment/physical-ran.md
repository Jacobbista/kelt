# Physical RAN Integration

Connect a physical femtocell or small-cell gNB (e.g. nCELL-F2240) instead of, or alongside, UERANSIM.

## Interfaces: Host, Worker, Bridge

| Layer                | Name                     | Where            | Example           | Purpose                                                                                                                      |
| -------------------- | ------------------------ | ---------------- | ----------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| **Host interface**   | `PHYSICAL_RAN_BRIDGE`    | Your laptop/NUC  | `enx00e04c6817b7` | Host NIC on the same L2 network as the gNB. Can be a built-in Ethernet port, a USB-to-Ethernet adapter, or any NIC — Vagrant bridges it into the worker VM. |
| **Worker interface** | `physical_ran_interface` | Inside worker VM | `enp0s9`          | Virtual NIC created by VirtualBox. Linux names it (e.g. `enp0s9`). Leave empty in `group_vars` for auto-detect by subnet IP. |
| **Bridge**           | `br-ran`                 | Worker VM (OVS)  | `br-ran`          | OVS bridge. The worker interface is added to it; `br-ran` gets the gateway IP (192.168.6.1).                                 |

**Flow**: Host NIC → (VirtualBox bridge) → Worker NIC (`enp0s9`) → (OVS) → `br-ran` → patch ports → `br-n2` / `br-n3` → AMF / UPF pods.

**Verification**: When you run `vagrant reload worker` with `PHYSICAL_RAN_BRIDGE=<nic>`, Vagrant persists the applied value to `.physical_ran_bridge_applied`. The dashboard reads this and shows a ✓ next to the Host PC NIC when it matches — no trust required.

## Architecture

### Worker-as-Router Design

The worker VM acts as a **transport router** between the physical RAN network and the 5G overlay networks, mirroring how a real transport network connects a cell site to the core.

```
   PHYSICAL RAN NETWORK                    WORKER VM (Router)                     5G CORE PODS
   192.168.6.0/24                                                               (Overlay Networks)

  ┌──────────┐                     ┌───────────────────────────────────┐
  │   UE     │                     │                                   │
  │(via Uu)  │                     │   br-ran  (192.168.6.1/24)        │
  └────┬─────┘                     │     │                             │
       │ radio                     │     ├── patch-ran-n2 ──┐          │
  ┌────┴─────┐     enp0s9         │     └── patch-ran-n3 ──┼──┐       │
  │   gNB    │ ─────────────────> │                        │  │       │    ┌──────────────┐
  │ .5.100   │  (L2 bridged)      │   br-n2  (10.202.0.1) │  │       │    │ AMF          │
  └──────────┘                     │     └── patch-n2-ran ──┘  │       │──> │ n2: .202.0.100│
                                   │                           │       │    │ n2phy: .5.150 │
                                   │   br-n3  (10.203.0.1)    │       │    └──────────────┘
                                   │          (10.203.0.254)   │       │
                                   │     └── patch-n3-ran ─────┘       │    ┌──────────────┐
                                   │                                   │──> │ UPF-Cloud    │
                                   │   br-n4  (10.204.0.1)            │    │ n3: .203.0.101│
                                   │                                   │    └──────────────┘
                                   └───────────────────────────────────┘
```

### Why This Approach Is Correct

- **Mirrors real deployments**: In production 5G, the gNB connects to a transport network that reaches both the AMF (N2) and UPF (N3). OVS patch ports replicate this shared transport.
- **Network isolation preserved**: Each N-interface remains a separate OVS bridge with its own VXLAN tunnel and subnet. The worker only routes between the physical transport and the overlays.
- **No NAT or tunneling hacks**: The gNB communicates directly via L2 (patch ports provide bridge-level connectivity) for N2 signaling, and via L3 routing for N3 GTP-U to the UPF.

### Data Path: PDU Session User Plane

```
UE ──(Uu radio)──> gNB (192.168.6.100)
                     │
                     │ GTP-U encapsulated, dst = 10.203.0.101 (UPF N3)
                     │
                     ▼
              enp0s9 (bridged into br-ran)
                     │
              br-ran (192.168.6.1/24)
                     │
              patch-ran-n3 ──> patch-n3-ran
                     │
              br-n3  (10.203.0.1/24, 10.203.0.254/24)
                     │
              UPF-Cloud pod (n3: 10.203.0.101)
                     │
              ogstun  ──> iptables MASQUERADE ──> n6 ──> Data Network

Return path:
  UPF has route: 192.168.6.0/24 via 10.203.0.254 dev n3
  Worker br-n3 (10.203.0.254) forwards to br-ran via patch ports
  br-ran delivers to gNB via enp0s9
```

### IP Addressing Summary

| Component | Interface         | IP               | Role                            |
| --------- | ----------------- | ---------------- | ------------------------------- |
| Worker    | br-ran            | 192.168.6.1/24   | Gateway for physical RAN subnet |
| Worker    | br-n2             | 10.202.0.1/24    | N2 overlay gateway              |
| Worker    | br-n3             | 10.203.0.1/24    | N3 overlay gateway              |
| Worker    | br-n3 (secondary) | 10.203.0.254/24  | UPF return-route next-hop       |
| AMF       | n2phy             | 192.168.6.150/24 | NGAP endpoint for physical gNB  |
| AMF       | n2                | 10.202.0.100/24  | NGAP endpoint (overlay)         |
| UPF-Cloud | n3                | 10.203.0.101/24  | GTP-U endpoint                  |
| gNB       | eth               | 192.168.6.100/24 | Physical RAN interface          |

### OVS DaemonSet vs NAD (what runs where)

| Component                                 | Where                     | What it does                                                                                                                                                 |
| ----------------------------------------- | ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **OVS DaemonSet** (`ds-net-setup-worker`) | Worker node (hostNetwork) | Runs `ovs-setup.sh` to create/remove `br-ran`, patch ports, gateway IPs. When `RAN_BRIDGE_MODE=disabled`, it tears down `br-ran`.                            |
| **NAD n2-physical**                       | Kubernetes API (cluster)  | NetworkAttachmentDefinition that tells Multus how to attach pods to `br-ran`. It is a cluster resource, not "on" the worker. Disable deletes it via Ansible. |
| **Playbook guard**                        | Ansible (phase 4 & 5)     | When `physical_ran_enabled` is true, checks whether `br-ran` exists on the worker before creating `n2-physical` and before AMF attachment. If the bridge is missing, physical-RAN-specific resources are skipped for that run so the base system can still finish deploying. Bypass: `physical_ran_skip_bridge_check: true` in `group_vars` or `-e physical_ran_skip_bridge_check=true`. |

---

## 1. Enable Integration

### Step 1: Configure Ansible

Edit `ansible/group_vars/all.yml`:

```yaml
physical_ran_enabled: true
physical_ran_interface: "" # Leave empty for auto-detect by subnet IP
physical_ran_subnet: "192.168.6.0/24"
amf_physical_ran_ip: "192.168.6.150"
ran_bridge_mode: n2_n3
ran_interface: "{{ physical_ran_interface }}"
```

`physical_ran_interface` is the **worker** NIC name (e.g. `enp0s9`). When empty, the OVS setup script auto-detects it by finding the interface with an IP in `physical_ran_subnet`.

### Step 2: Configure Vagrantfile

The worker VM needs a bridged network adapter connected to the same physical network as the gNB. In the Vagrantfile this is the `ran_network`:

```ruby
worker.vm.network "private_network", ip: "192.168.6.1",
  virtualbox__intnet: "5g-ran-network"
```

If using a USB Ethernet adapter on the host, bridge it instead:

```ruby
worker.vm.network "public_network", bridge: "enxe2b7aa97626e"
```

After changing the Vagrantfile, reload the VM:

```bash
vagrant reload worker
```

### Step 3: Apply Overlay + Core Changes

```bash
vagrant ssh ansible
cd ~/ansible-ro

# Re-deploy the OVS DaemonSet (creates br-ran, patch ports, gateway IPs)
ansible-playbook phases/04-overlay-network/playbook.yml --tags overlay

# Re-deploy 5G Core (adds n2-physical NAD to AMF, PHYSICAL_RAN_SUBNET to UPF)
ansible-playbook phases/05-5g-core/playbook.yml --tags deployments
```

---

## 2. Configure the gNB

### Network Configuration (Web UI)

For **commercial femtocells**, configure these in the device's web UI (typically under LAN, Network, or Ethernet settings):

| Parameter                    | Value                                                              | Notes                                                                           |
| ---------------------------- | ------------------------------------------------------------------ | ------------------------------------------------------------------------------- |
| gNB IP                       | `192.168.6.100/24`                                                 | Any free IP in the RAN subnet                                                   |
| **Default gateway**          | `192.168.6.1`                                                      | **Required.** Worker's br-ran. Without this, GTP-U to UPF (10.203.0.101) fails. |
| Static routes (if supported) | `10.202.0.0/16 via 192.168.6.1`<br>`10.203.0.0/16 via 192.168.6.1` | Alternative if the UI has a "Static routes" or "Route table" section            |

**Critical:** The gNB reaches the AMF (192.168.6.150) on the same subnet. To reach the UPF (10.203.0.101), it must use 192.168.6.1 as gateway. If "Default gateway" is empty or wrong, you get `Network is unreachable` when the gNB tries to send GTP-U.

### 5G Parameters

| Parameter     | Value                                   |
| ------------- | --------------------------------------- |
| MCC           | `001`                                   |
| MNC           | `01`                                    |
| TAC           | `1`                                     |
| AMF IP        | `192.168.6.150` (AMF's n2phy interface) |
| AMF SCTP Port | `38412`                                 |
| S-NSSAI       | SST=1, SD=0x000001                      |

---

## 3. Physical Connection

### With VirtualBox

The gNB must be on the same L2 segment as the host NIC specified in `PHYSICAL_RAN_BRIDGE`. Vagrant bridges that NIC into the worker VM.

**NUC / Server** — the gNB is connected via a router or switch to one of the NUC's built-in Ethernet ports:

```
[NUC]
    └── enp2s0 ─── [Router / Switch] ─── gNB (192.168.6.100)
                          │
    [VirtualBox]          │
        └── Worker VM (enp0s9) ────┘
            bridged to enp2s0
```

`./testbed-config ran enp2s0` — use the NIC name that is on the gNB's network.

**Laptop** — USB-to-Ethernet adapter bridged to the same switch:

```
[Laptop]
    └── USB Ethernet (enx00e04c...) ─── [Switch] ─── gNB (192.168.6.100)
                                            │
    [VirtualBox]                            │
        └── Worker VM (enp0s9) ─────────────┘
            bridged to USB adapter
```

`./testbed-config ran enx00e04c6817b7` — use the adapter's interface name.

### Bare Metal (production)

Connect the gNB directly to the worker's dedicated RAN NIC. No VirtualBox bridging needed.

---

## 4. Verify

### OVS Bridges and Patch Ports

```bash
vagrant ssh worker
sudo ovs-vsctl show | grep -A8 br-ran
```

Expected:

```
Bridge br-ran
    Port enp0s9
        Interface enp0s9
    Port patch-ran-n2
        Interface patch-ran-n2
            type: patch
            options: {peer=patch-n2-ran}
    Port patch-ran-n3
        Interface patch-ran-n3
            type: patch
            options: {peer=patch-n3-ran}
```

### Gateway IPs

```bash
ip -4 addr show br-ran | grep inet    # 192.168.6.1/24
ip -4 addr show br-n3 | grep inet     # 10.203.0.1/24 and 10.203.0.254/24
```

### gNB Reachability

```bash
# From the gNB
ping 192.168.6.1      # Worker br-ran gateway
ping 192.168.6.150    # AMF n2phy
ping 10.203.0.101     # UPF N3 (via routing through worker)
```

### AMF Registration

```bash
sudo k3s kubectl logs -f -l app=amf -n 5g | grep -i gnb
```

Expected:

```
[Added] Number of gNBs is now 1
```

### UPF Return Route

```bash
sudo k3s kubectl exec -n 5g deploy/upf-cloud -- ip route show | grep 192.168.5
```

Expected:

```
192.168.6.0/24 via 10.203.0.254 dev n3
```

---

## 5. Switch Back to UERANSIM

```yaml
# ansible/group_vars/all.yml
physical_ran_enabled: false
ran_bridge_mode: disabled
```

```bash
vagrant ssh ansible
cd ~/ansible-ro
ansible-playbook phases/04-overlay-network/playbook.yml --tags overlay
ansible-playbook phases/05-5g-core/playbook.yml --tags deployments
ansible-playbook phases/06-ueransim-mec/playbook.yml
```

---

## Troubleshooting

| Problem                                 | Cause                                         | Solution                                                                                                                                                                                        |
| --------------------------------------- | --------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ping 192.168.6.1` fails                | br-ran has no IP                              | Re-run overlay playbook; check `RAN_SUBNET` env var                                                                                                                                             |
| `ping 10.203.0.101` fails from gNB      | Missing route on gNB                          | **Commercial femtocell:** set Default gateway = 192.168.6.1 in web UI. Software gNB: `ip route add 10.203.0.0/16 via 192.168.6.1`                                                               |
| UPF can't reach gNB (no GTP-U downlink) | Missing return route in UPF                   | Check `PHYSICAL_RAN_SUBNET` env var in UPF deployment                                                                                                                                           |
| AMF doesn't see gNB                     | PLMN mismatch or SCTP issue                   | Check MCC/MNC/TAC; `sudo modprobe sctp` on worker                                                                                                                                               |
| `failed to find bridge br-ran`          | NAD / AMF reference `br-ran` but OVS never created it | Worker needs RAN NIC + overlay applied; restart `ds-net-setup-worker` on worker. Same root cause as a skipped physical-RAN configuration (table *OVS DaemonSet vs NAD*).                            |
| Physical RAN is skipped during deploy   | `physical_ran_enabled` true, `br-ran` missing on worker | Apply `PHYSICAL_RAN_BRIDGE` to the worker VM (`testbed-config provision` now reloads the worker automatically when needed, or run `vagrant reload worker`), then re-run overlay/core or enable later from the dashboard. |
| br-ran persists after Disable           | DS pod restarted before teardown; old script  | Fixed: ovs-setup.sh now tears down br-ran when RAN_BRIDGE_MODE=disabled. Re-run Disable or `ansible-playbook phases/04-overlay-network/playbook.yml --tags overlay -e ran_bridge_mode=disabled` |
| NAD n2-physical persists after Disable  | Playbook only skipped creation, never deleted | Fixed: multus_install now deletes the NAD when physical_ran_enabled=false. Re-run `ansible-playbook phases/04-overlay-network/playbook.yml --tags nad -e physical_ran_enabled=false`            |
| `macvlan: device or resource busy`      | n2-physical NAD misconfigured                 | Ensure NAD uses `type: ovs, bridge: br-ran`                                                                                                                                                     |
| UE authenticated but no data            | PDU session fails at PFCP                     | Check SMF→UPF N4 connectivity; check UPF logs                                                                                                                                                   |

### Useful Commands

```bash
# Check OVS bridge details
sudo ovs-vsctl show

# Check all bridge IPs on worker
ip -4 addr show | grep -E 'br-(ran|n2|n3)'

# Check UPF routing table
sudo k3s kubectl exec -n 5g deploy/upf-cloud -- ip route

# Check AMF NGAP listener
sudo k3s kubectl exec -n 5g deploy/amf -- ss -Slnp | grep 38412

# Capture GTP-U traffic on br-n3
sudo tcpdump -i br-n3 udp port 2152 -c 10

# Check subscribers in MongoDB
sudo k3s kubectl exec -n 5g deploy/mongodb -- mongosh open5gs --eval "db.subscribers.find()"
```
