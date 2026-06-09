# Multus NAD & Whereabouts IPAM Diagnostics

**Multus CNI** enables multiple network interfaces per pod, while **Whereabouts IPAM** provides stable IP address management. This runbook provides comprehensive diagnostics for Multus and Whereabouts issues.

## Quick Health Check

```bash
# One-liner: Check Multus and NADs status
sudo k3s kubectl -n kube-system get ds kube-multus-ds && echo "Multus OK" || echo "Multus FAIL"; kubectl get net-attach-def -A | wc -l && echo "NADs found"
```

## Step-by-Step Diagnostics

### 1. Check Multus DaemonSet

```bash
# Check Multus DaemonSet status
sudo k3s kubectl -n kube-system get ds kube-multus-ds

# Expected output: READY 2/2 (one on worker, one on edge)

# Check Multus DaemonSet details
sudo k3s kubectl -n kube-system describe ds kube-multus-ds

# Check Multus pods
sudo k3s kubectl -n kube-system get pods -l app=multus

# Check Multus pod logs
sudo k3s kubectl -n kube-system logs -l app=multus --tail=100
```

### 2. Inspect NetworkAttachmentDefinitions

```bash
# List all NADs
sudo k3s kubectl get net-attach-def -A

# Check specific NADs for 5G interfaces
sudo k3s kubectl -n 5g get net-attach-def n1-net -o yaml
sudo k3s kubectl -n 5g get net-attach-def n2-net -o yaml
sudo k3s kubectl -n 5g get net-attach-def n3-net -o yaml
sudo k3s kubectl -n 5g get net-attach-def n4-net -o yaml

# Check MEC NADs
sudo k3s kubectl -n mec get net-attach-def n6-mec-net -o yaml
sudo k3s kubectl -n 5g get net-attach-def n6-cld-net -o yaml

# Check NAD configuration details
sudo k3s kubectl -n 5g get net-attach-def n3-net -o json | jq '.spec.config'
```

### 3. Pod Network Status Analysis

```bash
# Get pod network status for AMF
POD=$(kubectl -n 5g get pods -l app=amf -o jsonpath='{.items[0].metadata.name}')
sudo k3s kubectl -n 5g get pod $POD -o json | jq -r '.metadata.annotations["k8s.v1.cni.cncf.io/network-status"]' | jq '.'

# Get pod network status for SMF
POD=$(kubectl -n 5g get pods -l app=smf -o jsonpath='{.items[0].metadata.name}')
sudo k3s kubectl -n 5g get pod $POD -o json | jq -r '.metadata.annotations["k8s.v1.cni.cncf.io/network-status"]' | jq '.'

# Get pod network status for UPF-edge
POD=$(kubectl -n 5g get pods -l app=upf-edge -o jsonpath='{.items[0].metadata.name}')
sudo k3s kubectl -n 5g get pod $POD -o json | jq -r '.metadata.annotations["k8s.v1.cni.cncf.io/network-status"]' | jq '.'

# Get pod network status for gNB
POD=$(kubectl -n 5g get pods -l app=gnb -o jsonpath='{.items[0].metadata.name}')
sudo k3s kubectl -n 5g get pod $POD -o json | jq -r '.metadata.annotations["k8s.v1.cni.cncf.io/network-status"]' | jq '.'
```

### 4. Interface Verification

```bash
# Check AMF interfaces
sudo k3s kubectl -n 5g exec deploy/amf -- ip addr show
sudo k3s kubectl -n 5g exec deploy/amf -- ip -o -4 addr show dev n1
sudo k3s kubectl -n 5g exec deploy/amf -- ip -o -4 addr show dev n2

# Check SMF interfaces
sudo k3s kubectl -n 5g exec deploy/smf -- ip addr show
sudo k3s kubectl -n 5g exec deploy/smf -- ip -o -4 addr show dev n4

# Check UPF-cloud interfaces (active data-plane UPF; upf-edge is disabled by default)
sudo k3s kubectl -n 5g exec deploy/upf-cloud -- ip addr show
sudo k3s kubectl -n 5g exec deploy/upf-cloud -- ip -o -4 addr show dev n3
sudo k3s kubectl -n 5g exec deploy/upf-cloud -- ip -o -4 addr show dev n4
sudo k3s kubectl -n 5g exec deploy/upf-cloud -- ip -o -4 addr show dev n6
```

### 5. Whereabouts IPAM Configuration

```bash
# Check Whereabouts configuration on worker
ssh worker "ls -l /etc/cni/net.d/whereabouts.d/"
ssh worker "cat /etc/cni/net.d/whereabouts.d/whereabouts.conf"

# Check Whereabouts configuration on edge
ssh edge "ls -l /etc/cni/net.d/whereabouts.d/"
ssh edge "cat /etc/cni/net.d/whereabouts.d/whereabouts.conf"

# Check Whereabouts IP pools
sudo k3s kubectl get ipaddresspools -A
sudo k3s kubectl get overlappingrangeipreservations -A
```

### 6. CNI Configuration Files

```bash
# Check CNI configuration on worker
ssh worker "ls -l /etc/cni/net.d/"
ssh worker "cat /etc/cni/net.d/00-multus.conf"

# Check CNI configuration on edge
ssh edge "ls -l /etc/cni/net.d/"
ssh edge "cat /etc/cni/net.d/00-multus.conf"

# Check CNI binaries
ssh worker "ls -l /opt/cni/bin/"
ssh edge "ls -l /opt/cni/bin/"
```

## Common Issues & Solutions

### 1. Multus DaemonSet Not Running

**Symptoms:**

- `kubectl -n kube-system get ds kube-multus-ds` shows 0/2 Ready
- Pods not getting additional network interfaces

**Diagnosis:**

