# OVS/VXLAN Health Diagnostics

**Open vSwitch (OVS)** provides the overlay network infrastructure with **VXLAN tunnels** connecting worker and edge nodes. This runbook provides comprehensive diagnostics for OVS and VXLAN issues.

## Quick Health Check

```bash
# One-liner: Check OVS and VXLAN status
sudo k3s kubectl -n kube-system get ds | grep -E "ds-net-setup|kube-multus-ds" && echo "DaemonSets OK" || echo "DaemonSets FAIL"; sudo ovs-vsctl show | grep -c "Bridge br-n" && echo "Bridges found"
```

## Step-by-Step Diagnostics

### 1. Kubernetes DaemonSet Status

```bash
# Check OVS DaemonSets
sudo k3s kubectl -n kube-system get ds | grep -E "ds-net-setup|kube-multus-ds"

# Expected output:
# ds-net-setup-edge    2/2     2            2           2d
# ds-net-setup-worker  2/2     2            2           2d
# kube-multus-ds       2/2     2            2           2d

# Check DaemonSet details
sudo k3s kubectl -n kube-system describe ds ds-net-setup-worker
sudo k3s kubectl -n kube-system describe ds ds-net-setup-edge

# Check DaemonSet pods
sudo k3s kubectl -n kube-system get pods -l app=ds-net-setup-worker
sudo k3s kubectl -n kube-system get pods -l app=ds-net-setup-edge
```

### 2. OVS DaemonSet Logs

```bash
# Check OVS DaemonSet logs
sudo k3s kubectl -n kube-system logs -l app=ds-net-setup-worker --tail=200
sudo k3s kubectl -n kube-system logs -l app=ds-net-setup-edge --tail=200

# Check OVS DaemonSet logs with timestamps
sudo k3s kubectl -n kube-system logs -l app=ds-net-setup-worker --tail=200 --timestamps
sudo k3s kubectl -n kube-system logs -l app=ds-net-setup-edge --tail=200 --timestamps

# Follow OVS DaemonSet logs in real-time
sudo k3s kubectl -n kube-system logs -l app=ds-net-setup-worker -f
sudo k3s kubectl -n kube-system logs -l app=ds-net-setup-edge -f
```

### 3. OVS Bridge Configuration

```bash
# Check OVS configuration
sudo ovs-vsctl show

# Expected output should show:
# - Bridge br-n1, br-n2, br-n3, br-n4, br-n6e, br-n6c
# - VXLAN interfaces (vxlan-n1, vxlan-n2, etc.)
# - Port mappings

# List all bridges
sudo ovs-vsctl list-br

# Expected: br-n1, br-n2, br-n3, br-n4, br-n6e, br-n6c

# Check specific bridge details
sudo ovs-vsctl show br-n3
sudo ovs-vsctl show br-n4
```

### 4. VXLAN Tunnel Status

```bash
# Check VXLAN interfaces
ip -d link show | grep -A2 vxlan-

# Expected output should show:
# - vxlan-n1, vxlan-n2, vxlan-n3, vxlan-n4, vxlan-n6
# - VXLAN configuration details

# Check VXLAN tunnel configuration
sudo ovs-vsctl list interface | grep -A10 vxlan

# Check VXLAN tunnel status
ip -d link show vxlan-n3
ip -d link show vxlan-n4
```

### 5. Bridge Port Configuration

```bash
# List ports for each bridge
sudo ovs-vsctl list-ports br-n1
sudo ovs-vsctl list-ports br-n2
sudo ovs-vsctl list-ports br-n3
sudo ovs-vsctl list-ports br-n4
sudo ovs-vsctl list-ports br-n6e
sudo ovs-vsctl list-ports br-n6c

# Check port details
sudo ovs-vsctl list port vxlan-n3
sudo ovs-vsctl list port vxlan-n4

# Check interface details
sudo ovs-vsctl list interface vxlan-n3
sudo ovs-vsctl list interface vxlan-n4
```

### 6. VXLAN Tunnel Connectivity

