# PFCP (N4) Diagnostics: SMF ↔ UPF

**PFCP (Packet Forwarding Control Protocol)** is the control plane protocol between SMF and UPF on the N4 interface. This runbook provides comprehensive diagnostics for PFCP connectivity issues.

## Quick Health Check

```bash
# One-liner: Check if PFCP is working
sudo k3s kubectl -n 5g exec deploy/smf -- bash -lc 'ss -unap | grep 8805 && echo "SMF listening" || echo "SMF not listening"; nc -zuvw1 10.204.0.101 8805 && echo "UPF-edge reachable" || echo "UPF-edge unreachable"'
```

## Step-by-Step Diagnostics

### 1. Check SMF PFCP Server

```bash
# Check SMF is listening on PFCP port 8805
sudo k3s kubectl -n 5g exec deploy/smf -- bash -lc 'ss -unap | grep 8805'

# Expected output: udp 0 0 0.0.0.0:8805 0.0.0.0:* users:(("open5gs-smfd",pid=123,fd=5))

# Check SMF process
sudo k3s kubectl -n 5g exec deploy/smf -- ps aux | grep smf

# Check SMF configuration
sudo k3s kubectl -n 5g exec deploy/smf -- cat /etc/open5gs/smf.yaml | grep -A5 pfcp
```

### 2. Check UPF PFCP Clients

```bash
# Check UPF-edge PFCP client
sudo k3s kubectl -n 5g exec deploy/upf-edge -- bash -lc 'ss -unap | grep 8805'

# Check UPF-cloud PFCP client
sudo k3s kubectl -n 5g exec deploy/upf-cloud -- bash -lc 'ss -unap | grep 8805'

# Check UPF processes
sudo k3s kubectl -n 5g exec deploy/upf-edge -- ps aux | grep upf
sudo k3s kubectl -n 5g exec deploy/upf-cloud -- ps aux | grep upf
```

### 3. Network Interface Verification

```bash
# Check SMF N4 interface
sudo k3s kubectl -n 5g exec deploy/smf -- ip -o -4 addr show dev n4
# Expected: 10.204.0.100/24

# Check UPF-edge N4 interface
sudo k3s kubectl -n 5g exec deploy/upf-edge -- ip -o -4 addr show dev n4
# Expected: 10.204.0.101/24

# Check UPF-cloud N4 interface
sudo k3s kubectl -n 5g exec deploy/upf-cloud -- ip -o -4 addr show dev n4
# Expected: 10.204.0.102/24

# Check routing table
sudo k3s kubectl -n 5g exec deploy/smf -- ip route show dev n4
sudo k3s kubectl -n 5g exec deploy/upf-edge -- ip route show dev n4
```

### 4. Connectivity Tests

```bash
# Test SMF → UPF-edge connectivity
sudo k3s kubectl -n 5g exec deploy/smf -- ping -c 3 -I n4 10.204.0.101

# Test SMF → UPF-cloud connectivity
sudo k3s kubectl -n 5g exec deploy/smf -- ping -c 3 -I n4 10.204.0.102

# Test PFCP port connectivity
sudo k3s kubectl -n 5g exec deploy/smf -- nc -zuvw1 10.204.0.101 8805
sudo k3s kubectl -n 5g exec deploy/smf -- nc -zuvw1 10.204.0.102 8805

# Test with telnet (if available)
sudo k3s kubectl -n 5g exec deploy/smf -- telnet 10.204.0.101 8805
```

### 5. Multus Network Status

```bash
# Check NetworkAttachmentDefinition exists
sudo k3s kubectl -n 5g get net-attach-def n4-net

# Check SMF pod network status
sudo k3s kubectl -n 5g get pod -l app=smf -o json | jq -r '.items[0].metadata.annotations["k8s.v1.cni.cncf.io/network-status"]' | jq '.'

# Check UPF-edge pod network status
sudo k3s kubectl -n 5g get pod -l app=upf-edge -o json | jq -r '.items[0].metadata.annotations["k8s.v1.cni.cncf.io/network-status"]' | jq '.'

# Check UPF-cloud pod network status
sudo k3s kubectl -n 5g get pod -l app=upf-cloud -o json | jq -r '.items[0].metadata.annotations["k8s.v1.cni.cncf.io/network-status"]' | jq '.'
```

### 6. OVS Bridge Verification

```bash
# Check N4 bridge exists
sudo ovs-vsctl show | grep -A5 br-n4

# Check VXLAN tunnel for N4
sudo ovs-vsctl list interface | grep -A10 vxlan-n4

# Check bridge ports
sudo ovs-vsctl list-ports br-n4

# Check VXLAN tunnel status
ip -d link show | grep -A2 vxlan-n4
```

## Log Analysis

### SMF Logs

```bash
# Real-time SMF logs
sudo k3s kubectl -n 5g logs deploy/smf -c smf -f

# Recent SMF logs with PFCP context
sudo k3s kubectl -n 5g logs deploy/smf -c smf --tail=500 | grep -i pfcp

# SMF logs with timestamps
sudo k3s kubectl -n 5g logs deploy/smf -c smf --tail=200 --timestamps

# Check for PFCP errors
sudo k3s kubectl -n 5g logs deploy/smf -c smf --tail=1000 | grep -i "pfcp\|error\|fail"
```