```bash
# Check Multus DaemonSet status
sudo k3s kubectl -n kube-system get ds kube-multus-ds

# Check Multus pod logs
sudo k3s kubectl -n kube-system logs -l app=multus --tail=100

# Check node resources
sudo k3s kubectl describe node worker
sudo k3s kubectl describe node edge
```

**Solution:**

- Check node resources and taints
- Restart Multus DaemonSet: `kubectl -n kube-system rollout restart ds kube-multus-ds`
- Check CNI configuration files

### 2. NetworkAttachmentDefinition Missing

**Symptoms:**

- `kubectl get net-attach-def -A` shows missing NADs
- Pods fail to get additional interfaces

**Diagnosis:**

```bash
# Check if NADs exist
sudo k3s kubectl get net-attach-def -A

# Check OVS DaemonSet logs
sudo k3s kubectl -n kube-system logs -l app=ds-net-setup-worker --tail=200
sudo k3s kubectl -n kube-system logs -l app=ds-net-setup-edge --tail=200
```

**Solution:**

- Restart OVS DaemonSet
- Check Ansible playbook execution
- Manually create missing NADs

### 3. Pod Not Getting Network Interface

**Symptoms:**

- Pod only has default interface
- No additional interfaces in `ip addr show`

**Diagnosis:**

```bash
# Check pod annotations
sudo k3s kubectl -n 5g get pod <pod-name> -o json | jq '.metadata.annotations'

# Check pod network status
sudo k3s kubectl -n 5g get pod <pod-name> -o json | jq -r '.metadata.annotations["k8s.v1.cni.cncf.io/network-status"]' | jq '.'

# Check Multus logs
sudo k3s kubectl -n kube-system logs -l app=multus --tail=100
```

**Solution:**

- Ensure pod has correct annotation
- Check NetworkAttachmentDefinition exists
- Verify Multus DaemonSet is running

### 4. Whereabouts IPAM Issues

**Symptoms:**

- Pods not getting IP addresses
- IP conflicts or allocation failures

**Diagnosis:**

```bash
# Check Whereabouts configuration
ssh worker "cat /etc/cni/net.d/whereabouts.d/whereabouts.conf"

# Check IP pools
sudo k3s kubectl get ipaddresspools -A

# Check overlapping reservations
sudo k3s kubectl get overlappingrangeipreservations -A

# Check Whereabouts logs
sudo k3s kubectl -n kube-system logs -l app=whereabouts --tail=100
```

**Solution:**

- Verify Whereabouts configuration
- Check IP pool ranges
- Clear overlapping reservations if needed

## Advanced Diagnostics

### Network Status Deep Dive

```bash
# Get detailed network status for all pods
for pod in $(kubectl -n 5g get pods -o jsonpath='{.items[*].metadata.name}'); do
  echo "=== $pod ==="
  sudo k3s kubectl -n 5g get pod $pod -o json | jq -r '.metadata.annotations["k8s.v1.cni.cncf.io/network-status"]' | jq '.'
  echo
done
```

### Interface Statistics

```bash
# Check interface statistics
sudo k3s kubectl -n 5g exec deploy/amf -- cat /proc/net/dev | grep -E "n1|n2"
sudo k3s kubectl -n 5g exec deploy/smf -- cat /proc/net/dev | grep n4
sudo k3s kubectl -n 5g exec deploy/upf-edge -- cat /proc/net/dev | grep -E "n3|n4|n6"
```

### Routing Table Analysis

```bash
# Check routing tables
sudo k3s kubectl -n 5g exec deploy/amf -- ip route show
sudo k3s kubectl -n 5g exec deploy/smf -- ip route show
sudo k3s kubectl -n 5g exec deploy/upf-edge -- ip route show
sudo k3s kubectl -n 5g exec deploy/upf-cloud -- ip route show
```

### CNI Plugin Verification

```bash
# Check CNI plugins are available
sudo k3s kubectl -n 5g exec deploy/amf -- ls -l /opt/cni/bin/

# Check CNI configuration
sudo k3s kubectl -n 5g exec deploy/amf -- cat /etc/cni/net.d/00-multus.conf
```

## Troubleshooting Commands

### Quick Fixes

```bash
# Restart Multus DaemonSet
sudo k3s kubectl -n kube-system rollout restart ds kube-multus-ds

# Restart OVS DaemonSet
sudo k3s kubectl -n kube-system rollout restart ds ds-net-setup-worker
sudo k3s kubectl -n kube-system rollout restart ds ds-net-setup-edge

# Restart specific pod
sudo k3s kubectl -n 5g rollout restart deployment/amf
sudo k3s kubectl -n 5g rollout restart deployment/smf
```

### Configuration Validation

```bash
# Validate NAD configuration
sudo k3s kubectl -n 5g get net-attach-def n3-net -o json | jq '.spec.config' | jq '.'

# Check static IP assignments
sudo k3s kubectl -n 5g exec deploy/amf -- ip addr show dev n1 | grep 10.201.0.100
sudo k3s kubectl -n 5g exec deploy/amf -- ip addr show dev n2 | grep 10.202.0.100
sudo k3s kubectl -n 5g exec deploy/smf -- ip addr show dev n4 | grep 10.204.0.100
```

### Log Analysis

```bash
# Multus logs with timestamps
sudo k3s kubectl -n kube-system logs -l app=multus --tail=200 --timestamps

# OVS DaemonSet logs
sudo k3s kubectl -n kube-system logs -l app=ds-net-setup-worker --tail=200 --timestamps
sudo k3s kubectl -n kube-system logs -l app=ds-net-setup-edge --tail=200 --timestamps

# Whereabouts logs
sudo k3s kubectl -n kube-system logs -l app=whereabouts --tail=200 --timestamps
```
