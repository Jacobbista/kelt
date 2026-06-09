# Coverage Tracker

Known documentation gaps and open implementation issues. Update this file when a gap is closed or a new one is identified.

**Relationship with [roadmap.md](roadmap.md):** The roadmap sorts work by horizon (near term versus longer directions) and records out-of-scope decisions. This file assigns a discrete **status** and implementation or documentation notes for each tracked gap. The same topic often appears in both; the roadmap states **priority**, this file owns **current state**.

**Status values:** `Missing` — does not exist · `Stub` — exists but incomplete · `Known Issue` — confirmed bug or limitation · `Planned` — acknowledged, work not yet started

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
| UERANSIM automated integration | Planned | Simulated-RAN automation on the edge is not actively maintained; integration work is paused. Physical gNB paths are the primary supported RAN mode until this resumes. Listed under **Near Term** in [roadmap.md](roadmap.md). |
| CI pipeline | Missing | No automated validation of the deployment. Tests run locally only via `make` targets in `tests/`. |
| Physical RAN hot-swap | Planned | Switching between physical and simulated RAN while the core is running is partially supported via the dashboard but not validated end-to-end. |
| CAMARA/positioning phase structure pending | Known Issue | All optional phases now have first-class `*_enabled` flags in `all.yml`, off by default (`ueransim_enabled`, `camara_enabled`, `positioning_enabled`, `positioning_demo_enabled`), and the main playbook self-gates on them. Remaining: finalize the CAMARA/positioning/demo phase wiring and the showcase default-on decision after the `5g-northbound` repo rework; optionally add core/addon presets to `testbed-config`. |
| VXLAN VNIs hardcoded in overlay script | Known Issue | `ansible/phases/04-overlay-network/scripts/ovs-setup.sh` hardcodes VXLAN VNIs as literals (N1=101, N2=102, N3=103, N4=104, N6e=106, N6c=107, N6m=108) instead of referencing `all.yml`, against the convention in CLAUDE.md (Networking). Only `n6m_vni` exists in `all.yml` and the script does not even use it. Move all VNIs into `all.yml` and template or parameterize the script. This missing single source is why handbook VNI values had drifted. |