```bash
# Check VXLAN tunnel endpoints
sudo ovs-vsctl list interface vxlan-n3 | grep -E "remote_ip|local_ip"

# Check VXLAN tunnel status
sudo ovs-vsctl list interface vxlan-n3 | grep -E "status|admin_state"

# Check VXLAN tunnel statistics
sudo ovs-vsctl list interface vxlan-n3 | grep -E "statistics"

# Test VXLAN tunnel connectivity
ping -c 3 10.203.0.100  # UPF-edge N3 IP
ping -c 3 10.203.0.101  # UPF-cloud N3 IP
```

### 7. OVS Flow Table

```bash
# Check OVS flow table
sudo ovs-ofctl dump-flows br-n3
sudo ovs-ofctl dump-flows br-n4

# Check OVS flow table with statistics
sudo ovs-ofctl dump-flows br-n3 --statistics

# Check OVS flow table with counters
sudo ovs-ofctl dump-flows br-n3 --counters
```

## Common Issues & Solutions

### 1. OVS DaemonSet Not Running

**Symptoms:**

- `kubectl -n kube-system get ds ds-net-setup-worker` shows 0/2 Ready
- No OVS bridges created

**Diagnosis:**

```bash
# Check DaemonSet status
sudo k3s kubectl -n kube-system get ds ds-net-setup-worker
sudo k3s kubectl -n kube-system get ds ds-net-setup-edge

# Check pod logs
sudo k3s kubectl -n kube-system logs -l app=ds-net-setup-worker --tail=100
sudo k3s kubectl -n kube-system logs -l app=ds-net-setup-edge --tail=100

# Check node resources
sudo k3s kubectl describe node worker
sudo k3s kubectl describe node edge
```

**Solution:**

- Check node resources and taints
- Restart DaemonSet: `kubectl -n kube-system rollout restart ds ds-net-setup-worker`
- Check OVS installation on nodes

### 2. OVS Bridges Missing

**Symptoms:**

- `sudo ovs-vsctl list-br` shows missing bridges
- No br-n1, br-n2, br-n3, etc.

**Diagnosis:**

```bash
# Check OVS bridges
sudo ovs-vsctl list-br

# Check OVS DaemonSet logs
sudo k3s kubectl -n kube-system logs -l app=ds-net-setup-worker --tail=200

# Check OVS service status
sudo systemctl status openvswitch-switch
```

**Solution:**

- Restart OVS service: `sudo systemctl restart openvswitch-switch`
- Restart OVS DaemonSet
- Check OVS installation

### 3. VXLAN Tunnels Not Created

**Symptoms:**

- No VXLAN interfaces in `ip -d link show`
- No connectivity between worker↔edge

**Diagnosis:**

```bash
# Check VXLAN interfaces
ip -d link show | grep vxlan

# Check OVS interface list
sudo ovs-vsctl list interface | grep vxlan

# Check OVS DaemonSet logs
sudo k3s kubectl -n kube-system logs -l app=ds-net-setup-worker --tail=200
```

**Solution:**

- Check worker↔edge connectivity
- Verify VXLAN configuration
- Restart OVS DaemonSet

### 4. VXLAN Tunnel Connectivity Issues

**Symptoms:**

- VXLAN interfaces exist but no connectivity
- Ping fails between worker↔edge

**Diagnosis:**

```bash
# Check VXLAN tunnel endpoints
sudo ovs-vsctl list interface vxlan-n3 | grep -E "remote_ip|local_ip"

# Check VXLAN tunnel status
sudo ovs-vsctl list interface vxlan-n3 | grep -E "status|admin_state"

# Test connectivity
ping -c 3 10.203.0.100
ping -c 3 10.203.0.101
```

**Solution:**

- Verify VXLAN tunnel configuration
- Check firewall rules
- Restart VXLAN interfaces

## Advanced Diagnostics

### OVS Database Analysis

```bash
# Check OVS database
sudo ovs-vsctl list bridge
sudo ovs-vsctl list port
sudo ovs-vsctl list interface

# Check OVS database with details
sudo ovs-vsctl list bridge br-n3
sudo ovs-vsctl list port vxlan-n3
sudo ovs-vsctl list interface vxlan-n3
```

### VXLAN Tunnel Statistics

