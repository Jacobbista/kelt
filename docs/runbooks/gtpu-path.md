# GTP-U (N3) Path — gNB/UE ↔ UPF

**GTP-U (GPRS Tunnelling Protocol User Plane)** is the data plane protocol between gNB/UE and UPF on the N3 interface. This runbook provides comprehensive diagnostics for GTP-U connectivity issues.

## Quick Health Check

```bash
# One-liner: Check if GTP-U is working
sudo k3s kubectl -n 5g exec deploy/upf-edge -- bash -lc 'ss -unap | grep 2152 && echo "UPF-edge GTP-U listening" || echo "UPF-edge GTP-U not listening"'
sudo k3s kubectl -n 5g exec deploy/upf-cloud -- bash -lc 'ss -unap | grep 2152 && echo "UPF-cloud GTP-U listening" || echo "UPF-cloud GTP-U not listening"'
```

## Step-by-Step Diagnostics

### 1. Check UPF GTP-U Servers

```bash
# Check UPF-edge is listening on GTP-U port 2152
sudo k3s kubectl -n 5g exec deploy/upf-edge -- bash -lc 'ss -unap | grep 2152'

# Expected output: udp 0 0 0.0.0.0:2152 0.0.0.0:* users:(("open5gs-upfd",pid=123,fd=5))

# Check UPF-cloud is listening on GTP-U port 2152
sudo k3s kubectl -n 5g exec deploy/upf-cloud -- bash -lc 'ss -unap | grep 2152'

# Check UPF processes
sudo k3s kubectl -n 5g exec deploy/upf-edge -- ps aux | grep upf
sudo k3s kubectl -n 5g exec deploy/upf-cloud -- ps aux | grep upf
```

### 2. Check gNB/UE GTP-U Clients

```bash
# Check gNB process
sudo k3s kubectl -n 5g exec deploy/gnb -- ps aux | grep gnb

# Check UE process
sudo k3s kubectl -n 5g exec deploy/ue -- ps aux | grep ue

# Check gNB configuration
sudo k3s kubectl -n 5g exec deploy/gnb -- cat /etc/ueransim/gnb.yaml | grep -A5 upf

# Check UE configuration
sudo k3s kubectl -n 5g exec deploy/ue -- cat /etc/ueransim/ue.yaml | grep -A5 upf
```

### 3. Network Interface Verification

```bash
# Check UPF-edge N3 interface
sudo k3s kubectl -n 5g exec deploy/upf-edge -- ip -o -4 addr show dev n3
# Expected: 10.203.0.102/24

# Check UPF-cloud N3 interface
sudo k3s kubectl -n 5g exec deploy/upf-cloud -- ip -o -4 addr show dev n3
# Expected: 10.203.0.101/24

# Check gNB N3 interface
sudo k3s kubectl -n 5g exec deploy/gnb -- ip -o -4 addr show dev n3
# Expected: Dynamic IP from Whereabouts IPAM

# Check UE N3 interface
sudo k3s kubectl -n 5g exec deploy/ue -- ip -o -4 addr show dev n3
# Expected: Dynamic IP from Whereabouts IPAM

# Check routing table
sudo k3s kubectl -n 5g exec deploy/upf-edge -- ip route show dev n3
sudo k3s kubectl -n 5g exec deploy/gnb -- ip route show dev n3
```

### 3.1 Overlay Gateway Ownership (Host-Side)

For stable N3 forwarding, the worker host must own the N3 gateway IP declared by IPAM.

```bash
# Worker host: N3 bridge must own 10.203.0.1/24
vagrant ssh worker -c 'ip -o -4 addr show br-n3'
# Expected to include: 10.203.0.1/24

# Optional: validate N2/N4 gateways as well
vagrant ssh worker -c 'ip -o -4 addr show br-n2 br-n4'
# Expected to include: 10.202.0.1/24 and 10.204.0.1/24

# From UPF cloud pod, the N3 gateway must be reachable
vagrant ssh master -c 'sudo k3s kubectl -n 5g exec deploy/upf-cloud -c upf-cloud -- ping -c 3 -I n3 10.203.0.1'
```

If `10.203.0.1` is missing on `br-n3`, PDU setup can fail before user-plane traffic starts.

### 4. Connectivity Tests

```bash
# Test gNB → UPF-edge connectivity
sudo k3s kubectl -n 5g exec deploy/gnb -- ping -c 3 -I n3 10.203.0.102

# Test gNB → UPF-cloud connectivity
sudo k3s kubectl -n 5g exec deploy/gnb -- ping -c 3 -I n3 10.203.0.101

# Test UE → UPF-edge connectivity
sudo k3s kubectl -n 5g exec deploy/ue -- ping -c 3 -I n3 10.203.0.102

# Test GTP-U port connectivity
sudo k3s kubectl -n 5g exec deploy/gnb -- nc -zuvw1 10.203.0.102 2152
sudo k3s kubectl -n 5g exec deploy/gnb -- nc -zuvw1 10.203.0.101 2152
```

