# Roadmap

Planned and possible directions for the testbed, sorted by how committed each
item is. Labels are explicit so the line between decided work and exploratory
ideas stays honest.

**Relationship with [gaps.md](gaps.md):** that tracker assigns a discrete status
and notes per gap (current state). This file states priority and intent. The
same topic can appear in both. Component maturity (what is validated versus
experimental) lives in [status.md](status.md).

---

## In progress

Active development now, tied to the current thesis.

- **CAMARA Location + positioning.** Location API on Open5GS with a pluggable positioning engine and a demo SPA. Deployed and working (phases 10-12); end-to-end validation and hardening ongoing. See [architecture/positioning-adapters.md](architecture/positioning-adapters.md).

---

## Near term (decided)

Committed work tied to the current codebase.

- **UERANSIM integration.** Resume maintained automation for software RAN. This is the v1 prerequisite for a hardware-free end-to-end data plane; the validated path today uses a physical femtocell. See the reproducibility scope in [status.md](status.md).
- **Resolve UPF-Edge CNI route conflict.** UPF-Edge is disabled (`replicas: 0`). Restoring it requires fixing the CNI route conflict on the edge node. See [known-issues/upf-edge-cni-route-conflict.md](known-issues/upf-edge-cni-route-conflict.md).
- **Reconcile optional phase gating.** Make optional phases (6, 10-12) toggleable through first-class `*_enabled` flags in `all.yml`, make the main playbook self-gating, and surface the core versus full versus addon choice in `testbed-config`. Tracked in [gaps.md](gaps.md).
- **Observability operations guide.** User-facing documentation for the Prometheus, Loki, and Grafana stack from phase 7: accessing dashboards, writing alert rules, querying logs.
- **Surface hidden documentation.** Link `ansible/phases/05-5g-core/NF_ARCHITECTURE.md` from the architecture navigation.

---

## Candidate

Possible future directions. Not started and not committed. Listed because they
are plausible, not because they are planned.

- **CI pipeline.** GitHub Actions workflow to validate the deployment stack on push.
- **Traefik ingress for the front-door.** Phase 11 routes external subdomains with a small nginx that proxies by Host. k3s ships Traefik (disabled here via `--disable traefik`); adopting it would express the same routing as native `Ingress` objects, one per surface, with automatic reload, at the cost of re-enabling the ingress controller. Worth it only if the testbed grows many routes.
- **Physical RAN hot-swap validation.** Validate and document switching between physical and simulated RAN on a running testbed.
- **Dashboard NF update detection.** Compare deployed NF image tags against a canonical version list from `nf-platform` and surface per-NF update availability, with operator-triggered redeploy. Depends on `nf-platform` integration.
- **Global northbound version management.** The console already flags a catalog adapter behind the current KELT release and upgrades it in place (image-only patch, config preserved). A fuller version, deferred: query GHCR for the latest published tag of every northbound component, show current-vs-latest across managed and catalog services, and offer arbitrary-tag targeted upgrades, with the managed services still reconciled through `all.yml` so the phase stays the source of truth.
- **O-RAN near-RT RIC.** Deploy a near-RT RIC (FlexRIC or OSC RIC) on the edge node as a KubeEdge workload and expose E2 toward a capable gNB. UERANSIM does not provide E2, so this implies OAI, srsRAN, or a physical O-RAN gNB.
- **NWDAF integration.** Network Data Analytics Function for closed-loop analytics. Open5GS NWDAF support is partial; if it becomes a primary target, evaluate free5GC.
- **Network-based (3GPP) positioning.** The CAMARA surface is technology-agnostic and already serves non-3GPP fixes. Attaching an LMF-capable or open RAN, or a simulator that exposes positioning, alongside an LMF would let the same gateway serve network-sourced fixes next to the asset path. KELT orchestrates the components, so this is an integration step rather than a rearchitecture. Bounded today by hardware, see [known-issues/no-network-based-positioning.md](known-issues/no-network-based-positioning.md).
- **Multi-UE scenario coverage.** Test scenarios and runbooks for concurrent registration, PDU session management, and QoS differentiation.
- **MEC application guide.** Documentation and an example application consuming N6 user-plane traffic on the edge node.

---

## Out of scope

- Production-grade high availability for the 5G core
- IPv6 across overlay networks and 5G interfaces
- Multi-site or multi-cluster federation
- Carrier-grade performance or benchmarking
