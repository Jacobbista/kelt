# KubeEdge Edge Node Discovery Issues

## Problem Summary

Pods running on KubeEdge edge nodes cannot use standard Kubernetes service discovery mechanisms (DNS, ServiceAccount tokens, ConfigMaps/Secrets) due to EdgeCore limitations.

## Issues Encountered

### 1. No CoreDNS Resolution

**Symptom:** Pods on edge nodes cannot resolve Kubernetes service names.

**Cause:** EdgeCore doesn't proxy DNS queries to CoreDNS running on the control plane.

**Workaround:** Query Kubernetes API directly using the master node IP.

### 2. Secrets and ConfigMaps Not Synced

**Symptom:** Volume mounts for Secrets/ConfigMaps are empty on edge pods.

**Cause:** KubeEdge doesn't sync Secret/ConfigMap data to edge nodes by default.

**Workaround:** Pass sensitive data as environment variables injected at deploy time by Ansible.

```yaml
# Instead of volume mount:
# volumes:
#   - name: token
#     secret:
#       secretName: discovery-token

# Use environment variable:
env:
  - name: DISCOVERY_TOKEN
    value: "{{ discovery_token }}"  # Ansible injects at deploy time
```

### 3. ServiceAccount Token Projection Fails

**Symptom:** 
```
serviceaccount.go:112] query meta "default"/"5g"/[]string(nil)/3607/v1.BoundObjectReference{...} length error
```

**Cause:** EdgeCore's implementation of projected ServiceAccount tokens has bugs with certain configurations.

**Workaround:** Generate a long-lived token using `kubectl create token` and store it in a ConfigMap (read by Ansible, injected as env var).

```bash
# Generate 1-year token
sudo k3s kubectl create token edge-discovery -n 5g --duration=8760h
```

### 4. No Default Route in Containers

**Symptom:** Containers on edge cannot reach external IPs (including K8s API server).

**Cause:** Edge CNI (flannel/edge-cni) doesn't set a default gateway for pods.

**Workaround:** Add default route in init container with NET_ADMIN capability.

```yaml
initContainers:
  - name: discovery
    securityContext:
      capabilities:
        add: ["NET_ADMIN"]
    command: ["/bin/sh", "-c"]
    args:
      - |
        # Extract eth0 network and add default route
        ETH0_NET=$(ip route | grep 'dev eth0' | awk '{print $1}' | sed 's|.0/24||')
        ip route add default via ${ETH0_NET}.1 dev eth0 2>/dev/null || true
        
        # Now can reach K8s API
        curl -sk https://192.168.56.10:6443/api
```

## Complete Workaround Implementation

### Infrastructure Setup (Ansible)

```yaml
# roles/infrastructure_setup/tasks/main.yml

# 1. Create ServiceAccount with API access
- name: Create discovery ServiceAccount
  kubernetes.core.k8s:
    definition:
      apiVersion: v1
      kind: ServiceAccount
      metadata:
        name: edge-discovery
        namespace: 5g

# 2. Create Role for pod/endpoint listing
- name: Create discovery Role
  kubernetes.core.k8s:
    definition:
      apiVersion: rbac.authorization.k8s.io/v1
      kind: Role
      metadata:
        name: edge-discovery
        namespace: 5g
      rules:
        - apiGroups: [""]
          resources: ["pods", "endpoints"]
          verbs: ["get", "list"]

# 3. Bind Role to ServiceAccount
- name: Create RoleBinding
  kubernetes.core.k8s:
    definition:
      apiVersion: rbac.authorization.k8s.io/v1
      kind: RoleBinding
      metadata:
        name: edge-discovery
        namespace: 5g
      subjects:
        - kind: ServiceAccount
          name: edge-discovery
      roleRef:
        kind: Role
        name: edge-discovery
        apiGroup: rbac.authorization.k8s.io

# 4. Generate long-lived token
- name: Generate discovery token
  shell: |
    kubectl create token edge-discovery -n 5g --duration=8760h
  register: discovery_token_result

# 5. Store in ConfigMap (will be read by Ansible, not mounted)
- name: Store token in ConfigMap
  kubernetes.core.k8s:
    definition:
      apiVersion: v1
      kind: ConfigMap
      metadata:
        name: discovery-token
        namespace: 5g
      data:
        token: "{{ discovery_token_result.stdout }}"
```

### Pod Template (gNB example)

```yaml
# Read token from ConfigMap in Ansible task
- name: Get discovery token
  kubernetes.core.k8s_info:
    kind: ConfigMap
    name: discovery-token
    namespace: 5g
  register: token_cm

- name: Extract token
  set_fact:
    discovery_token: "{{ token_cm.resources[0].data.token }}"

# In template:
spec:
  initContainers:
    - name: amf-discovery
      image: nicolaka/netshoot:latest
      securityContext:
        capabilities:
          add: ["NET_ADMIN"]  # Required for route manipulation
      env:
        - name: DISCOVERY_TOKEN
          value: "{{ discovery_token }}"  # Injected by Ansible
        - name: K8S_API
          value: "https://192.168.56.10:6443"
      command: ["/bin/sh", "-c"]
      args:
        - |
          set -e
          
          # Workaround: Add default route
          ETH0_NET=$(ip route | grep 'dev eth0' | awk '{print $1}' | sed 's|.0/24||')
          if [ -n "$ETH0_NET" ]; then
            ip route add default via ${ETH0_NET}.1 dev eth0 2>/dev/null || true
          fi
          
          # Now can query K8s API
          for i in $(seq 1 10); do
            RESP=$(curl -sk -H "Authorization: Bearer $DISCOVERY_TOKEN" \
              "${K8S_API}/api/v1/namespaces/5g/pods?labelSelector=app=amf")
            
            AMF_IP=$(echo "$RESP" | jq -r '
              .items[0].metadata.annotations["k8s.v1.cni.cncf.io/network-status"]
              | fromjson | .[] | select(.interface == "n2c1") | .ips[0]
            ')
            
            if [ -n "$AMF_IP" ] && [ "$AMF_IP" != "null" ]; then
              echo "$AMF_IP" > /config/amf-ip
              exit 0
            fi
            sleep 2
          done
          exit 1
```

## Testing the Workarounds

### Verify Token Works from Edge

```bash
# SSH to edge node
vagrant ssh edge

# Get token (must be done from master, then copied)
TOKEN="<token-value>"

# Test API access
curl -sk -H "Authorization: Bearer $TOKEN" \
  "https://192.168.56.10:6443/api/v1/namespaces/5g/pods" | jq '.items[].metadata.name'
```

### Verify Route in Container

```bash
# On edge node, find container
sudo ctr -n k8s.io task list | grep amf-discovery

# Exec into container
sudo ctr -n k8s.io task exec --exec-id test <container-id> sh -c 'ip route'

# Should show:
# default via 10.244.0.1 dev eth0
# 10.244.0.0/24 dev eth0 ...
```

## Alternative Approaches (Not Used)

### Option A: Configure EdgeCore to Use CoreDNS

Requires modifying EdgeCore configuration - complex and version-dependent.

### Option B: HostAliases

Hardcode IPs in pod spec - doesn't work for dynamic IPs.

### Option C: Sidecar with DNS Proxy

Run dnsmasq sidecar - adds complexity, resource overhead.

## Conclusion

The combination of:
1. Long-lived token stored in ConfigMap
2. Token injected as environment variable by Ansible
3. Init container with NET_ADMIN to add default route
4. Direct K8s API queries with jq parsing

Provides a robust workaround for KubeEdge's limitations while maintaining dynamic service discovery capabilities.
