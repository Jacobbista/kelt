# Phase 3 — KubeEdge Integration

## Overview

This phase integrates KubeEdge to extend the Kubernetes control plane to the edge node:

- **CloudCore** (on the worker): cloud-side control-plane endpoint for edge nodes
- **EdgeCore** (on the edge): lightweight kubelet replacement managing edge workloads

The edge node joins the cluster **without k3s-agent**, using only EdgeCore. This is the recommended KubeEdge architecture to avoid dual-kubelet conflicts.

## Theory

### KubeEdge Architecture

KubeEdge extends Kubernetes to the edge using a cloud-edge messaging channel:

- **CloudCore** exposes a WebSocket/QUIC tunnel on TCP 10000 and issues **join tokens**
- **EdgeCore** connects to CloudCore and acts as a lightweight kubelet, managing pods via the local containerd runtime
- Once joined, the edge node appears as a normal Kubernetes Node object, schedulable according to taints/labels

### Why EdgeCore Only (No k3s-agent)?

Running both k3s-agent and EdgeCore on the same node causes:

- **Dual kubelet conflict**: both try to manage pods and sync node status
- **Double registration**: same hostname registered twice in Kubernetes API
- **Runtime contention**: competing CRI connections to containerd

**Solution**: Edge node runs **standalone containerd + EdgeCore only**, no k3s components.

## Implementation

### Roles

| Role                 | Purpose                                                                                 |
| -------------------- | --------------------------------------------------------------------------------------- |
| `kubeedge_common`    | Installs `keadm` tool and distributes kubeconfig to worker and edge                     |
| `edge_containerd`    | **NEW**: Installs and configures standalone containerd on edge (with systemd cgroups)   |
| `kubeedge_cloudcore` | Deploys CloudCore on worker (port 10000), generates and stores edge join token         |
| `kubeedge_edgecore`  | Joins edge to cluster via `keadm join` using standalone containerd (not k3s containerd) |

### Key Implementation Details

#### Edge Containerd Setup (`edge_containerd` role)

The edge node requires a standalone containerd installation because it doesn't run k3s:

1. Installs `containerd` package via apt
2. Creates `/etc/containerd/config.toml` with `SystemdCgroup = true`
3. Enables and starts `containerd.service`

**Important**: Uses standard containerd socket `unix:///run/containerd/containerd.sock`, not k3s socket.

#### EdgeCore Join

The `keadm join` command uses:

- `--remote-runtime-endpoint=unix:///run/containerd/containerd.sock` (standalone containerd)
- `--cgroupdriver=systemd` (matches containerd config)
- Token fetched from CloudCore on worker

## Expected Results

After Phase 3:

- `kubectl get ns kubeedge` → `kubeedge` namespace present
- `kubectl -n kubeedge get pods` → CloudCore pod Running on worker
- `kubectl get nodes` → **3 nodes total**: master + worker (k3s) + edge (KubeEdge)
- Edge node shows as Ready with KubeEdge labels

### Node Count Summary

| Phase | Nodes Visible | Notes                                |
| ----- | ------------- | ------------------------------------ |
| 2     | 2 nodes       | master + worker (k3s cluster)        |
| 3     | 3 nodes       | + edge (joined via KubeEdge, no k3s) |

## Notes

- **Prerequisites**: DNS, NTP, and NAT configured in Phase 1
- **Cgroup driver**: systemd for both containerd and EdgeCore
- **No cleanup needed**: EdgeCore-only model avoids double-registration conflicts
- **CNI requirement**: Edge needs a primary CNI config (handled in Phase 4)