### 5. Multus Network Status

```bash
# Check NetworkAttachmentDefinition exists
sudo k3s kubectl -n 5g get net-attach-def n3-net

# Check UPF-edge pod network status
sudo k3s kubectl -n 5g get pod -l app=upf-edge -o json | jq -r '.items[0].metadata.annotations["k8s.v1.cni.cncf.io/network-status"]' | jq '.'

# Check UPF-cloud pod network status
sudo k3s kubectl -n 5g get pod -l app=upf-cloud -o json | jq -r '.items[0].metadata.annotations["k8s.v1.cni.cncf.io/network-status"]' | jq '.'

# Check gNB pod network status
sudo k3s kubectl -n 5g get pod -l app=gnb -o json | jq -r '.items[0].metadata.annotations["k8s.v1.cni.cncf.io/network-status"]' | jq '.'

# Check UE pod network status
sudo k3s kubectl -n 5g get pod -l app=ue -o json | jq -r '.items[0].metadata.annotations["k8s.v1.cni.cncf.io/network-status"]' | jq '.'
```

### 6. OVS Bridge Verification

```bash
# Check N3 bridge exists
sudo ovs-vsctl show | grep -A5 br-n3

# Check VXLAN tunnel for N3
sudo ovs-vsctl list interface | grep -A10 vxlan-n3

# Check bridge ports
sudo ovs-vsctl list-ports br-n3

# Check VXLAN tunnel status
ip -d link show | grep -A2 vxlan-n3
```

## Log Analysis

### UPF Logs

```bash
# Real-time UPF-edge logs
sudo k3s kubectl -n 5g logs deploy/upf-edge -c upf -f

# Real-time UPF-cloud logs
sudo k3s kubectl -n 5g logs deploy/upf-cloud -c upf -f

# UPF logs with GTP-U context
sudo k3s kubectl -n 5g logs deploy/upf-edge -c upf --tail=500 | grep -i gtpu
sudo k3s kubectl -n 5g logs deploy/upf-cloud -c upf --tail=500 | grep -i gtpu

# Check for UPF errors
sudo k3s kubectl -n 5g logs deploy/upf-edge -c upf --tail=1000 | grep -i "error\|fail\|gtpu"
sudo k3s kubectl -n 5g logs deploy/upf-cloud -c upf --tail=1000 | grep -i "error\|fail\|gtpu"
```

### gNB/UE Logs

```bash
# Real-time gNB logs
sudo k3s kubectl -n 5g logs deploy/gnb -c gnb -f

# Real-time UE logs
sudo k3s kubectl -n 5g logs deploy/ue -c ue -f

# gNB logs with GTP-U context
sudo k3s kubectl -n 5g logs deploy/gnb -c gnb --tail=500 | grep -i gtpu

# UE logs with GTP-U context
sudo k3s kubectl -n 5g logs deploy/ue -c ue --tail=500 | grep -i gtpu

# Check for gNB/UE errors
sudo k3s kubectl -n 5g logs deploy/gnb -c gnb --tail=1000 | grep -i "error\|fail\|gtpu"
sudo k3s kubectl -n 5g logs deploy/ue -c ue --tail=1000 | grep -i "error\|fail\|gtpu"
```

## Common Issues & Solutions

### 1. UPF Not Listening on GTP-U Port

**Symptoms:**

- `ss -unap | grep 2152` returns empty
- UPF logs show "bind failed" or "address already in use"

**Diagnosis:**

```bash
# Check if port is already in use
sudo k3s kubectl -n 5g exec deploy/upf-edge -- netstat -tulpn | grep 2152

# Check UPF configuration
sudo k3s kubectl -n 5g exec deploy/upf-edge -- cat /etc/open5gs/upf-edge.yaml | grep -A10 gtpu
```

**Solution:**

- Restart UPF deployment: `kubectl -n 5g rollout restart deployment/upf-edge`
- Check for port conflicts
- Verify UPF configuration

### 2. gNB/UE Cannot Reach UPF

**Symptoms:**

- `nc -zuvw1 10.203.0.102 2152` fails
- gNB/UE logs show "connection refused" or "timeout"

**Diagnosis:**

```bash
# Check gNB N3 interface
sudo k3s kubectl -n 5g exec deploy/gnb -- ip addr show dev n3

# Check routing
sudo k3s kubectl -n 5g exec deploy/gnb -- ip route show dev n3

# Check VXLAN tunnel
sudo ovs-vsctl show | grep -A5 br-n3
```

**Solution:**

- Verify N3 NetworkAttachmentDefinition
- Check OVS bridge configuration
- Restart gNB/UE deployment

### 3. GTP-U Tunnel Issues

**Symptoms:**

