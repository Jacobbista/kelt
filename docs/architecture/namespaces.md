# Namespaces

This document owns the Kubernetes namespaces of the testbed: what each one holds
and the rule that decides where a workload goes. The names are declared once, in
the Namespaces block of [`ansible/group_vars/all.yml`](../../ansible/group_vars/all.yml);
the playbooks, the dashboard backend, the test suites and the experiment scripts
all read them from there.

---

## The rule

**A namespace groups workloads by role, never by location.**

A namespace is a boundary for ownership, permissions and network policy. Where a
pod runs is decided by its node placement (`nodeSelector`, affinity), not by its
namespace: an app in `mec` can run on the worker today and on an edge node joined
through KubeEdge tomorrow without changing namespace. In the same way a network
attachment (NAD) is a cluster-wide object, so nothing in it may depend on which
node a namespace "belongs" to.

---

## The namespaces

| Namespace | Variable | Holds | Deployed by |
|-----------|----------|-------|-------------|
| `5g` | `namespace_5g` | the 5G core NFs, the subscriber database, the `netshoot` probe, the core overlay NADs | phases 04, 05 |
| `mec` | `apps_namespace` | every deployed application, on any site: apps from the dashboard Apps page, the positioning demo (`location-app`), the N6m NADs | phases 04, 10, 12 and the dashboard |
| `registry` | `registry_namespace` | the local image registry the apps are pulled from | phase 12 |
| `positioning` | `positioning_namespace` | the positioning engine, its adapters, the placement editor | phase 10 and the dashboard |
| `camara` | `camara_namespace` | the CAMARA gateway, the northbound API surface | phase 10 |
| `iam` | `iam_namespace` | Keycloak and its database | phase 08 |
| `monitoring` | `monitoring_namespace` | Prometheus, Grafana, Loki, the exporters | phase 07 |
| `dashboard` | `dashboard_namespace` | the dashboard frontend and docs (the backend runs on the ansible VM) | phase 09 |
| `frontdoor` | `frontdoor_namespace` | the front-door reverse proxy | phase 11 |
| `kubeedge` | `kubeedge_namespace` | CloudCore; the name is fixed by `keadm` | phase 03 |

`kube-system` and the other Kubernetes namespaces keep their standard roles
(Multus, the OVS setup DaemonSet, CoreDNS).

`positioning` and `camara` are separate on purpose: the gateway is the only
component that exposes the API, and the engine behind it is not meant to be
reached directly. The namespace boundary is where that is enforced.

---

## Changing a name

Edit the Namespaces block in `all.yml` and re-run the phases that deploy into
that namespace. The dashboard backend reads the names at start, so re-run phase
09 as well. Nothing moves existing objects: renaming a namespace redeploys into
the new one, and the old one is left to clean up. The one exception is the
registry, whose phase moves the stored images from its former `apps` namespace.

---

## Network policies

Phase 13 turns the namespace boundary into a network boundary on the pod network
(`eth0`). The flows are declared in one table,
`ansible/phases/13-network-policies/roles/network_policies/defaults/main.yml`;
`network_policies_enabled` in `all.yml` switches them all on or off. The overlay
planes (N1-N6) are not pod-network traffic and are covered by the worker plane
filter instead, see [Plane Isolation](plane-isolation.md). Policies are not
enforced on KubeEdge edge nodes.

### How they work

A NetworkPolicy selects pods and lists what may reach them (ingress) or what they
may reach (egress). A pod no policy selects accepts everything. Once any policy
selects a pod, only what the policies allow reaches it; policies only add allows,
there is no explicit deny. Each listed namespace therefore gets:

- `default-deny-ingress`: selects every pod, allows nothing;
- `allow-common-ingress`: the namespace's own pods, metrics scrapes from
  `monitoring`, and the nodes and the management network (below);
- `allow-<app>-ingress`: one per declared flow, opening the pods of one app (or
  of the whole namespace) to the listed namespaces.

The k3s embedded controller (kube-router) enforces them with iptables on each
node. Applying or removing a policy takes effect within seconds.

### Why the nodes and the management network are trusted

Measured on this cluster with a default-deny namespace before any policy was
written:

| Source | Without an allow | Why |
|--------|------------------|-----|
| the pod's own node (kubelet probes) | allowed | kube-router lets node-local traffic through |
| the other node | blocked | arrives with that node's flannel address (`10.42.x.0`) |
| a NodePort, entering on the pod's node | blocked | the filter runs before kube-proxy rewrites the source, so it sees the client (`192.168.56.x`) |
| a NodePort, entering on the other node | blocked | arrives with that node's flannel/cni address |

So every listed namespace accepts the management network (`mgmt_subnet`) and the
first two addresses of each node's `podCIDR`, read from the cluster at apply time.
Without that, every NodePort (Keycloak, the registry, the front door, MongoDB for
the dashboard) stops answering.

### What is allowed

| Namespace | Accepts, besides the common sources |
|-----------|-------------------------------------|
| `5g` | nothing: in particular apps cannot reach MongoDB or the SBI |
| `mec` | the front door, to any app |
| `registry` | nothing (containerd pulls and pushes arrive through the NodePort) |
| `positioning` | `camara` to any pod (the gateway calls the engine and the adapters); the front door to the placement gate |
| `camara` | the front door and `mec` to the gateway |
| `iam` | `camara`, `positioning`, `dashboard` (login proxy), the front door and `mec` to Keycloak; its database only from inside `iam` |
| `dashboard` | the front door |
| `monitoring`, `frontdoor` | nothing |

`mec` is also limited outbound, because it runs third-party code: DNS, the CAMARA
gateway, Keycloak, and public internet addresses. Every private range (pod and
service networks, overlays, management and RAN networks) is out of reach, so an
app cannot touch the core, the databases, the positioning internals or the VMs.
The apps' UE traffic on N6m is not affected: it does not cross the pod network.

`kube-system`, `kubeedge` and `default` are not listed and keep Kubernetes' default.

### How the table was built

The cross-namespace flows were sampled with conntrack on both nodes for 15
minutes, while the test suites ran, and checked against every in-cluster URL
the playbooks configure. The policies were then applied one namespace at a time,
checking after each that every NodePort and front-door host answered as before,
and from a probe pod in `mec` that the allowed destinations stayed reachable and
the others did not.

To look at them, open the dashboard's Isolation page (Network group): one row per
namespace, and a check that tells whether a given flow passes and which policy
decides it. From a shell:

```bash
sudo k3s kubectl get networkpolicy -A
sudo k3s kubectl -n iam describe networkpolicy allow-keycloak-ingress
```

A new flow (an app that needs another service, a namespace that calls another)
is a new `allow` entry in the table and a re-run of phase 13.
