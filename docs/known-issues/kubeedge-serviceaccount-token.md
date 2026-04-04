# KubeEdge: ServiceAccount Token Projection

KubeEdge EdgeCore cannot handle projected ServiceAccount token volumes. Pods on edge nodes that have `automountServiceAccountToken` enabled get stuck in `PodInitializing` with a length error in the EdgeCore logs.

A secondary race condition exists where Multus secondary interfaces are not yet assigned when the main container starts, causing UERANSIM to fail on first boot.

## How It Works in This Testbed

All edge pod specs set `automountServiceAccountToken: false`. The init container waits for Multus interfaces to be assigned (up to 30 seconds) before exiting, ensuring the main container starts with networking fully ready.

## Implementation

- gNB pod spec: `ansible/phases/06-ueransim-mec/roles/gnb_deployment/templates/gnb-deployment.yaml.j2`
- UE pod spec: `ansible/phases/06-ueransim-mec/roles/ue_deployment/templates/ue-statefulset.yaml.j2`
