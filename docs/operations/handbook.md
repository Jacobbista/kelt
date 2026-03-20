# 5G K8s Testbed Handbook

This is the canonical, exhaustive documentation for the testbed. It reflects the code under `ansible/phases/*` and the Vagrant topology.

> **kubectl**: K3s does not install a standalone `kubectl` binary. All commands in this handbook run from master (`vagrant ssh master`) using **`sudo k3s kubectl`**.

## 1. Architecture & Topology

- Nodes (Vagrant):
  - master: k3s server (control-plane)
  - worker: k3s agent; KubeEdge CloudCore; Open5GS 5G Core; OVS bridges; Multus
  - edge (optional): KubeEdge EdgeCore (no k3s-agent); OVS bridges; Multus; gNB + UEs (UERANSIM). Disabled by default in `server` profile — see [Server Setup](../deployment/server-setup.md)
  - ansible: orchestration node; Dashboard (FastAPI + React)
- Overlay (worker↔edge): OVS bridges per 5G interface, VXLAN tunnels with fixed VNI keys
- Primary CNI: Flannel; Multus is secondary

IPs (as provisioned in Vagrantfile): master 192.168.56.10, worker 192.168.56.11, edge 192.168.56.12, ansible 192.168.56.13

## 2. Interfaces Matrix (Complete)

For each interface: purpose, plan, bridge, VXLAN key, NAD, validation. Static IPs defined in `ansible/group_vars/all.yml` are referenced where applicable.

### N1 — UE ↔ AMF (NAS)

- **Purpose**: Non-Access Stratum signaling between UE and AMF
- **Subnet**: 10.201.0.0/24; Gateway: 10.201.0.1; Range: 10.201.0.100-250
- **OVS bridge**: br-n1; **VXLAN key**: 1; **NAD**: 5g/n1-net
- **Static IP**: AMF = 10.201.0.100 (from `amf_n1_ip`)
- **IPAM reservation**: `n1-net` excludes `amf_n1_ip/32` in Whereabouts to prevent dynamic reuse of the AMF static IP
- **Protocol**: NAS over SCTP
- **Validation**:

  ```bash
  # Check NAD exists
  sudo k3s kubectl -n 5g get net-attach-def n1-net

  # Check AMF has N1 interface with correct IP
  sudo k3s kubectl -n 5g exec deploy/amf -- ip -o -4 addr show dev n1 | awk '{print $4}' | cut -d/ -f1
  # Expected: 10.201.0.100

  # Check network status annotation
  sudo k3s kubectl -n 5g get pod -l app=amf -o json | jq -r '.items[0].metadata.annotations["k8s.v1.cni.cncf.io/network-status"]' | jq '.'
  ```

### N2 — gNB ↔ AMF (NGAP, SCTP 38412)

- **Purpose**: NGAP signaling between gNB and AMF
- **Subnet**: 10.202.0.0/24; Gateway: 10.202.0.1; Range: 10.202.0.100-250
- **OVS bridge**: br-n2; **VXLAN key**: 2; **NAD**: 5g/n2-net
- **Static IP**: AMF = 10.202.0.100 (from `amf_n2_ip`)
- **IPAM reservation**: `n2-net` excludes `amf_n2_ip/32` in Whereabouts to prevent dynamic reuse of the AMF static IP
- **Protocol**: NGAP over SCTP port 38412
- **Validation**:

  ```bash
  # Check NAD exists
  sudo k3s kubectl -n 5g get net-attach-def n2-net

  # Check AMF SCTP port 38412
  sudo k3s kubectl -n 5g exec deploy/amf -- bash -lc 'ss -S -na | grep 38412 || echo no-sctp'

  # Check AMF has N2 interface with correct IP
  sudo k3s kubectl -n 5g exec deploy/amf -- ip -o -4 addr show dev n2 | awk '{print $4}' | cut -d/ -f1
  # Expected: 10.202.0.100

  # Check AMF logs for NGAP activity
  sudo k3s kubectl -n 5g logs deploy/amf -c amf --tail=200 | grep -i ngap
  ```

### N3 — gNB/UE ↔ UPF (GTP-U, UDP 2152)

- **Purpose**: GTP-U data plane between gNB/UE and UPF
- **Subnet**: 10.203.0.0/24; Gateway: 10.203.0.1; Range: 10.203.0.100-250
- **OVS bridge**: br-n3; **VXLAN key**: 3; **NAD**: 5g/n3-net
- **Static IPs**:
  - UPF-edge = 10.203.0.100 (from `upf_edge_n3_ip`)
  - UPF-cloud = 10.203.0.101 (from `upf_cloud_n3_ip`)
- **IPAM reservation**: `n3-net` excludes `upf_edge_n3_ip/32` and `upf_cloud_n3_ip/32` in Whereabouts
- **Protocol**: GTP-U over UDP port 2152
- **Validation**:

  ```bash
  # Check NAD exists
  sudo k3s kubectl -n 5g get net-attach-def n3-net

  # Check UPF-edge GTP-U port 2152
  sudo k3s kubectl -n 5g exec deploy/upf-edge -- bash -lc 'ss -unap | grep 2152'

  # Check UPF-cloud GTP-U port 2152
  sudo k3s kubectl -n 5g exec deploy/upf-cloud -- bash -lc 'ss -unap | grep 2152'

  # Check N3 interface IPs
  sudo k3s kubectl -n 5g exec deploy/upf-edge -- ip -o -4 addr show dev n3 | awk '{print $4}' | cut -d/ -f1
  # Expected: 10.203.0.100

  sudo k3s kubectl -n 5g exec deploy/upf-cloud -- ip -o -4 addr show dev n3 | awk '{print $4}' | cut -d/ -f1
  # Expected: 10.203.0.101
  ```

