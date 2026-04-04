# Roadmap

Planned improvements and future directions for the testbed. Items in **Near Term** are concrete and tied to the current codebase. Items in **Planned** are committed directions not yet started.

Open issues and documentation gaps are tracked separately in [gaps.md](gaps.md).

---

## Near Term

- **Resolve UPF-Edge CNI route conflict** — UPF-Edge is currently disabled (`replicas: 0`). Restoring it requires identifying and fixing the CNI route conflict on the edge node. See [known-issues/upf-edge-cni-route-conflict.md](known-issues/upf-edge-cni-route-conflict.md).
- **CI pipeline** — GitHub Actions workflow to validate the full deployment stack automatically on push.
- **Observability operations guide** — user-facing documentation for the Prometheus + Loki + Grafana stack deployed in Phase 7: accessing dashboards, writing alert rules, querying logs.
- **Surface hidden documentation** — link `ansible/phases/05-5g-core/NF_ARCHITECTURE.md` from the architecture section of the documentation navigation.
- **Physical RAN hot-swap validation** — validate and document the full workflow for switching between physical and simulated RAN on a running testbed.

---

## Planned

- **O-RAN near-RT RIC** — deploy a near-RT RAN Intelligent Controller (FlexRIC or OSC RIC) on the edge node as a KubeEdge workload, exposing the E2 interface toward the gNB. Requires evaluating gNB E2 support: UERANSIM does not implement E2 natively, so this may involve replacing or supplementing it with OAI or srsRAN, or leveraging a physical gNB with O-RAN E2 capability. xApps would be packaged as deployable edge workloads.
- **NWDAF integration** — integrate a Network Data Analytics Function for closed-loop analytics and policy feedback. Open5GS NWDAF support is partial; if NWDAF becomes a primary research target, migration to free5GC (which has a dedicated NWDAF project) should be evaluated.
- **Multi-UE scenario coverage** — documented test scenarios and runbooks for concurrent UE registration, PDU session management, and QoS differentiation.
- **MEC application guide** — documentation and example application for developing workloads that consume N6 user-plane traffic on the edge node.

---

## Out of Scope

- Production-grade high availability for the 5G core
- IPv6 support across overlay networks and 5G interfaces
- Multi-site or multi-cluster federation
- Carrier-grade performance or benchmarking
