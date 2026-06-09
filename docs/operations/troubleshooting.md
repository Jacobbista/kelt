# Troubleshooting Guide

Common issues and their solutions.

## Time Sync (VM Clock Drift)

**Symptom**: `ping` shows `invalid tv_usec`, `time of day goes back`, `wrong data byte`; logs have odd timestamps; dashboard Time Sync popover shows "DRIFT DETECTED".

**Cause**: VM clock drift (common with VirtualBox suspend/resume). Chrony's gradual frequency steering is often insufficient in virtualized environments where VMs get paused, resumed, or snapshotted.

**Automatic correction** (deployed by Phase 1):
- **Chrony**: configured with `makestep 1 -1`: allows stepping the clock at any time if drift exceeds 1 second
- **VirtualBox Guest Additions**: time sync every 10 seconds (Vagrant trigger on `up`/`resume`/`reload`)
- **Systemd timer** (`chrony-force-sync.timer`): runs `chronyc makestep` every 5 minutes on all VMs. If drift exceeds the chrony threshold, the clock is stepped immediately; otherwise it's a no-op

**Manual correction**:
- **Dashboard**: open the Time Sync popover (click the clock in the sidebar). If drift is detected, a "Force Sync" button appears. This runs `chronyc makestep` on all VMs via SSH
- **CLI**: `vagrant ssh worker -c 'sudo chronyc -a makestep'`

**Diagnostics**:
```bash
# Check chrony sources and tracking
chronyc sources -v
chronyc tracking

# Check the force-sync timer is active
systemctl list-timers | grep chrony-force-sync

# Check last sync result
journalctl -u chrony-force-sync.service --no-pager -n 5
```

---

## Quick Diagnostics

```bash
# Cluster health
sudo k3s kubectl get nodes
sudo k3s kubectl get pods -A | grep -v Running

# 5G Core status
sudo k3s kubectl get pods -n 5g

# Recent events
sudo k3s kubectl get events -n 5g --sort-by='.lastTimestamp' | tail -20
```

---

## Deployment Issues

### Vagrant VM Won't Start

**Symptom**: `vagrant up` fails with VirtualBox errors

**Solutions**:
```bash
# Check VirtualBox status
VBoxManage list vms

# Remove stale VMs
vagrant destroy -f
rm -rf .vagrant/

# Restart VirtualBox
sudo systemctl restart vboxdrv
```

### Ansible Playbook Fails

**Symptom**: Phase fails with SSH or task errors

**Solutions**:
```bash
# Check SSH connectivity
vagrant ssh ansible
ssh worker 'echo OK'
ssh edge 'echo OK'

# Re-run with verbose
ansible-playbook phases/XX/playbook.yml -i inventory.ini -vvv
```

---

## Kubernetes Issues

### Node Not Ready

**Symptom**: `kubectl get nodes` shows NotReady

**Diagnosis**:
```bash
sudo k3s kubectl describe node <node-name>
```

**Solutions**:
```bash
# Check K3s service
vagrant ssh <node>
sudo journalctl -u k3s -f          # master
sudo journalctl -u k3s-agent -f    # worker/edge

# Restart K3s
sudo systemctl restart k3s         # or k3s-agent
```

### Pod Stuck in Pending

**Symptom**: Pods remain in Pending state

**Diagnosis**:
```bash
sudo k3s kubectl describe pod <pod-name> -n <namespace>
```

**Common causes**:
- Insufficient resources: Check node capacity
- Node selector: Verify labels match
- PVC issues: Check storage provisioner

---

## Network Issues

### Multus NAD Not Working

**Symptom**: Pod can't get secondary interface

**Diagnosis**:
```bash
# Check NAD exists
sudo k3s kubectl get net-attach-def -n 5g

# Check pod annotations
sudo k3s kubectl get pod <pod> -n 5g -o yaml | grep -A10 annotations

# Check Multus logs
sudo k3s kubectl logs -n kube-system -l app=multus --tail=50
```

### VXLAN Tunnel Down

**Symptom**: Pods on different nodes can't communicate

**Diagnosis**:
```bash
# Check OVS bridges
vagrant ssh worker
sudo ovs-vsctl show

# Check VXLAN port
sudo ovs-vsctl list interface | grep -A5 vxlan

# Test UDP connectivity
nc -vzu 192.168.56.12 4789
```

**Solutions**:
```bash
# Restart OVS DaemonSet
sudo k3s kubectl rollout restart ds/ds-net-setup-worker -n kube-system
sudo k3s kubectl rollout restart ds/ds-net-setup-edge -n kube-system
```

### Pod Can't Reach Overlay IP

**Symptom**: Ping to 10.20x.x.x fails

**Diagnosis**:
```bash
# Check interface exists
sudo k3s kubectl exec -n 5g <pod> -- ip addr show

# Check routes
sudo k3s kubectl exec -n 5g <pod> -- ip route

# Check ARP
sudo k3s kubectl exec -n 5g <pod> -- arp -n
```

---

## 5G Core Issues

### NF Pod CrashLoopBackOff

**Symptom**: 5G NF pod keeps restarting

**Diagnosis**:
```bash
sudo k3s kubectl logs -n 5g deploy/<nf-name> --previous
sudo k3s kubectl describe pod -n 5g -l app=<nf-name>
```

**Common causes**:
- Config error: Check ConfigMap
- NRF not ready: NFs depend on NRF for registration
- Network interface missing: Check Multus annotation

### AMF Not Listening on SCTP

**Symptom**: gNB can't connect to AMF