### N4 — SMF ↔ UPF (PFCP, UDP 8805)

- **Purpose**: PFCP control plane between SMF and UPF
- **Subnet**: 10.204.0.0/24; Gateway: 10.204.0.1; Range: 10.204.0.100-250
- **OVS bridge**: br-n4; **VXLAN key**: 4; **NAD**: 5g/n4-net
- **Static IPs**:
  - SMF = 10.204.0.100 (from `smf_n4_ip`)
  - UPF-edge = 10.204.0.101 (from `upf_edge_n4_ip`)
  - UPF-cloud = 10.204.0.102 (from `upf_cloud_n4_ip`)
- **Protocol**: PFCP over UDP port 8805
- **Validation**:

  ```bash
  # Check NAD exists
  sudo k3s kubectl -n 5g get net-attach-def n4-net

  # Check SMF PFCP port 8805
  sudo k3s kubectl -n 5g exec deploy/smf -- bash -lc 'ss -unap | grep 8805 || echo no-pfcp'

  # Check SMF can reach UPF-edge on N4
  sudo k3s kubectl -n 5g exec deploy/smf -- bash -lc 'nc -zuvw1 10.204.0.101 8805 || echo pfcp-fail'

  # Check SMF can reach UPF-cloud on N4
  sudo k3s kubectl -n 5g exec deploy/smf -- bash -lc 'nc -zuvw1 10.204.0.102 8805 || echo pfcp-fail'

  # Check N4 interface IPs
  sudo k3s kubectl -n 5g exec deploy/smf -- ip -o -4 addr show dev n4 | awk '{print $4}' | cut -d/ -f1
  # Expected: 10.204.0.100

  # Check SMF logs for PFCP activity
  sudo k3s kubectl -n 5g logs deploy/smf -c smf --tail=200 | grep -i pfcp
  ```

### N6e — UPF-edge ↔ MEC

- **Purpose**: Data plane between UPF-edge and MEC applications
- **Subnet**: 10.206.0.0/24; Gateway: 10.206.0.1; Range: 10.206.0.100-250
- **OVS bridge**: br-n6e; **VXLAN key**: 6; **NAD**: mec/n6-mec-net
- **Static IP**: UPF-edge gets dynamic IP from Whereabouts IPAM
- **Protocol**: IP routing
- **Validation**:

  ```bash
  # Check NAD exists in mec namespace
  sudo k3s kubectl -n mec get net-attach-def n6-mec-net

  # Check UPF-edge has N6 interface
  sudo k3s kubectl -n 5g exec deploy/upf-edge -- ip -o -4 addr show dev n6 | awk '{print $4}' | cut -d/ -f1

  # Check MEC pod has N6 interface (if deployed)
  sudo k3s kubectl -n mec get pod -l app=mec -o json | jq -r '.items[0].metadata.annotations["k8s.v1.cni.cncf.io/network-status"]' | jq '.'
  ```

### N6c — UPF-cloud ↔ DN

- **Purpose**: Data plane between UPF-cloud and Data Network
- **Subnet**: 10.207.0.0/24; Gateway: 10.207.0.1; Range: 10.207.0.100-250
- **OVS bridge**: br-n6c; **VXLAN key**: 7; **NAD**: 5g/n6-cld-net
- **Static IP**: UPF-cloud gets dynamic IP from Whereabouts IPAM
- **Protocol**: IP routing
- **Validation**:

  ```bash
  # Check NAD exists
  sudo k3s kubectl -n 5g get net-attach-def n6-cld-net

  # Check UPF-cloud has N6 interface
  sudo k3s kubectl -n 5g exec deploy/upf-cloud -- ip -o -4 addr show dev n6 | awk '{print $4}' | cut -d/ -f1
  ```

**Outbound Internet access (N6c)**:
To allow traffic from `10.207.0.0/24` to reach external networks, the worker VM must have IP forwarding enabled and an outbound NAT rule. This repo's `Vagrantfile` applies these settings on the **worker** at boot/reload (iptables rules are not persistent by default).

```bash
# On the worker VM
ssh worker "sysctl net.ipv4.ip_forward"
ssh worker "sudo iptables -t nat -S POSTROUTING | grep 10.207.0.0/24 || true"
```

### N6m — UPF-Cloud ↔ MEC Services

