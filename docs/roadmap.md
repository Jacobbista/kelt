# Roadmap

Planned improvements and future directions for the testbed. Items in **Near Term** are concrete and tied to the current codebase. Items in **Planned** are committed directions not yet started.

**Relationship with [gaps.md](gaps.md):** That tracker holds **status** and detailed notes per gap. Overlap with **Near Term** here is intentional: these bullets express **order of attention**, not a second inventory. **Planned** research lines may have no gaps row until work starts; add one when the effort becomes trackable.

Open issues and documentation gaps are tracked in [gaps.md](gaps.md).

---

## What this testbed combines

The repository wires together, in one reproducible Ansible and Vagrant flow:

- Multi-VM topology (master, worker, optional edge) with Open5GS 5GC workloads on Kubernetes (K3s).
- Kubernetes-native edge workloads via KubeEdge on the optional edge VM.
- Dedicated overlay networks per 5G logical interface using OVS and Multus secondary attachments (see [architecture/network-topology.md](architecture/network-topology.md)).
- A path for physical RAN attachment alongside simulated RAN where integration is maintained (see Near Term).

Architecture docs and Ansible playbooks are the source of truth for topology and phase behavior.

---

## Near Term

- **UERANSIM integration** — Resume maintained automation for simulated RAN on the edge; see [gaps.md](gaps.md) Implementation (**UERANSIM automated integration**). Physical gNB operation remains the better supported RAN mode until then.
- **Resolve UPF-Edge CNI route conflict** — UPF-Edge is currently disabled (`replicas: 0`). Restoring it requires identifying and fixing the CNI route conflict on the edge node. See [known-issues/upf-edge-cni-route-conflict.md](known-issues/upf-edge-cni-route-conflict.md).
- **CI pipeline** — GitHub Actions workflow to validate the full deployment stack automatically on push.
- **Observability operations guide** — user-facing documentation for the Prometheus + Loki + Grafana stack deployed in Phase 7: accessing dashboards, writing alert rules, querying logs.
- **Surface hidden documentation** — link `ansible/phases/05-5g-core/NF_ARCHITECTURE.md` from the architecture section of the documentation navigation.
- **Physical RAN hot-swap validation** — validate and document the full workflow for switching between physical and simulated RAN on a running testbed.

---

## Planned

- **O-RAN near-RT RIC** — deploy a near-RT RAN Intelligent Controller (for example FlexRIC or OSC RIC) on the edge node as a KubeEdge workload and expose the E2 interface toward a gNB that supports it. xApps would be packaged as deployable edge workloads. Depends on gNB E2 capability: UERANSIM does not provide E2, so this implies OAI, srsRAN, or a physical O-RAN-capable gNB. Prerequisite: stable physical RAN workflow (Near Term **Physical RAN hot-swap validation**).
- **NWDAF integration** — integrate a Network Data Analytics Function for closed-loop analytics and policy feedback. Open5GS NWDAF support is partial; if NWDAF becomes a primary research target, migration to free5GC (which has a dedicated NWDAF project) should be evaluated.
- **Multi-UE scenario coverage** — documented test scenarios and runbooks for concurrent UE registration, PDU session management, and QoS differentiation.
- **MEC application guide** — documentation and example application for developing workloads that consume N6 user-plane traffic on the edge node.

---

## Out of Scope

- Production-grade high availability for the 5G core
- IPv6 support across overlay networks and 5G interfaces
- Multi-site or multi-cluster federation
- Carrier-grade performance or benchmarking
