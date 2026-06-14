# Coverage Tracker

Known documentation gaps and open implementation issues. Update this file when a gap is closed or a new one is identified.

**Relationship with [roadmap.md](roadmap.md):** The roadmap sorts work by horizon (near term versus longer directions) and records out-of-scope decisions. This file assigns a discrete **status** and implementation or documentation notes for each tracked gap. The same topic often appears in both; the roadmap states **priority**, this file owns **current state**.

**Status values:** `Missing`: does not exist · `Stub`: exists but incomplete · `Known Issue`: confirmed bug or limitation · `Planned`: acknowledged, work not yet started

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
| CAMARA/positioning phase wiring | Planned | Reworked against the refactored `5g-northbound` platform: phases 10-12 updated to the new env contract, phase 11 adds the standalone `mock-positioning` adapter, the blueprint PVC, and `placement-editor`; `testbed northbound on` enables the whole feature in one command; phase 08 gates the camara/positioning/placement realm objects behind the feature flag. Feature stays opt-in by deliberate decision (not default-on). Remaining: see the edge single-origin proxy and placement-editor external access rows below. |
| placement-editor Keycloak gate (oauth2-proxy) | Known Issue | Phase 11 deploys an `oauth2-proxy` (NodePort `31950`) in front of the ClusterIP `placement-editor`, admitting `g-positioning-editors` or `g-dashboard-admins`. First live deploy: parked (scaled to 0). Two blockers, both folded into the central auth front-door rework: (1) issuer mismatch — Keycloak advertises its `KC_HOSTNAME` issuer (e.g. the tunnel `https://core.<domain>/auth/realms/...`), so when behind a tunnel oauth2-proxy needs the dual-URL split (browser issuer vs in-cluster discovery/JWKS URL), not just one issuer URL; (2) the `placement-editor-proxy` client only exists after a realm re-import. Until then reach placement-editor via `kubectl port-forward`. |
| Single-origin edge (collapse all surfaces under one hostname) | Planned | The dashboard frontend nginx already reverse-proxies `/api` and `/auth` (single origin); the CAMARA API can be added as a `/camara/` location. Collapsing the demo and placement-editor SPAs under sub-paths needs a Vite `base` path in the upstream `5g-northbound` images, so it is deferred to an upstream change. Today CAMARA, demo, and placement-editor (via oauth2-proxy) each have their own NodePort/origin. See `docs/security/external-access.md`. |
| Service access control (scoped grants for dynamic services) | Stub | Infra plane (`dashboard-admin`/`viewer`) separated from a service plane of per-service action labels: `camara-location-read` (positioning VIEW) and `positioning-edit` (EDIT, gating placement-editor via oauth2-proxy alongside `g-dashboard-admins`), granted via groups (`g-camara-users`, `g-positioning-editors`). Scaffolded in the realm template (gated by the northbound flag); takes effect on the next realm import (Keycloak imports once). Remaining: tenant isolation (org as group/claim) and the longer-term per-service resource-server-client + authz-contract model. See `docs/security/iam.md`. |
| Realm reconcile cannot create new clients | Known Issue | Keycloak imports the realm JSON only on first boot. Enabling `northbound` on an already-provisioned cluster does not auto-create the camara/positioning/placement clients; re-import the realm or add them via the admin console. See `docs/security/iam.md`. |
| Blueprint PVC requires single-node co-location | Known Issue | The `positioning-blueprint` PVC is RWO on the default `local-path` StorageClass (node-local). Every consumer (engine, placement-editor, any blueprint-mounting adapter) must run on the same node; all northbound pods already pin `nodeSelector: kubernetes.io/hostname: worker`. Propagating placement-editor edits into the engine's live floor plan is still an operator step. |
| VXLAN VNIs hardcoded in overlay script | Known Issue | `ansible/phases/04-overlay-network/scripts/ovs-setup.sh` hardcodes VXLAN VNIs as literals (N1=101, N2=102, N3=103, N4=104, N6e=106, N6c=107, N6m=108) instead of referencing `all.yml`, against the convention in CLAUDE.md (Networking). Only `n6m_vni` exists in `all.yml` and the script does not even use it. Move all VNIs into `all.yml` and template or parameterize the script. This missing single source is why handbook VNI values had drifted. |