- **Purpose**: Data plane between UPF-Cloud and MEC application pods (via DNN "mec")
- **Subnet**: 10.208.0.0/24; Gateway: 10.208.0.1; Range: 10.208.0.100-250
- **OVS bridge**: br-n6m; **VXLAN key**: 108; **NAD**: mec/n6m-net
- **UPF-Cloud routing**: UE traffic on DNN "mec" enters `ogstun2` (10.46.0.0/16), policy routing table 300 forwards it via `10.208.0.1` on the `n6m` interface to MEC pods
- **Protocol**: IP routing
- **Validation**:

  ```bash
  # Check NAD exists in mec namespace
  sudo k3s kubectl -n mec get net-attach-def n6m-net

  # Check UPF-Cloud has N6m interface
  sudo k3s kubectl -n 5g exec deploy/upf-cloud -- ip -o -4 addr show dev n6m | awk '{print $4}' | cut -d/ -f1

  # Check ogstun2 TUN interface exists in UPF-Cloud
  sudo k3s kubectl -n 5g exec deploy/upf-cloud -- ip link show ogstun2

  # Check policy routing table 300
  sudo k3s kubectl -n 5g exec deploy/upf-cloud -- ip rule show | grep "lookup 300"
  sudo k3s kubectl -n 5g exec deploy/upf-cloud -- ip route show table 300
  # Expected: default via 10.208.0.1 dev n6m

  # Check MEC pod has N6m interface (if deployed)
  sudo k3s kubectl -n mec get pod -o json | jq -r '.items[0].metadata.annotations["k8s.v1.cni.cncf.io/network-status"]' | jq '.'
  ```

### Future Interfaces (Placeholder)

The following interfaces are planned for future implementation:

- **N11** — AMF ↔ AUSF (authentication)
- **N9** — UPF ↔ UPF (inter-UPF communication)
- **N15** — PCF ↔ AMF (policy control)
- **N16** — PCF ↔ SMF (policy control)

These will be implemented as additional Multus NADs and OVS bridges when needed for advanced 5G scenarios.

## 3. VXLAN Tunnel Configuration

The overlay network uses VXLAN tunnels between worker and edge nodes with the following key mappings:

| Interface | VXLAN Key | Bridge | Purpose              |
| --------- | --------- | ------ | -------------------- |
| N1        | 1         | br-n1  | UE ↔ AMF (NAS)       |
| N2        | 2         | br-n2  | gNB ↔ AMF (NGAP)     |
| N3        | 3         | br-n3  | gNB/UE ↔ UPF (GTP-U) |
| N4        | 4         | br-n4  | SMF ↔ UPF (PFCP)     |
| N6e       | 6         | br-n6e | UPF-edge ↔ MEC       |
| N6c       | 7         | br-n6c | UPF-cloud ↔ DN       |
| N6m       | 108       | br-n6m | UPF-cloud ↔ MEC      |

**VXLAN Configuration:**

- **Remote IP**: Worker ↔ Edge (192.168.56.11 ↔ 192.168.56.12)
- **Local IP**: Auto-detected from routing table
- **UDP Port**: 4789 (standard VXLAN port)
- **TOS**: Inherit from inner packet
- **DF**: false (allow fragmentation)

**Validation:**

```bash
# Check VXLAN interfaces on worker
sudo ovs-vsctl show | grep -A5 vxlan

# Check VXLAN interfaces on edge
sudo ovs-vsctl show | grep -A5 vxlan

# Check VXLAN tunnel status
ip -d link show | grep -A2 vxlan
```

## 4. Deployment Phases

The testbed is deployed in 6 phases using Ansible. Each phase builds upon the previous one:

### Phase 1: Infrastructure Setup

**What it does:**

- Installs system packages (chrony, iptables, net-tools, OVS)
- Configures IP forwarding and iptables rules
- Sets up hostname resolution
- Tests connectivity between nodes

**Run phase:**

```bash
ansible-playbook phases/01-infrastructure/playbook.yml -i inventory.ini
```

**Verification:**

```bash
# Check all nodes are reachable
for node in master worker edge; do
  ping -c 1 $node
done

# Check OVS is installed on worker/edge
ssh worker "dpkg -l | grep openvswitch"
ssh edge "dpkg -l | grep openvswitch"

# Check IP forwarding is enabled
ssh worker "sysctl net.ipv4.ip_forward"
ssh edge "sysctl net.ipv4.ip_forward"
```

### Phase 2: Kubernetes Cluster (K3s)

**What it does:**

- Installs k3s server on master
- Installs k3s agents on worker/edge
- Configures Flannel CNI
- Sets up kubeconfig

**Run phase:**

```bash
ansible-playbook phases/02-kubernetes/playbook.yml -i inventory.ini
```

**Verification:**

```bash
# Check cluster nodes
sudo k3s kubectl get nodes -o wide

# Check k3s services
ssh master "systemctl status k3s"
ssh worker "systemctl status k3s-agent"
ssh edge "systemctl status k3s-agent"

# Check Flannel is working
sudo k3s kubectl get pods -n kube-system -l app=flannel
```

### Phase 3: KubeEdge Integration

**What it does:**

- Installs CloudCore on worker
- Installs EdgeCore on edge
- Establishes cloud↔edge communication
- Configures containerd for KubeEdge

**Run phase:**

```bash
ansible-playbook phases/03-kubeedge/playbook.yml -i inventory.ini
```

**Verification:**

```bash
# Check KubeEdge pods
sudo k3s kubectl -n kubeedge get pods

# Check edge node is registered
sudo k3s kubectl get nodes -l node-type=edge

# Check CloudCore logs
sudo k3s kubectl -n kubeedge logs -l app=cloudcore --tail=100

# Check EdgeCore logs
ssh edge "journalctl -u edgecore --tail=100"
```

### Phase 4: Overlay Network (OVS + Multus)

**What it does:**

- Creates OVS bridges (br-n1, br-n2, br-n3, br-n4, br-n6e, br-n6c)
- Establishes VXLAN tunnels between worker↔edge
- Installs Multus CNI
- Creates NetworkAttachmentDefinitions
- Sets up Whereabouts IPAM

