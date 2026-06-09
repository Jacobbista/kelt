# Handbook (Operator Cheat-Sheet)

A one-screen operational reference for working inside the testbed. Canonical
detail lives in the linked owners below; this page does not restate IPs, VNIs,
or phase documentation (see [CLAUDE.md](https://github.com/Jacobbista/kelt/blob/main/CLAUDE.md) for the ownership
charter).

> **kubectl**: K3s ships no standalone `kubectl`. Run from master
> (`vagrant ssh master`) with **`sudo k3s kubectl`**.

## Where to find what

| For | See |
|-----|-----|
| Interface matrix (subnets, static IPs, VXLAN VNIs) | [architecture/5g-interfaces.md](../architecture/5g-interfaces.md) |
| Node/VM topology and IPs | [architecture/overview.md](../architecture/overview.md) |
| Network design (OVS, VXLAN, Multus) | [architecture/network-topology.md](../architecture/network-topology.md) |
| Deployment phases | [deployment/phases.md](../deployment/phases.md) |
| Dashboard access and modules | [dashboard/overview.md](../dashboard/overview.md) |
| IAM, roles, OIDC clients | [security/iam.md](../security/iam.md) |
| External access and tunnels | [security/external-access.md](../security/external-access.md), [deployment/external-tunnel.md](../deployment/external-tunnel.md) |
| Troubleshooting | [troubleshooting.md](troubleshooting.md) + [runbooks/](../runbooks/) |
| Feature maturity | [status.md](../status.md) |
| Roadmap | [roadmap.md](../roadmap.md) |
| Known issues | [known-issues/](../known-issues/) |

## Quick commands

### Cluster status

```bash
sudo k3s kubectl get nodes -o wide
sudo k3s kubectl get pods -A
sudo k3s kubectl -n 5g get deploy,svc,pods
sudo k3s kubectl top pods -n 5g
sudo k3s kubectl top nodes
```

### Network

```bash
# Interfaces on a pod
sudo k3s kubectl -n 5g exec deploy/amf -- ip addr show

# Overlay
sudo ovs-vsctl show
ip -d link show | grep vxlan
sudo k3s kubectl get net-attach-def -A
```

### Logs

```bash
# K3s
journalctl -u k3s -f
journalctl -u k3s-agent -f

# KubeEdge
sudo k3s kubectl -n kubeedge logs -l app=cloudcore -f
ssh edge "journalctl -u edgecore -f"

# 5G Core
sudo k3s kubectl -n 5g logs deploy/amf -f
sudo k3s kubectl -n 5g logs deploy/smf -f

# OVS DaemonSet
sudo k3s kubectl -n kube-system logs -l app=ds-net-setup-worker -f
```

### Configuration files

- Ansible variables: `ansible/group_vars/all.yml`
- Vagrant: `Vagrantfile`
- 5G Core configs: `ansible/phases/05-5g-core/configs/`
- OVS scripts: `ansible/phases/04-overlay-network/scripts/`

## Operations

### Pod migration (UPF/MEC between cloud and edge)

> `upf-edge` is disabled by default (`replicas: 0`); it is shown here only as the migration example.

```bash
sudo k3s kubectl -n 5g get pods -o wide

# Pin a deployment to a node
sudo k3s kubectl -n 5g patch deployment upf-edge \
  -p '{"spec":{"template":{"spec":{"nodeSelector":{"kubernetes.io/hostname":"worker"}}}}}'

sudo k3s kubectl -n 5g rollout status deploy/upf-edge --timeout=120s
```

### Restart and rollout

```bash
sudo k3s kubectl -n 5g rollout restart deployment/amf
sudo k3s kubectl -n 5g rollout status deployment/amf
```

### Backup and recovery

```bash
# Back up Ansible config and live manifests
tar -czf ansible-config-backup.tar.gz ansible/
sudo k3s kubectl get all -A -o yaml > k8s-manifests-backup.yaml
sudo ovs-vsctl show > ovs-config-backup.txt

# Restore
tar -xzf ansible-config-backup.tar.gz
sudo k3s kubectl apply -f k8s-manifests-backup.yaml
```

---

For focused subsystem diagnostics (NGAP, PFCP, GTP-U, OVS, Multus) see the
[runbooks](../runbooks/). For deeper procedures see the topic owners in the
table above.
