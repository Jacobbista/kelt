# Coverage Tracker

Known documentation gaps and open implementation issues. Update this file when a gap is closed or a new one is identified.

**Status values:** `Missing` — does not exist · `Stub` — exists but incomplete · `Known Issue` — confirmed bug or limitation · `Planned` — acknowledged, work not yet started

---

## Documentation

| Area | Status | Notes |
|------|--------|-------|
| Observability operations guide | Missing | Phase 7 deploys Prometheus, Loki, and Grafana. No user-facing guide exists for accessing dashboards, writing alert rules, or querying logs. Implementation notes are in `ansible/phases/07-observability/README.md`. |
| 5G NF architecture | Stub | `ansible/phases/05-5g-core/NF_ARCHITECTURE.md` documents Open5GS NF interactions, 3GPP interface references, and message flows in detail but is not linked from the documentation navigation. |
| MEC application development | Missing | No guide covers writing or deploying an application that consumes N6 user-plane traffic on the edge node. |
| Upgrade and migration | Missing | No procedure for upgrading K3s, KubeEdge, or Open5GS in a running testbed. |
| Multi-UE scenarios | Missing | No walkthrough for concurrent UE registration, PDU session management, or QoS differentiation experiments. |

---

## Implementation

| Area | Status | Notes |
|------|--------|-------|
| UPF-Edge CNI route conflict | Known Issue | UPF-Edge pod gets stuck in `ContainerCreating` due to a CNI route conflict on the edge node. Currently mitigated by setting `replicas: 0`. Root cause documented in [known-issues/upf-edge-cni-route-conflict.md](known-issues/upf-edge-cni-route-conflict.md). |
| CI pipeline | Missing | No automated validation of the deployment. Tests run locally only via `make` targets in `tests/`. |
| Physical RAN hot-swap | Planned | Switching between physical and simulated RAN while the core is running is partially supported via the dashboard but not validated end-to-end. |
