# Runbooks Index

This directory contains focused diagnostic and troubleshooting procedures for the 5G K8s Testbed.

## Quick Navigation

### 5G Protocol Diagnostics

| Runbook                                      | Protocol | Interface | Purpose                 |
| -------------------------------------------- | -------- | --------- | ----------------------- |
| [`pfcp-diagnostics.md`](pfcp-diagnostics.md) | PFCP     | N4        | SMF ↔ UPF control plane |
| [`ngap-diagnostics.md`](ngap-diagnostics.md) | NGAP     | N2        | gNB ↔ AMF signaling     |
| [`gtpu-path.md`](gtpu-path.md)               | GTP-U    | N3        | gNB/UE ↔ UPF data plane |

### Infrastructure Diagnostics

| Runbook                                      | Component          | Purpose                             |
| -------------------------------------------- | ------------------ | ----------------------------------- |
| [`multus-nad-ipam.md`](multus-nad-ipam.md)   | Multus/Whereabouts | Multiple network interfaces per pod |
| [`ovs-vxlan-health.md`](ovs-vxlan-health.md) | OVS/VXLAN          | Overlay network infrastructure      |

> **kubectl**: All commands run from master (`vagrant ssh master`) using `sudo k3s kubectl`.

## Usage

Each runbook follows a consistent structure:

1. **Quick Health Check** - One-liner to verify status
2. **Step-by-Step Diagnostics** - Detailed troubleshooting procedure
3. **Common Issues & Solutions** - Frequent problems and fixes
4. **Advanced Diagnostics** - Deep troubleshooting and performance testing

## Quick Commands

### Check All 5G Interfaces

```bash
# PFCP (N4) - SMF ↔ UPF
sudo k3s kubectl -n 5g exec deploy/smf -- bash -lc 'ss -unap | grep 8805 && echo "SMF listening" || echo "SMF not listening"'

# NGAP (N2) - gNB ↔ AMF
sudo k3s kubectl -n 5g exec deploy/amf -- bash -lc 'ss -S -na | grep 38412 && echo "AMF SCTP listening" || echo "AMF SCTP not listening"'

# GTP-U (N3) - gNB/UE ↔ UPF
sudo k3s kubectl -n 5g exec deploy/upf-edge -- bash -lc 'ss -unap | grep 2152 && echo "UPF-edge GTP-U listening" || echo "UPF-edge GTP-U not listening"'
```

### Check Infrastructure

```bash
# Multus and NADs
sudo k3s kubectl -n kube-system get ds kube-multus-ds && echo "Multus OK" || echo "Multus FAIL"

# OVS and VXLAN
sudo k3s kubectl -n kube-system get ds | grep -E "ds-net-setup|kube-multus-ds" && echo "DaemonSets OK" || echo "DaemonSets FAIL"
```

## Troubleshooting Flow

1. **Start with Quick Health Check** - Identify which component is failing
2. **Follow Step-by-Step Diagnostics** - Systematic approach to isolate the issue
3. **Check Common Issues & Solutions** - Look for known problems and fixes
4. **Use Advanced Diagnostics** - Deep dive if needed

## Related Documentation

- **Main Handbook**: [`../operations/handbook.md`](../operations/handbook.md) - Complete system documentation
- **Root README**: [`../README.md`](../README.md) - Quick start and overview
- **Context**: [`../CONTEXT.md`](../CONTEXT.md) - High-level architecture overview

## Contributing

When adding new runbooks:

1. Follow the established structure
2. Include Quick Health Check one-liner
3. Provide step-by-step diagnostics
4. Document common issues and solutions
5. Add advanced diagnostics for complex scenarios
6. Update this index

## Examples

### Real-time Log Monitoring

```bash
# AMF logs with NGAP context
sudo k3s kubectl -n 5g logs deploy/amf -c amf -f | grep -i ngap

# SMF logs with PFCP context
sudo k3s kubectl -n 5g logs deploy/smf -c smf -f | grep -i pfcp

# UPF logs with GTP-U context
sudo k3s kubectl -n 5g logs deploy/upf-edge -c upf -f | grep -i gtpu
```

### Network Interface Verification

```bash
# Check all interfaces on AMF
sudo k3s kubectl -n 5g exec deploy/amf -- ip addr show

# Check specific interface
sudo k3s kubectl -n 5g exec deploy/amf -- ip -o -4 addr show dev n2

# Check network status annotation
sudo k3s kubectl -n 5g get pod -l app=amf -o json | jq -r '.items[0].metadata.annotations["k8s.v1.cni.cncf.io/network-status"]' | jq '.'
```

### Performance Testing

```bash
# Test VXLAN tunnel performance
sudo k3s kubectl -n 5g exec deploy/gnb -- iperf3 -c 10.203.0.102 -t 30

# Test PFCP message exchange
sudo k3s kubectl -n 5g exec deploy/smf -- bash -c 'for i in {1..10}; do nc -zuvw1 10.204.0.101 8805 && echo "Success $i" || echo "Failed $i"; sleep 1; done'
```