**Run phase:**

```bash
ansible-playbook phases/04-overlay-network/playbook.yml -i inventory.ini
```

**Verification:**

```bash
# Check OVS bridges
sudo ovs-vsctl show

# Check VXLAN tunnels
ip -d link show | grep vxlan

# Check Multus DaemonSet
sudo k3s kubectl -n kube-system get ds kube-multus-ds

# Check NetworkAttachmentDefinitions
sudo k3s kubectl get net-attach-def -A
```

### Phase 5: 5G Core Network Functions

**What it does:**

- Deploys Open5GS 5G Core (NRF, AMF, SMF, UPF, UDM, UDR, PCF, BSF, NSSF, AUSF)
- Configures static IPs for network functions
- Sets up MongoDB for subscriber data
- Imports subscriber database

**Run phase:**

```bash
ansible-playbook phases/05-5g-core/playbook.yml -i inventory.ini
```

**Verification:**

```bash
# Check 5G Core deployments
sudo k3s kubectl -n 5g get deploy,svc

# Check all pods are running
sudo k3s kubectl -n 5g get pods

# Check static IPs are assigned
sudo k3s kubectl -n 5g exec deploy/amf -- ip addr show dev n1
sudo k3s kubectl -n 5g exec deploy/amf -- ip addr show dev n2
sudo k3s kubectl -n 5g exec deploy/smf -- ip addr show dev n4
```

### Phase 6: UERANSIM & MEC

**What it does:**

- Deploys UERANSIM (gNB/UE simulator)
- Sets up MEC applications
- Configures edge computing scenarios

**Run phase:**

```bash
ansible-playbook phases/06-ueransim-mec/playbook.yml -i inventory.ini
```

**Verification:**

```bash
# Check UERANSIM pods
sudo k3s kubectl -n 5g get pods -l app=gnb
sudo k3s kubectl -n 5g get pods -l app=ue

# Check MEC pods
sudo k3s kubectl -n mec get pods

# Check gNB logs
sudo k3s kubectl -n 5g logs -l app=gnb --tail=100

# Check UE logs
sudo k3s kubectl -n 5g logs -l app=ue --tail=100
```

## 5. Operations

### Full Deployment

To deploy the entire testbed:

```bash
# Start all VMs
vagrant up

# Or run specific phases
vagrant ssh ansible
cd /home/vagrant/ansible-ro
ansible-playbook phases/00-main-playbook.yml
```

### Phase-by-Phase Deployment

To run individual phases:

```bash
# Run specific phase
ansible-playbook phases/0X-phase-name/playbook.yml -i inventory.ini

# Run with tags
ansible-playbook phases/00-main-playbook.yml --tags phase4,phase5
```

### Pod Migration

To migrate UPF/MEC between cloud and edge:

```bash
# Check current placement
sudo k3s kubectl -n 5g get pods -o wide

# Migrate UPF-edge to worker (cloud)
sudo k3s kubectl -n 5g patch deployment upf-edge -p '{"spec":{"template":{"spec":{"nodeSelector":{"kubernetes.io/hostname":"worker"}}}}}'

# Migrate UPF-cloud to edge
sudo k3s kubectl -n 5g patch deployment upf-cloud -p '{"spec":{"template":{"spec":{"nodeSelector":{"kubernetes.io/hostname":"edge"}}}}}'

# Verify migration
sudo k3s kubectl -n 5g get pods -o wide
```

### Restart Services

```bash
# Restart specific deployment
sudo k3s kubectl -n 5g rollout restart deployment/amf

# Restart all 5G Core
sudo k3s kubectl -n 5g rollout restart deployment/

# Check rollout status
sudo k3s kubectl -n 5g rollout status deployment/amf
```

## 6. Troubleshooting

### Common Issues

#### 1. Pod Not Getting Network Interface

**Symptoms:**

- Pod shows only default interface
- No Multus annotation in pod metadata

**Diagnosis:**

```bash
# Check pod annotations
sudo k3s kubectl -n 5g get pod <pod-name> -o json | jq '.metadata.annotations'

# Check Multus DaemonSet
sudo k3s kubectl -n kube-system get ds kube-multus-ds

# Check Multus logs
sudo k3s kubectl -n kube-system logs -l app=multus --tail=100
```

**Solution:**

- Ensure Multus DaemonSet is running
- Check NetworkAttachmentDefinition exists
- Verify pod has correct annotation

#### 2. VXLAN Tunnel Issues

**Symptoms:**

- No connectivity between worker↔edge
- VXLAN interfaces not created

**Diagnosis:**

```bash
# Check OVS bridges
sudo ovs-vsctl show

# Check VXLAN interfaces
ip -d link show | grep vxlan

# Check OVS DaemonSet logs
sudo k3s kubectl -n kube-system logs -l app=ds-net-setup-worker --tail=100
```

**Solution:**

- Restart OVS DaemonSet
- Check worker↔edge connectivity
- Verify VXLAN configuration

#### 3. 5G Core Not Starting

**Symptoms:**

- Pods stuck in Pending/CrashLoopBackOff
- Network functions not responding

**Diagnosis:**

```bash
# Check pod status
sudo k3s kubectl -n 5g get pods

# Check pod logs
sudo k3s kubectl -n 5g logs <pod-name> --tail=100

# Check pod events
sudo k3s kubectl -n 5g describe pod <pod-name>
```

