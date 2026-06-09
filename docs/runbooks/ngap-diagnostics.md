# NGAP (N2) Diagnostics: gNB ↔ AMF

**NGAP (Next Generation Application Protocol)** is the control plane protocol between gNB and AMF on the N2 interface. This runbook provides comprehensive diagnostics for NGAP connectivity issues.

## Quick Health Check

```bash
# One-liner: Check if NGAP is working
sudo k3s kubectl -n 5g exec deploy/amf -- bash -lc 'ss -S -na | grep 38412 && echo "AMF SCTP listening" || echo "AMF SCTP not listening"'
```

## Step-by-Step Diagnostics

### 1. Check AMF NGAP Server

```bash
# Check AMF is listening on NGAP port 38412 (SCTP)
sudo k3s kubectl -n 5g exec deploy/amf -- bash -lc 'ss -S -na | grep 38412'

# Expected output: sctp 0 0 0.0.0.0:38412 0.0.0.0:* users:(("open5gs-amfd",pid=123,fd=5))

# Check AMF process
sudo k3s kubectl -n 5g exec deploy/amf -- ps aux | grep amf

# Check AMF configuration
sudo k3s kubectl -n 5g exec deploy/amf -- cat /etc/open5gs/amf.yaml | grep -A5 ngap
```

### 2. Check gNB NGAP Client

```bash
# Check gNB process
sudo k3s kubectl -n 5g exec deploy/gnb -- ps aux | grep gnb

# Check gNB configuration
sudo k3s kubectl -n 5g exec deploy/gnb -- cat /etc/ueransim/gnb.yaml | grep -A5 amf

# Check gNB logs for NGAP activity
sudo k3s kubectl -n 5g logs deploy/gnb -c gnb --tail=200 | grep -i ngap
```

### 3. Network Interface Verification

```bash
# Check AMF N2 interface
sudo k3s kubectl -n 5g exec deploy/amf -- ip -o -4 addr show dev n2
# Expected: 10.202.0.100/24

# Check gNB N2 interface
sudo k3s kubectl -n 5g exec deploy/gnb -- ip -o -4 addr show dev n2
# Expected: Dynamic IP from Whereabouts IPAM

# Check routing table
sudo k3s kubectl -n 5g exec deploy/amf -- ip route show dev n2
sudo k3s kubectl -n 5g exec deploy/gnb -- ip route show dev n2
```

### 4. Connectivity Tests

```bash
# Test gNB → AMF connectivity
sudo k3s kubectl -n 5g exec deploy/gnb -- ping -c 3 -I n2 10.202.0.100

# Test SCTP port connectivity
sudo k3s kubectl -n 5g exec deploy/gnb -- nc -zuvw1 10.202.0.100 38412

# Test with telnet (if available)
sudo k3s kubectl -n 5g exec deploy/gnb -- telnet 10.202.0.100 38412
```

### 5. Multus Network Status

```bash
# Check NetworkAttachmentDefinition exists
sudo k3s kubectl -n 5g get net-attach-def n2-net

# Check AMF pod network status
sudo k3s kubectl -n 5g get pod -l app=amf -o json | jq -r '.items[0].metadata.annotations["k8s.v1.cni.cncf.io/network-status"]' | jq '.'

# Check gNB pod network status
sudo k3s kubectl -n 5g get pod -l app=gnb -o json | jq -r '.items[0].metadata.annotations["k8s.v1.cni.cncf.io/network-status"]' | jq '.'
```

### 6. OVS Bridge Verification

```bash
# Check N2 bridge exists
sudo ovs-vsctl show | grep -A5 br-n2

# Check VXLAN tunnel for N2
sudo ovs-vsctl list interface | grep -A10 vxlan-n2

# Check bridge ports
sudo ovs-vsctl list-ports br-n2

# Check VXLAN tunnel status
ip -d link show | grep -A2 vxlan-n2
```

## Log Analysis

### AMF Logs

```bash
# Real-time AMF logs
sudo k3s kubectl -n 5g logs deploy/amf -c amf -f

# Recent AMF logs with NGAP context
sudo k3s kubectl -n 5g logs deploy/amf -c amf --tail=500 | grep -i ngap

# AMF logs with timestamps
sudo k3s kubectl -n 5g logs deploy/amf -c amf --tail=200 --timestamps

# Check for NGAP errors
sudo k3s kubectl -n 5g logs deploy/amf -c amf --tail=1000 | grep -i "ngap\|error\|fail"
```

### gNB Logs

```bash
# Real-time gNB logs
sudo k3s kubectl -n 5g logs deploy/gnb -c gnb -f

# gNB logs with NGAP context
sudo k3s kubectl -n 5g logs deploy/gnb -c gnb --tail=500 | grep -i ngap

# Check for gNB errors
sudo k3s kubectl -n 5g logs deploy/gnb -c gnb --tail=1000 | grep -i "error\|fail\|ngap"
```

