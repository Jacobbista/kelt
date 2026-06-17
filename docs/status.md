# Feature Maturity

This page records the maturity of each component as of the current commit. It
is the single source of truth for what is validated, what is scaffolded, and
what is planned. The README carries a condensed summary that links here.

---

## Tiers

- **Supported**: deploys through the standard flow, is documented, and has been
  exercised end to end (thesis result, automated test, or manual validation).
  Reproducible on a clean install. Enabled by default.
- **Experimental**: code and manifests exist and deploy, but the path is not
  validated end to end, or it depends on an experimental component, or it has no
  exercised use case. Often disabled by default (`replicas: 0` or opt-in).
- **Planned**: described as a direction, with no working code yet.

Tier assignment rule: a component is Supported only when all three conditions
hold (deploys through the standard flow, is documented, exercised end to end).
If one condition fails it is Experimental. With no working code it is Planned.
Abandoned code is removed, not tiered.

A tier followed by `*` is an explicit exception: the component is shown at its
target tier while end-to-end validation is still in progress. The remaining work
is tracked in [gaps.md](gaps.md), and the marker is removed once the component is
validated end to end.

The **Validated by** column records the evidence: a thesis reference, a test
path, `manual`, or `—`.

---

## Components

| Component | Tier | Validated by | Default | Notes |
|---|---|---|---|---|
| Core deployment (K3s master + worker, Open5GS SBA NFs, MongoDB) | Supported | manual | on | 12 pods; AMF, SMF, UPF, NRF, PCF, AUSF, BSF, NSSF, UDM, UDR |
| Per-interface VXLAN overlays (N1-N4) on OVS | Supported | manual | on | one VNI per interface |
| Multus NADs + Whereabouts IPAM | Supported | manual | on | |
| IAM / Keycloak realm (phase 08) | Supported | manual | on | admin and viewer roles, orthogonal CAMARA role |
| Dashboard modules: Overview, Kubernetes, 5G Core, Topology, RAN, Subscribers, UE Monitor, Metrics, IAM | Supported | manual | on | image hot-updatable on a running testbed; UE session data from a native Open5GS endpoint |
| Dashboard module: Northbound (positioning/CAMARA service console) | Experimental | — | on | inventory + adapter registry + deploy-from-image + fusion + contract guidance; deploy-from-image gated by `allow_workload_create`; needs e2e against the live stack |
| Node and NF metrics (Prometheus to Metrics module and Overview) | Supported | manual | on | |
| Physical RAN attach (femtocell) | Supported | manual | on | validated as a working private 5G network, end to end |
| CAMARA Location + positioning demo (phase 10 northbound) | Supported * | manual: retrieve e2e + make iam | off | thesis core; opt-in via `testbed northbound on`; CAMARA retrieve returns a location end to end (client_credentials token to gateway to engine to mock, HTTP 200), `make iam` green; interactive demo/editor browser login still being exercised |
| Standalone `mock-positioning` adapter (phase 10, positioning_engine) | Supported * | manual | off | deployed in the lean baseline, seeded into the engine `ADAPTER_URLS`; exercises the real `/measurement` HTTP contract end to end |
| `placement-editor` geometry UI (phase 10, placement_editor) | Experimental | — | off | write-client to the engine blueprint store (`PUT /blueprint`, no PVC); always fronted by the generic `frontdoor_gate` (oauth2-proxy, NodePort 31950, Keycloak `g-positioning-editors`/`g-dashboard-admins`); gate verified live (302 to canonical login, dual-URL); interactive login + the v0.5.0 blueprint round trip (editor PUT → engine → demo via gateway) not yet exercised e2e (see docs/gaps.md) |
| `frontdoor_gate` reusable Keycloak gate (phase 10) | Experimental | manual | off | generic oauth2-proxy role (dual-URL: browser canonical issuer, server-side in-cluster); gates any no-auth surface; placement-editor is the first consumer; redirect-to-login proven, interactive e2e pending |
| Custom adapter catalog (`wifi-positioning`, `rest-adapter`, bring-your-own image) | Experimental | — | off | deployed on demand from the Northbound dashboard console, not by Ansible |
| 3GPP network-based positioning (LMF) | Planned | — | — | out of scope on current hardware; position is sourced from non-3GPP adapters instead. See [known-issues/no-network-based-positioning.md](known-issues/no-network-based-positioning.md) |
| Guided contract-driven service setup (dashboard) | Experimental | manual | off | reads each service's `/contract` (0.3.0+) and walks required/recommended/optional fields; apply routes by `sensitive` to Secret vs ConfigMap (single mechanism), then rolls; first version, UX iterating |
| Single-origin front-door (phase 11) | Experimental | manual: --syntax-check + check-mode render | base-domain-conditional | in-cluster nginx routes `<subdomain>.<base>` by Host behind one Cloudflare wildcard; deploys only when `external_base_domain` is set; templates render clean, end-to-end routing through the wildcard not yet exercised on a live tunnel |
| Idempotent re-provision and frontend image update (CLI/TUI) | Supported | manual | on | non-breaking upgrade of existing deployments is a v1 goal |
| Diagnostics / log management | Experimental | — | on | present, not extensively validated |
| Grafana advanced and alert rules (phase 07) | Experimental | — | opt-in | metrics pipeline works; alerting and log dashboards unvalidated |
| UERANSIM simulated RAN (phase 06) | Experimental | — | off | not currently exercised; see Reproducibility scope |
| KubeEdge edge node (phase 03) | Experimental | — | off | semi-implemented, no exercised use case |
| UPF-MEC | Experimental | — | `replicas: 0` | CNI route conflict, see [known-issues/upf-edge-cni-route-conflict.md](known-issues/upf-edge-cni-route-conflict.md) |
| Edge worker provisioning from dashboard | Planned | — | — | not built |
| MEC service scheduling (dashboard) | Planned | — | — | depends on the edge path |
| O-RAN near-RT RIC, NWDAF, NF update detection | Planned | — | — | directions, no code |

---

## Companion repositories

The testbed consumes images produced by two separate repositories, bound by
image tags in `ansible/group_vars/all.yml`.

| Repository | Role | Status | Binding |
|---|---|---|---|
| `nf-platform` | Builds and patches Open5GS NF images, CI/CD, versioning | Image build pipeline in use; per-NF independent updates planned | image tags consumed via `all.yml` |
| `5g-northbound` | CAMARA and positioning application images | In active development | image tags pulled by phases 10-12 |

---

## Reproducibility scope

The reproducible artifact is the software platform: the 5G core, SDN/NFV
orchestration, overlay networking, IAM, dashboard, and the optional northbound
addons all deploy in software on a single workstation from a clean install, with
no RAN hardware required.

The RAN attaches at a clean boundary, in two modes:

- **Physical femtocell**: plug and play. A femtocell connected to the worker
  yields a working private 5G network end to end. This is the validated path
  today.
- **UERANSIM (software RAN)**: a hardware-free end-to-end data plane. In
  progress, a v1 objective.

Hardware is needed only for an over-the-air data plane, not to deploy or operate
the platform. See [deployment/phases.md](deployment/phases.md) for the core vs
optional phase split.