**Solution:**

- Check resource requirements
- Verify network connectivity
- Check configuration files

#### 4. KubeEdge Edge Node Not Joining

**Symptoms:**

- Edge node not visible in cluster
- EdgeCore not connecting to CloudCore

**Diagnosis:**

```bash
# Check edge node status
sudo k3s kubectl get nodes -l node-type=edge

# Check EdgeCore logs
ssh edge "journalctl -u edgecore --tail=100"

# Check CloudCore logs
sudo k3s kubectl -n kubeedge logs -l app=cloudcore --tail=100
```

**Solution:**

- Verify network connectivity (port 10000)
- Check EdgeCore configuration
- Restart EdgeCore service

### Diagnostic Commands

#### Network Diagnostics

```bash
# Check all network interfaces
sudo k3s kubectl -n 5g exec <pod-name> -- ip addr show

# Check routing table
sudo k3s kubectl -n 5g exec <pod-name> -- ip route show

# Test connectivity
sudo k3s kubectl -n 5g exec <pod-name> -- ping -c 3 <target-ip>

# Check port connectivity
sudo k3s kubectl -n 5g exec <pod-name> -- nc -zv <target-ip> <port>
```

#### OVS Diagnostics

```bash
# Check OVS bridges
sudo ovs-vsctl show

# Check bridge ports
sudo ovs-vsctl list-ports br-n3

# Check VXLAN interfaces
sudo ovs-vsctl list interface | grep vxlan

# Check OVS flows
sudo ovs-ofctl dump-flows br-n3
```

#### Kubernetes Diagnostics

```bash
# Check node resources
sudo k3s kubectl describe node <node-name>

# Check pod events
sudo k3s kubectl get events --sort-by=.metadata.creationTimestamp

# Check service endpoints
sudo k3s kubectl -n 5g get endpoints

# Check network policies
sudo k3s kubectl get networkpolicy -A
```

## 7. Future Enhancements

### Monitoring & Observability

- **Prometheus Integration**: Metrics collection for 5G network functions
- **Grafana Dashboards**: Real-time visualization of network performance
- **Loki Logging**: Centralized log aggregation and analysis
- **cAdvisor**: Container resource monitoring

### Benchmarking & Testing

- **Performance Testing**: Automated load testing for 5G scenarios
- **Latency Measurement**: End-to-end latency analysis
- **Throughput Testing**: Data plane performance validation
- **Stress Testing**: System behavior under high load

### Advanced Features

- **Service Mesh**: Istio integration for advanced traffic management
- **Policy Engine**: Dynamic policy enforcement
- **AI/ML Integration**: Intelligent network optimization
- **Multi-Cloud**: Support for multiple cloud providers

### Development Tools

- **CI/CD Pipeline**: Automated testing and deployment
- **Development Environment**: Local development setup
- **API Documentation**: OpenAPI specifications
- **Testing Framework**: Comprehensive test suite

## 8. Quick Reference

### Essential Commands

```bash
# Check cluster status
sudo k3s kubectl get nodes -o wide
sudo k3s kubectl get pods -A

# Check 5G Core
sudo k3s kubectl -n 5g get deploy,svc
sudo k3s kubectl -n 5g get pods

# Check network interfaces
sudo k3s kubectl -n 5g exec deploy/amf -- ip addr show
sudo k3s kubectl -n 5g exec deploy/smf -- ip addr show

# Check OVS bridges
sudo ovs-vsctl show

# Check VXLAN tunnels
ip -d link show | grep vxlan

# Check Multus NADs
sudo k3s kubectl get net-attach-def -A
```

### Log Locations

```bash
# K3s logs
journalctl -u k3s -f
journalctl -u k3s-agent -f

# KubeEdge logs
sudo k3s kubectl -n kubeedge logs -l app=cloudcore -f
ssh edge "journalctl -u edgecore -f"

# 5G Core logs
sudo k3s kubectl -n 5g logs deploy/amf -f
sudo k3s kubectl -n 5g logs deploy/smf -f
sudo k3s kubectl -n 5g logs deploy/upf-edge -f

# OVS logs
sudo k3s kubectl -n kube-system logs -l app=ds-net-setup-worker -f
```

### Configuration Files

- **Ansible Variables**: `ansible/group_vars/all.yml`
- **Vagrant Configuration**: `Vagrantfile`
- **5G Core Configs**: `ansible/phases/05-5g-core/configs/`
- **OVS Scripts**: `ansible/phases/04-overlay-network/scripts/`

---

**This handbook is the canonical source of truth for the 5G K3s KubeEdge Testbed.**

### N15 — PCF ↔ AMF; N16 — PCF ↔ SMF

- Planned. Provide NADs, bridges, and validation similar to above after enabling PCF policy interfaces.

## 3. Phases — Procedures, Commands, Validation, Triage

All phases can be run from Ansible host:

```bash
ansible-playbook phases/00-main-playbook.yml --tags phaseX
```

### Phase 1 — Infrastructure

Run:

```bash
ansible-playbook phases/01-infrastructure/playbook.yml
```

Validate:

```bash
for n in master worker edge; do ssh $n 'hostname; ip a | sed -n "1,40p"'; done
ssh worker 'dpkg -l | egrep "openvswitch-switch|chrony|iptables"'
ssh edge   'dpkg -l | egrep "openvswitch-switch|chrony|iptables"'
```