## Common Issues & Solutions

### 1. AMF Not Listening on NGAP Port

**Symptoms:**

- `ss -S -na | grep 38412` returns empty
- AMF logs show "bind failed" or "address already in use"

**Diagnosis:**

```bash
# Check if port is already in use
sudo k3s kubectl -n 5g exec deploy/amf -- netstat -tulpn | grep 38412

# Check AMF configuration
sudo k3s kubectl -n 5g exec deploy/amf -- cat /etc/open5gs/amf.yaml | grep -A10 ngap
```

**Solution:**

- Restart AMF deployment: `kubectl -n 5g rollout restart deployment/amf`
- Check for port conflicts
- Verify AMF configuration

### 2. gNB Cannot Reach AMF

**Symptoms:**

- `nc -zuvw1 10.202.0.100 38412` fails
- gNB logs show "connection refused" or "timeout"

**Diagnosis:**

```bash
# Check gNB N2 interface
sudo k3s kubectl -n 5g exec deploy/gnb -- ip addr show dev n2

# Check routing
sudo k3s kubectl -n 5g exec deploy/gnb -- ip route show dev n2

# Check VXLAN tunnel
sudo ovs-vsctl show | grep -A5 br-n2
```

**Solution:**

- Verify N2 NetworkAttachmentDefinition
- Check OVS bridge configuration
- Restart gNB deployment

### 3. SCTP Protocol Issues

**Symptoms:**

- SCTP port not accessible
- "Protocol not supported" errors

**Diagnosis:**

```bash
# Check SCTP kernel module
sudo k3s kubectl -n 5g exec deploy/amf -- lsmod | grep sctp

# Check SCTP configuration
sudo k3s kubectl -n 5g exec deploy/amf -- cat /proc/net/sctp/assocs
```

**Solution:**

- Ensure SCTP kernel module is loaded
- Check firewall rules
- Verify SCTP support in containers

### 4. Multus Interface Missing

**Symptoms:**

- Pod only has default interface
- No N2 interface in `ip addr show`

**Diagnosis:**

```bash
# Check pod annotations
sudo k3s kubectl -n 5g get pod -l app=amf -o json | jq '.items[0].metadata.annotations'

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
# Capture NGAP traffic on AMF
sudo k3s kubectl -n 5g exec deploy/amf -- tcpdump -i n2 -n port 38412

# Capture NGAP traffic on gNB
sudo k3s kubectl -n 5g exec deploy/gnb -- tcpdump -i n2 -n port 38412

# Capture on host (if needed)
sudo tcpdump -i br-n2 -n port 38412
```

### Performance Testing

```bash
# Test NGAP message exchange
sudo k3s kubectl -n 5g exec deploy/gnb -- bash -c 'for i in {1..10}; do nc -zuvw1 10.202.0.100 38412 && echo "Success $i" || echo "Failed $i"; sleep 1; done'

# Test with different packet sizes
sudo k3s kubectl -n 5g exec deploy/gnb -- ping -c 10 -s 1472 -I n2 10.202.0.100
```

### Configuration Validation

```bash
# Compare with expected configuration
sudo k3s kubectl -n 5g exec deploy/amf -- cat /etc/open5gs/amf.yaml | grep -A20 ngap

# Check static IP assignments
sudo k3s kubectl -n 5g exec deploy/amf -- ip addr show dev n2 | grep 10.202.0.100

# Check gNB configuration
sudo k3s kubectl -n 5g exec deploy/gnb -- cat /etc/ueransim/gnb.yaml | grep -A10 amf
```

## NGAP Message Flow

### Initial UE Message (5G-AN TNL Establishment)

```bash
# Check for Initial UE Message in AMF logs
sudo k3s kubectl -n 5g logs deploy/amf -c amf --tail=1000 | grep -i "initial.*ue"

# Check for NGAP Setup Request in gNB logs
sudo k3s kubectl -n 5g logs deploy/gnb -c gnb --tail=1000 | grep -i "ngap.*setup"
```

### PDU Session Establishment

```bash
# Check for PDU Session Establishment in AMF logs
sudo k3s kubectl -n 5g logs deploy/amf -c amf --tail=1000 | grep -i "pdu.*session"

# Check for NGAP messages in gNB logs
sudo k3s kubectl -n 5g logs deploy/gnb -c gnb --tail=1000 | grep -i "ngap"
```

### Handover Procedures

```bash
# Check for handover messages in AMF logs
sudo k3s kubectl -n 5g logs deploy/amf -c amf --tail=1000 | grep -i "handover"

# Check for handover messages in gNB logs
sudo k3s kubectl -n 5g logs deploy/gnb -c gnb --tail=1000 | grep -i "handover"
```
