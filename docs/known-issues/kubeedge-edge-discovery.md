# KubeEdge: Edge Node Service Discovery

KubeEdge edge nodes do not proxy DNS queries to CoreDNS and do not sync Secrets or ConfigMaps from the control plane. Standard Kubernetes service discovery mechanisms (DNS, volume-mounted tokens, environment variable injection) are unavailable to pods running on the edge.

## How It Works in This Testbed

Edge pods that need to locate other pods (e.g. gNB discovering the AMF IP) use a dedicated init container that queries the Kubernetes API directly using a long-lived bearer token injected at deploy time by Ansible.

The init container requires `NET_ADMIN` to add a default route, which is absent in edge pod networking by default. The token is generated with a 1-year TTL, stored in a ConfigMap, read by Ansible during provisioning, and passed as an environment variable — never mounted as a volume, since volume syncing does not work on edge nodes.

RBAC is scoped to `get` and `list` on `pods` and `endpoints` only.

## Implementation

- Token generation and ConfigMap storage: `ansible/phases/06-ueransim-mec/roles/infrastructure_setup/tasks/main.yml`
- Init container template (gNB): `ansible/phases/06-ueransim-mec/roles/gnb_deployment/templates/gnb-deployment.yaml.j2`
- Init container template (UE): `ansible/phases/06-ueransim-mec/roles/ue_deployment/templates/ue-statefulset.yaml.j2`