```bash
# Check VXLAN tunnel statistics
sudo ovs-vsctl list interface vxlan-n3 | grep -A20 statistics

# Check VXLAN tunnel counters
sudo ovs-ofctl dump-flows br-n3 --statistics

# Check VXLAN tunnel errors
sudo ovs-vsctl list interface vxlan-n3 | grep -E "error|drop"
```

### Network Performance Testing

```bash
# Test VXLAN tunnel performance
iperf3 -c 10.203.0.100 -t 10

# Test VXLAN tunnel with different packet sizes
ping -c 10 -s 1472 10.203.0.100

# Test VXLAN tunnel with UDP
iperf3 -u -c 10.203.0.100 -b 100M -t 10
```

### OVS Flow Table Analysis

```bash
# Check OVS flow table
sudo ovs-ofctl dump-flows br-n3

# Check OVS flow table with statistics
sudo ovs-ofctl dump-flows br-n3 --statistics

# Check OVS flow table with counters
sudo ovs-ofctl dump-flows br-n3 --counters

# Check OVS flow table for specific flows
sudo ovs-ofctl dump-flows br-n3 | grep vxlan
```

## Troubleshooting Commands

### Quick Fixes

```bash
# Restart OVS DaemonSet
sudo k3s kubectl -n kube-system rollout restart ds ds-net-setup-worker
sudo k3s kubectl -n kube-system rollout restart ds ds-net-setup-edge

# Restart OVS service
sudo systemctl restart openvswitch-switch

# Restart VXLAN interfaces
sudo ovs-vsctl del-port br-n3 vxlan-n3
sudo ovs-vsctl add-port br-n3 vxlan-n3 -- set interface vxlan-n3 type=vxlan options:key=3 options:remote_ip=192.168.56.12
```

### Configuration Validation

```bash
# Validate OVS configuration
sudo ovs-vsctl show

# Validate VXLAN tunnel configuration
sudo ovs-vsctl list interface vxlan-n3 | grep -E "remote_ip|local_ip|key"

# Validate bridge configuration
sudo ovs-vsctl list bridge br-n3
sudo ovs-vsctl list port vxlan-n3
```

### Log Analysis

```bash
# OVS DaemonSet logs with timestamps
sudo k3s kubectl -n kube-system logs -l app=ds-net-setup-worker --tail=200 --timestamps
sudo k3s kubectl -n kube-system logs -l app=ds-net-setup-edge --tail=200 --timestamps

# OVS service logs
sudo journalctl -u openvswitch-switch --tail=100

# OVS daemon logs
sudo journalctl -u ovs-vswitchd --tail=100
sudo journalctl -u ovsdb-server --tail=100
```

### Health Monitoring

```bash
# Monitor OVS bridges
watch -n 1 'sudo ovs-vsctl list-br'

# Monitor VXLAN interfaces
watch -n 1 'ip -d link show | grep vxlan'

# Monitor OVS flow table
watch -n 1 'sudo ovs-ofctl dump-flows br-n3 --statistics'
```

## OVS Garbage Collection

The testbed includes automated OVS garbage collection to clean up stale ports:

```bash
# Check OVS GC DaemonSet
sudo k3s kubectl -n kube-system get ds ds-ovs-gc

# Check OVS GC logs
sudo k3s kubectl -n kube-system logs -l app=ds-ovs-gc --tail=100

# Check OVS GC configuration
sudo k3s kubectl -n kube-system get configmap ovs-scripts -o yaml
```

## Performance Tuning

### VXLAN Tunnel Optimization

```bash
# Check VXLAN tunnel MTU
ip link show vxlan-n3 | grep mtu

# Set VXLAN tunnel MTU
sudo ip link set vxlan-n3 mtu 1450

# Check VXLAN tunnel offload
sudo ethtool -k vxlan-n3 | grep -E "tx-checksumming|tx-checksum-ipv4"
```

### OVS Flow Table Optimization

```bash
# Check OVS flow table size
sudo ovs-ofctl dump-flows br-n3 | wc -l

# Clear OVS flow table
sudo ovs-ofctl del-flows br-n3

# Add specific flows
sudo ovs-ofctl add-flow br-n3 "priority=100,actions=normal"
```