### Phase 2 — Kubernetes (k3s)

Run:

```bash
ansible-playbook phases/02-kubernetes/playbook.yml
```

Validate:

```bash
sudo k3s kubectl get nodes -o wide
sudo k3s kubectl get pods -n kube-system
journalctl -u k3s --no-pager | tail -200
journalctl -u k3s-agent --no-pager | tail -200
```

### Phase 3 — KubeEdge

Run:

```bash
ansible-playbook phases/03-kubeedge/playbook.yml
```

Validate:

```bash
sudo k3s kubectl -n kubeedge get pods -o wide
sudo k3s kubectl get nodes -o wide | grep edge
sudo k3s kubectl -n kubeedge logs -l app=cloudcore --tail=200
```

### Phase 4 — Overlay (OVS + Multus)

Run:

```bash
ansible-playbook phases/04-overlay-network/playbook.yml
```

Validate:

```bash
sudo k3s kubectl -n kube-system get ds | grep -E "ds-net-setup|kube-multus-ds"
sudo k3s kubectl get network-attachment-definitions -A
sudo k3s kubectl -n kube-system logs ds/kube-multus-ds --tail=200 || true
sudo k3s kubectl -n kube-system logs -l app=ds-net-setup-worker --tail=200 || true
sudo k3s kubectl -n kube-system logs -l app=ds-net-setup-edge --tail=200 || true
```

Node-level checks (on worker/edge):

```bash
sudo ovs-vsctl show
ip -d link show | grep -A2 vxlan-
```

### Phase 5 — 5G Core

Run:

```bash
ansible-playbook phases/05-5g-core/playbook.yml
```

Validate deployments:

```bash
sudo k3s kubectl -n 5g get deploy,svc
for d in mongodb nrf amf smf upf-edge upf-cloud udm udr pcf bsf nssf ausf; do
  sudo k3s kubectl -n 5g rollout status deploy/$d --timeout=60s || true
done
```

Targeted logs:

```bash
sudo k3s kubectl -n 5g logs deploy/smf -c smf --tail=200 | head
sudo k3s kubectl -n 5g logs deploy/amf -c amf --tail=200 | head
sudo k3s kubectl -n 5g logs deploy/upf-edge -c upf --tail=200 | head
```

PFCP/NGAP/GTP-U quick checks:

```bash
sudo k3s kubectl -n 5g exec deploy/smf -- bash -lc 'nc -zuvw1 10.204.0.101 8805 || ss -unap | grep 8805 || echo pfcp-fail'
sudo k3s kubectl -n 5g exec deploy/amf -- bash -lc 'ss -S -na | grep 38412 || echo no-sctp'
sudo k3s kubectl -n 5g exec deploy/upf-edge -- bash -lc 'ss -unap | grep 2152 || echo no-gtpu'
```

### Phase 6 — UERANSIM & MEC

Run:

```bash
ansible-playbook phases/06-ueransim-mec/playbook.yml
```

Validate:

```bash
sudo k3s kubectl -n 5g get pods -l app=gnb
sudo k3s kubectl -n 5g get pods -l app=ue
sudo k3s kubectl -n 5g logs deploy/ue -c ue -f
```

## 4. Operations

### Migrate UPF/MEC between nodes

```bash
# Move UPF-edge (example) to master or back to edge
sudo k3s kubectl patch deployment upf-edge -n 5g --type='json' -p='[
  {"op":"add","path":"/spec/template/spec/nodeSelector","value":{"kubernetes.io/hostname":"edge"}}
]'
sudo k3s kubectl -n 5g rollout status deploy/upf-edge --timeout=120s
```

### Safe restarts and rollouts

```bash
sudo k3s kubectl -n 5g rollout restart deploy/smf
sudo k3s kubectl -n 5g rollout status deploy/smf --timeout=120s
```

## 5. Troubleshooting Catalog

### Multus interface missing on pod

```bash
sudo k3s kubectl -n 5g get pod <pod> -o json | jq -r '.metadata.annotations["k8s.v1.cni.cncf.io/network-status"]' | jq '.'
sudo k3s kubectl -n 5g exec <pod> -- ip link show
sudo k3s kubectl -n kube-system logs ds/kube-multus-ds --tail=300
```

### PFCP not established (SMF↔UPF)

```bash
sudo k3s kubectl -n 5g exec deploy/smf -- bash -lc 'nc -zuvw1 10.204.0.101 8805 || ss -unap | grep 8805 || echo pfcp-fail'
sudo k3s kubectl -n 5g logs deploy/smf -c smf --tail=300
sudo k3s kubectl -n 5g logs deploy/upf-edge -c upf --tail=300
```

### NGAP not established (gNB↔AMF)

```bash
sudo k3s kubectl -n 5g exec deploy/amf -- bash -lc 'ss -S -na | grep 38412 || echo no-sctp'
sudo k3s kubectl -n 5g logs deploy/amf -c amf --tail=300
```

### VXLAN/OVS anomalies

```bash
sudo k3s kubectl -n kube-system logs -l app=ds-net-setup-worker --tail=200
sudo k3s kubectl -n kube-system logs -l app=ds-net-setup-edge --tail=200
sudo ovs-vsctl show
ip -d link show | grep -A2 vxlan-
```

## 6. Future Improvements & Roadmap