**Diagnosis**:
```bash
# Check SCTP port
sudo k3s kubectl exec -n 5g deploy/amf -- ss -Slnp | grep 38412

# Check AMF logs
sudo k3s kubectl logs -n 5g deploy/amf -c amf | grep -i error
```

**Solutions**:
```bash
# Verify SCTP module
vagrant ssh worker
lsmod | grep sctp
sudo modprobe sctp
```

### SMF-UPF PFCP Association Failed

**Symptom**: PDU sessions fail, no user plane

**Diagnosis**:
```bash
# Check PFCP ports
sudo k3s kubectl exec -n 5g deploy/smf -- ss -ulnp | grep 8805
sudo k3s kubectl exec -n 5g deploy/upf-cloud -- ss -ulnp | grep 8805

# Check SMF logs
sudo k3s kubectl logs -n 5g deploy/smf -c smf | grep -i pfcp

# Check connectivity
sudo k3s kubectl exec -n 5g deploy/smf -- ping -c 3 10.204.0.102
```

### PDU Session Setup Rejected (Cause 34 / Duplicated Session ID)

**Symptom**:
- AMF logs show `PDUSessionResourceSetupResponse(Unsuccessful)`
- AMF logs show `Receive Update SM context(DUPLICATED_PDU_SESSION_ID)`
- SMF logs show `Cause[Group:1 Cause:34]`
- No stable GTP-U traffic (`udp/2152`) during attach

**Diagnosis**:
```bash
# AMF failure signature
sudo k3s kubectl -n 5g logs deploy/amf -c amf --tail=300 | grep -E "PDUSessionResourceSetupResponse|DUPLICATED_PDU_SESSION_ID"

# SMF failure signature
sudo k3s kubectl -n 5g logs deploy/smf -c smf --tail=300 | grep -E "Cause\\[Group:1 Cause:34\\]|DNN|IPv4\\["

# Verify N3 gateway ownership on worker
vagrant ssh worker -c 'ip -o -4 addr show br-n3'
# Expected: 10.203.0.1/24

# Verify UPF can reach N3 gateway
vagrant ssh master -c 'sudo k3s kubectl -n 5g exec deploy/upf-cloud -c upf-cloud -- ping -c 3 -I n3 10.203.0.1'
```

**Interpretation**:
- If N4/PFCP is healthy but this signature persists, the failure is typically on gNB/UE context handling or RAN-side policy alignment (slice/DNN/session state), not on SMF↔UPF control-plane connectivity.

---

## KubeEdge Issues

### EdgeCore Not Connected

**Symptom**: Edge node shows NotReady or missing

**Diagnosis**:
```bash
# Check EdgeCore logs
vagrant ssh edge
sudo journalctl -u edgecore -f

# Check CloudCore
sudo k3s kubectl logs -n kubeedge deploy/cloudcore --tail=50
```

**Solutions**:
```bash
# Restart EdgeCore
vagrant ssh edge
sudo systemctl restart edgecore

# Check WebSocket connection
curl -k https://192.168.56.10:10000
```

### Edge Pod Can't Access K8s API

**Symptom**: Init containers fail with API errors

**Diagnosis**:
```bash
# Check from edge node
vagrant ssh edge
curl -sk https://192.168.56.10:6443/api

# Check discovery token
sudo k3s kubectl get configmap discovery-token -n 5g
```

**Solutions**:
This is a known KubeEdge limitation. Ensure:
1. Discovery token is passed as ENV var (not volume)
2. Init container adds default route
3. Token has sufficient permissions

See [KubeEdge Edge Discovery](../known-issues/kubeedge-edge-discovery.md).

---

## UERANSIM Issues

### gNB Can't Find AMF

**Symptom**: gNB init container fails

**Diagnosis**:
```bash
# Check init container logs (from edge node)
vagrant ssh edge
sudo find /var/log/pods -path '*gnb*' -name '*.log' -exec tail -20 {} \;

# Verify AMF is running
sudo k3s kubectl get pod -n 5g -l app=amf
```

### UE Registration Failed

**Symptom**: UE doesn't register with network

**Diagnosis**:
```bash
# Check AMF logs
sudo k3s kubectl logs -n 5g deploy/amf -c amf | grep -i "registration\|reject"

# Check subscriber exists
sudo k3s kubectl exec -n 5g deploy/mongodb -- mongosh open5gs --eval "db.subscribers.find()"
```

**Solutions**:
- Verify IMSI matches subscriber in MongoDB
- Check authentication keys (K, OP)
- Verify PLMN (MCC/MNC) matches

---

## Useful Commands

### Logs
```bash
# Follow NF logs
sudo k3s kubectl logs -n 5g deploy/amf -c amf -f

# All containers in pod
sudo k3s kubectl logs -n 5g <pod> --all-containers

# Previous crashed container
sudo k3s kubectl logs -n 5g <pod> --previous
```

### Exec
```bash
# Shell into pod
sudo k3s kubectl exec -it -n 5g deploy/amf -- bash

# Run command
sudo k3s kubectl exec -n 5g deploy/amf -- ip addr
```

### Network Debug
```bash
# Deploy debug pod
sudo k3s kubectl run netshoot --rm -it --image=nicolaka/netshoot -n 5g -- bash

# From inside:
ping 10.202.0.100
tcpdump -i any port 38412
```

## Related Documentation

- [Runbooks](../runbooks/README.md) - Detailed diagnostic procedures
- [Known Issues](../known-issues/) - Platform-specific workarounds
- [Architecture](../architecture/overview.md) - System design reference