- No data flow between gNB/UE and UPF
- GTP-U packets not reaching destination

**Diagnosis:**

```bash
# Check GTP-U tunnel configuration
sudo k3s kubectl -n 5g exec deploy/upf-edge -- ip tunnel show

# Check GTP-U statistics
sudo k3s kubectl -n 5g exec deploy/upf-edge -- cat /proc/net/dev | grep n3

# Check for GTP-U errors
sudo k3s kubectl -n 5g logs deploy/upf-edge -c upf --tail=1000 | grep -i "tunnel\|gtpu"
```

**Solution:**

- Verify GTP-U tunnel configuration
- Check network policies
- Restart UPF deployment

### 4. Multus Interface Missing

**Symptoms:**

- Pod only has default interface
- No N3 interface in `ip addr show`

**Diagnosis:**

```bash
# Check pod annotations
sudo k3s kubectl -n 5g get pod -l app=upf-edge -o json | jq '.items[0].metadata.annotations'

# Check Multus DaemonSet
sudo k3s kubectl -n kube-system get ds kube-multus-ds

# Check Multus logs
sudo k3s kubectl -n kube-system logs -l app=multus --tail=100
```

**Solution:**

- Ensure Multus DaemonSet is running
- Check NetworkAttachmentDefinition exists
- Verify pod has correct annotation

## Advanced Diagnostics

### Packet Capture

```bash
# Capture GTP-U traffic on UPF-edge
sudo k3s kubectl -n 5g exec deploy/upf-edge -- tcpdump -i n3 -n port 2152

# Capture GTP-U traffic on gNB
sudo k3s kubectl -n 5g exec deploy/gnb -- tcpdump -i n3 -n port 2152

# Capture on host (if needed)
sudo tcpdump -i br-n3 -n port 2152
```

### Performance Testing

```bash
# Test GTP-U data flow
sudo k3s kubectl -n 5g exec deploy/gnb -- iperf3 -c 10.203.0.102 -p 2152 -t 10

# Test with different packet sizes
sudo k3s kubectl -n 5g exec deploy/gnb -- ping -c 10 -s 1472 -I n3 10.203.0.102

# Test UDP throughput
sudo k3s kubectl -n 5g exec deploy/gnb -- iperf3 -u -c 10.203.0.102 -p 2152 -b 100M -t 10
```

### Configuration Validation

```bash
# Compare with expected configuration
sudo k3s kubectl -n 5g exec deploy/upf-edge -- cat /etc/open5gs/upf-edge.yaml | grep -A20 gtpu

# Check static IP assignments
sudo k3s kubectl -n 5g exec deploy/upf-edge -- ip addr show dev n3 | grep 10.203.0.102
sudo k3s kubectl -n 5g exec deploy/upf-cloud -- ip addr show dev n3 | grep 10.203.0.101

# Check gNB configuration
sudo k3s kubectl -n 5g exec deploy/gnb -- cat /etc/ueransim/gnb.yaml | grep -A10 upf
```

## GTP-U Message Flow

### PDU Session Establishment

```bash
# Check for PDU Session Establishment in UPF logs
sudo k3s kubectl -n 5g logs deploy/upf-edge -c upf --tail=1000 | grep -i "pdu.*session"

# Check for GTP-U messages in gNB logs
sudo k3s kubectl -n 5g logs deploy/gnb -c gnb --tail=1000 | grep -i "gtpu"
```

### Data Plane Activity

```bash
# Check for data plane activity in UPF logs
sudo k3s kubectl -n 5g logs deploy/upf-edge -c upf --tail=1000 | grep -i "data\|packet"

# Check for data plane activity in gNB logs
sudo k3s kubectl -n 5g logs deploy/gnb -c gnb --tail=1000 | grep -i "data\|packet"
```

### Tunnel Management

```bash
# Check for tunnel management in UPF logs
sudo k3s kubectl -n 5g logs deploy/upf-edge -c upf --tail=1000 | grep -i "tunnel\|teid"

# Check for tunnel management in gNB logs
sudo k3s kubectl -n 5g logs deploy/gnb -c gnb --tail=1000 | grep -i "tunnel\|teid"
```

## Optional Overlay Ping (when test pods enabled)

```bash
# Check if test pods are enabled
sudo k3s kubectl -n 5g get pods -l app=n3test -o wide

# If test pods exist, ping between worker↔edge on N3
sudo k3s kubectl -n 5g exec deploy/n3test-worker -- ping -c 3 -I n3 <edge-n3-ip>
sudo k3s kubectl -n 5g exec deploy/n3test-edge -- ping -c 3 -I n3 <worker-n3-ip>

# Check test pod network status
sudo k3s kubectl -n 5g get pod -l app=n3test -o json | jq -r '.items[0].metadata.annotations["k8s.v1.cni.cncf.io/network-status"]' | jq '.'
```