### Immediate Priorities (Phase A: Testing & Validation)

#### End-to-End Testing

- **UE → UPF-Edge TUN**: `kubectl -n 5g exec deploy/ue -- ping -c 5 10.46.0.1`
- **gNB → AMF (N2)**: `kubectl -n 5g exec deploy/gnb -- ping -I n2 -c 5 10.202.0.100`
- **gNB → UPF-Edge (N3)**: `kubectl -n 5g exec deploy/gnb -- ping -I n3 -c 5 10.203.0.1`
- **Performance Testing**: Latency (RTT), bandwidth (iperf3), load testing, stress testing

#### Resilience Testing

- Pod restart recovery
- Network failure recovery
- OVS GC verification
- Multus interface recovery

### Interface Completion (Phase B: 3-5 days)

#### High Priority Interfaces

- **N11 (AMF ↔ SMF)**: NAD `n11-net` (10.211.0.0/24), bridge `br-n11`, VXLAN key 111
- **N9 (UPF ↔ UPF)**: Inter-UPF communication, bridge `br-n9`
- **N15 (PCF ↔ AMF)**: Policy control, bridge `br-n15`
- **N16 (PCF ↔ SMF)**: Policy control, bridge `br-n16`

#### Medium Priority Interfaces

- **N12 (AMF ↔ AUSF)**: Authentication
- **N14 (AMF ↔ AMF)**: Inter-AMF communication

### Network Slicing (Phase C: 2-3 days)

#### Slice Types

- **eMBB**: Enhanced Mobile Broadband
- **URLLC**: Ultra-Reliable Low-Latency Communications
- **mMTC**: Massive Machine Type Communications

#### Implementation Areas

- Slice-aware routing
- QoS differentiation
- Traffic steering
- Slice lifecycle management

### Code Quality & Optimization (Phase D: 2-3 days)

#### Playbook Improvements

- Migrate shell tasks to `kubernetes.core` modules for idempotence
- Add readiness/liveness probes for all NFs
- Set `revisionHistoryLimit` on Deployments
- Unify log handling across NFs
- Standardize naming patterns

#### Technical Optimizations

- Performance: optimize deploy times, reduce resource usage
- Monitoring: health endpoints, metrics collection, dashboards
- Security: hardening, secrets management, network policies

### Automated OVS Garbage Collection

The testbed includes continuous OVS GC via the `ds-net-setup` DaemonSet:

- **Location**: `ansible/phases/04-overlay-network/roles/ovs_network_setup/tasks/main.yml`
- **Function**: Removes stale OVS ports and interfaces every 20s
- **Coverage**: All bridges (`br-n1`, `br-n2`, `br-n3`, `br-n4`, `br-n6e`, `br-n6c`)
- **Benefits**: Automatic cleanup after force deletions, kubelet restarts, or crashes

## 7. Development Notes

### Key Configuration Files

- **Inventory**: `ansible/inventory.ini` (node IPs and SSH keys)
- **Variables**: `ansible/group_vars/all.yml` (static IPs, versions, NAD configs)
- **Main Playbook**: `ansible/phases/00-main-playbook.yml` (orchestrates all phases)
- **Vagrant**: `Vagrantfile` (VM specs, provisioning)

### Static IP Assignments

Defined in `ansible/group_vars/all.yml`:

- AMF N1: `10.201.0.100`
- AMF N2: `10.202.0.100`
- UPF-edge N3: `10.203.0.100`
- UPF-cloud N3: `10.203.0.101`
- SMF N4: `10.204.0.100`
- UPF-edge N4: `10.204.0.101`
- UPF-cloud N4: `10.204.0.102`

### VXLAN Key Mapping

- N1: 101, N2: 102, N3: 103, N4: 104, N6e: 106, N6c: 106
- Future: N11: 111, N9: 109, N15: 115, N16: 116

## 8. Development & Testing

### Adding New 5G Network Functions

To add a new 5G Network Function:

1. **Create configuration** in `ansible/phases/05-5g-core/configs/`
2. **Add initialization script** in `ansible/phases/05-5g-core/scripts/`
3. **Create deployment manifest** in `ansible/phases/05-5g-core/roles/5g_core_manifests/tasks/main.yml`
4. **Add static IP** to `ansible/group_vars/all.yml`
5. **Update validation** in phase 5 playbook

Example for a new NRF:

```yaml
# In group_vars/all.yml
nrf_n4_ip: 10.204.0.103

# In 5g_core_manifests/tasks/main.yml
- name: Deploy NRF
  copy:
    content: |
      apiVersion: apps/v1
      kind: Deployment
      metadata:
        name: nrf
        namespace: 5g
      spec:
        replicas: 1
        selector:
          matchLabels:
            app: nrf
        template:
          metadata:
            labels:
              app: nrf
            annotations:
              k8s.v1.cni.cncf.io/networks: |
                [{"name":"n4-net","interface":"n4","ips":["{{ nrf_n4_ip ~ '/24' }}"]}]
          spec:
            containers:
            - name: nrf
              image: jacobbista/comnetsemu-5gc:latest
              ports:
              - containerPort: 7777
              - containerPort: 8805
```

### Adding New Network Interfaces

To add a new 5G interface (e.g., N9):

1. **Add variables** to `ansible/group_vars/all.yml`:

```yaml
# N9 interface
nad_n9_namespace: 5g
nad_n9_name: n9-net
n9_interface_expected: n9
n9_interface_regex: ^(n9|net[0-9]+)$
```

