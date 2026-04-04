# KubeEdge: Multus Environment Variable Injection

KubeEdge EdgeCore injects empty `KUBERNETES_SERVICE_HOST` and `KUBERNETES_SERVICE_PORT` environment variables into all containers running on edge nodes. Because the last value in the environment wins, any values set in the pod spec are overridden by the empty KubeEdge-injected ones. Multus `thin_entrypoint` reads these variables to generate its kubeconfig, producing an invalid `server: https://[]:` entry that causes the pod to crash.

## How It Works in This Testbed

The edge Multus DaemonSet uses static config mode instead of auto mode. A kubeconfig is written to `/var/lib/multus/multus.kubeconfig` by Ansible before the DaemonSet is deployed. This path is outside `/etc/cni/net.d`, so `thin_entrypoint` does not attempt to regenerate it. The conflist references the kubeconfig by absolute path.

The worker DaemonSet uses auto mode without issues, since K3s does not inject environment variables.

## Implementation

- Edge conflist template: `ansible/phases/04-overlay-network/roles/multus_install/templates/00-multus-edge.conflist.j2`
- Kubeconfig creation and DaemonSet deployment: `ansible/phases/04-overlay-network/roles/multus_install/tasks/main.yml`
