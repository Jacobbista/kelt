# Phase 13 — Network policies

Implementation notes (not user-facing). Topic owner: [docs/architecture/namespaces.md](../../../docs/architecture/namespaces.md#network-policies).

Runs last: it needs the namespaces the other phases create. A declared namespace
that does not exist yet is skipped with a message and picked up on the next run.

## Role

- **`network_policies`** — renders, per declared namespace:
  - `default-deny-ingress` and `allow-common-ingress` (own namespace, `monitoring`,
    `mgmt_subnet`, and the first two addresses of each node's `podCIDR`, read from
    the Node objects at apply time);
  - one `allow-<app>-ingress` per `allow` entry of `network_policy_ingress`;
  - for `apps_namespace`, `allow-declared-egress` (DNS, the apps' declared
    destinations, public addresses outside `network_policy_private_ranges`).

  Every policy carries `app.kubernetes.io/managed-by: kelt-network-policies`; any
  managed policy the current table does not render is deleted, so removing an
  entry or setting `network_policies_enabled: false` cleans up on the next run.

The table lives in `roles/network_policies/defaults/main.yml`; namespace names
come from the Namespaces block in `group_vars/all.yml`.