2. **Create NAD** in `ansible/phases/04-overlay-network/roles/ovs_network_setup/tasks/main.yml`
3. **Add OVS bridge** configuration
4. **Update VXLAN tunnel** setup
5. **Add validation** commands

### Performance Testing

#### Network Performance

```bash
# Test VXLAN tunnel performance
sudo k3s kubectl -n 5g exec deploy/gnb -- iperf3 -c 10.203.0.100 -t 30

# Test with different packet sizes
sudo k3s kubectl -n 5g exec deploy/gnb -- ping -c 100 -s 1472 -I n3 10.203.0.100

# Test UDP throughput
sudo k3s kubectl -n 5g exec deploy/gnb -- iperf3 -u -c 10.203.0.100 -b 100M -t 30
```

#### 5G Core Performance

```bash
# Test PFCP message exchange
sudo k3s kubectl -n 5g exec deploy/smf -- bash -c 'for i in {1..100}; do nc -zuvw1 10.204.0.101 8805 && echo "Success $i" || echo "Failed $i"; sleep 0.1; done'

# Test NGAP message exchange
sudo k3s kubectl -n 5g exec deploy/gnb -- bash -c 'for i in {1..100}; do nc -zuvw1 10.202.0.100 38412 && echo "Success $i" || echo "Failed $i"; sleep 0.1; done'
```

### Monitoring & Observability

#### Basic Monitoring

```bash
# Monitor pod resource usage
sudo k3s kubectl top pods -n 5g

# Monitor node resource usage
sudo k3s kubectl top nodes

# Monitor network interfaces
watch -n 1 'sudo k3s kubectl -n 5g exec deploy/amf -- ip addr show | grep -E "n1|n2"'
```

#### Log Analysis

```bash
# Real-time log monitoring
sudo k3s kubectl -n 5g logs deploy/amf -c amf -f | grep -E "ERROR|WARN|PFCP|NGAP"

# Log aggregation
sudo k3s kubectl -n 5g logs deploy/smf -c smf --tail=1000 | grep -i pfcp | tail -20

# Error pattern analysis
sudo k3s kubectl -n 5g logs deploy/upf-edge -c upf --tail=1000 | grep -E "error|fail|timeout" | sort | uniq -c
```

## 9. Advanced Configuration

### Customizing Network Topology

#### Adding More Edge Nodes

1. **Update Vagrantfile**:

```ruby
nodes = {
  "master"  => { cpu: 4, mem: 4096, ip: "192.168.56.10", box: "ubuntu/jammy64" },
  "worker"  => { cpu: 8, mem: 8192, ip: "192.168.56.11", box: "ubuntu/jammy64" },
  "edge1"   => { cpu: 4, mem: 4096, ip: "192.168.56.12", box: "ubuntu/jammy64" },
  "edge2"   => { cpu: 4, mem: 4096, ip: "192.168.56.13", box: "ubuntu/jammy64" },
  "ansible" => { cpu: 2, mem: 1024, ip: "192.168.56.14", box: "ubuntu/jammy64" }
}
```

2. **Update inventory.ini**:

```ini
[edges]
edge1 ansible_host=192.168.56.12 ansible_ssh_private_key_file=/home/vagrant/.ssh/edge1_key
edge2 ansible_host=192.168.56.13 ansible_ssh_private_key_file=/home/vagrant/.ssh/edge2_key
```

3. **Update OVS DaemonSet** to handle multiple edge nodes

#### Customizing IP Ranges

To change IP ranges, update `ansible/group_vars/all.yml`:

```yaml
# Example: Change N3 range to 10.300.0.0/24
nad_n3_namespace: 5g
nad_n3_name: n3-net
n3_interface_expected: n3
n3_interface_regex: ^(n3|net[0-9]+)$

# Update static IPs
upf_edge_n3_ip: 10.300.0.100
upf_cloud_n3_ip: 10.300.0.101
```

### Security Considerations

#### Network Security

```bash
# Check firewall rules
sudo iptables -L -n -v

# Check OVS flow table for security rules
sudo ovs-ofctl dump-flows br-n3 | grep -E "drop|reject"

# Monitor network traffic
sudo tcpdump -i br-n3 -n port 8805
```

#### Pod Security

```bash
# Check pod security context
sudo k3s kubectl -n 5g get pod -l app=amf -o json | jq '.spec.securityContext'

# Check container capabilities
sudo k3s kubectl -n 5g get pod -l app=amf -o json | jq '.spec.containers[0].securityContext.capabilities'
```

### Backup & Recovery

#### Configuration Backup

```bash
# Backup Ansible configuration
tar -czf ansible-config-backup.tar.gz ansible/

# Backup Kubernetes manifests
sudo k3s kubectl get all -A -o yaml > k8s-manifests-backup.yaml

# Backup OVS configuration
sudo ovs-vsctl show > ovs-config-backup.txt
```

#### Recovery Procedures

```bash
# Restore from backup
tar -xzf ansible-config-backup.tar.gz

# Restore Kubernetes state
sudo k3s kubectl apply -f k8s-manifests-backup.yaml

# Restore OVS configuration
# (Manual process based on ovs-config-backup.txt)
```

---

This handbook supersedes scattered markdowns in the repo. See also runbooks in `docs/runbooks/` for focused procedures.