### UPF Logs

```bash
# UPF-edge logs
sudo k3s kubectl -n 5g logs deploy/upf-edge -c upf -f

# UPF-cloud logs
sudo k3s kubectl -n 5g logs deploy/upf-cloud -c upf -f

# UPF logs with PFCP context
sudo k3s kubectl -n 5g logs deploy/upf-edge -c upf --tail=500 | grep -i pfcp
sudo k3s kubectl -n 5g logs deploy/upf-cloud -c upf --tail=500 | grep -i pfcp

# Check for UPF errors
sudo k3s kubectl -n 5g logs deploy/upf-edge -c upf --tail=1000 | grep -i "error\|fail\|pfcp"
```

## Common Issues & Solutions

### 1. SMF Not Listening on PFCP Port

**Symptoms:**

- `ss -unap | grep 8805` returns empty
- SMF logs show "bind failed" or "address already in use"

**Diagnosis:**

```bash
# Check if port is already in use
sudo k3s kubectl -n 5g exec deploy/smf -- netstat -tulpn | grep 8805

# Check SMF configuration
sudo k3s kubectl -n 5g exec deploy/smf -- cat /etc/open5gs/smf.yaml | grep -A10 pfcp
```

**Solution:**

- Restart SMF deployment: `kubectl -n 5g rollout restart deployment/smf`
- Check for port conflicts
- Verify SMF configuration

### 2. UPF Cannot Reach SMF

**Symptoms:**

- `nc -zuvw1 10.204.0.100 8805` fails
- UPF logs show "connection refused" or "timeout"

**Diagnosis:**

```bash
# Check UPF N4 interface
sudo k3s kubectl -n 5g exec deploy/upf-edge -- ip addr show dev n4

# Check routing
sudo k3s kubectl -n 5g exec deploy/upf-edge -- ip route show dev n4

# Check VXLAN tunnel
sudo ovs-vsctl show | grep -A5 br-n4
```

**Solution:**

- Verify N4 NetworkAttachmentDefinition
- Check OVS bridge configuration
- Restart UPF deployment

### 3. Multus Interface Missing

**Symptoms:**

- Pod only has default interface
- No N4 interface in `ip addr show`

**Diagnosis:**

```bash
# Check pod annotations
sudo k3s kubectl -n 5g get pod -l app=smf -o json | jq '.items[0].metadata.annotations'

# Check Multus DaemonSet
sudo k3s kubectl -n kube-system get ds kube-multus-ds

# Check Multus logs
sudo k3s kubectl -n kube-system logs -l app=multus --tail=100
```

**Solution:**

- Ensure Multus DaemonSet is running
- Check NetworkAttachmentDefinition exists
- Verify pod has correct annotation

### 4. VXLAN Tunnel Issues

**Symptoms:**

- No connectivity between worker↔edge
- VXLAN interfaces not created

**Diagnosis:**

```bash
# Check OVS DaemonSet
sudo k3s kubectl -n kube-system get ds ds-net-setup-worker
sudo k3s kubectl -n kube-system get ds ds-net-setup-edge

# Check OVS DaemonSet logs
sudo k3s kubectl -n kube-system logs -l app=ds-net-setup-worker --tail=200
sudo k3s kubectl -n kube-system logs -l app=ds-net-setup-edge --tail=200

# Check VXLAN interfaces
ip -d link show | grep vxlan
```

**Solution:**

- Restart OVS DaemonSet
- Check worker↔edge connectivity
- Verify VXLAN configuration

## Advanced Diagnostics

### Packet Capture

```bash
# Capture PFCP traffic on SMF
sudo k3s kubectl -n 5g exec deploy/smf -- tcpdump -i n4 -n port 8805

# Capture PFCP traffic on UPF-edge
sudo k3s kubectl -n 5g exec deploy/upf-edge -- tcpdump -i n4 -n port 8805

# Capture on host (if needed)
sudo tcpdump -i br-n4 -n port 8805
```

### Performance Testing

```bash
# Test PFCP message exchange
sudo k3s kubectl -n 5g exec deploy/smf -- bash -c 'for i in {1..10}; do nc -zuvw1 10.204.0.101 8805 && echo "Success $i" || echo "Failed $i"; sleep 1; done'

# Test with different packet sizes
sudo k3s kubectl -n 5g exec deploy/smf -- ping -c 10 -s 1472 -I n4 10.204.0.101
```

### Configuration Validation

```bash
# Compare with expected configuration
sudo k3s kubectl -n 5g exec deploy/smf -- cat /etc/open5gs/smf.yaml | grep -A20 pfcp

# Check static IP assignments
sudo k3s kubectl -n 5g exec deploy/smf -- ip addr show dev n4 | grep 10.204.0.100
sudo k3s kubectl -n 5g exec deploy/upf-edge -- ip addr show dev n4 | grep 10.204.0.101
sudo k3s kubectl -n 5g exec deploy/upf-cloud -- ip addr show dev n4 | grep 10.204.0.102
```
