# Phase 12 — Edge apps platform

Implementation notes (not user-facing). Topic owner: [docs/architecture/edge-apps.md](../../../docs/architecture/edge-apps.md).

Opt-in (`apps_enabled` / `testbed apps on`). Provides the pieces that let an
operator deploy their own application image as a pod and reach its frontend at
`<name>.<base>` through the phase 11 front-door, without editing any template.

## Roles

- **`apps_platform`** — creates the `apps_namespace` (default `apps`). The
  dashboard deploy-from-image console (Apps page) targets this namespace; the
  backend pins app pods to the worker via `nodeSelector`.

- **`local_registry`** — an in-cluster `registry:2`:
  - `Deployment` + RWO local-path `PVC` (image blobs) + `NodePort` `Service`
    (`apps_registry_nodeport`, default 31501), pinned to the worker.
  - htpasswd basic-auth `Secret`, generated once (bcrypt via passlib) and only
    when absent, so re-runs never churn it. Delete the Secret to rotate.
  - writes `/etc/rancher/k3s/registries.yaml` on the **worker** so containerd
    pulls pushed images from the insecure http endpoint with basic-auth. This is
    the only node-level change; it **restarts k3s on the worker only when the file
    actually changes** (notify handler).

## Security posture

The registry speaks plain HTTP and is exposed on a NodePort **only** — it is never
routed through the front-door / Cloudflare tunnel. It is protected by basic-auth
and the trusted LAN/Tailscale transport. The image tag host must equal
`apps_registry_host` (the registries.yaml mirror key) so containerd resolves the
mirror. Apps exposed at `<name>.<base>` have no application-level auth by default;
they sit behind the same optional Cloudflare Access perimeter as every other
surface. See [docs/security/external-access.md](../../../docs/security/external-access.md).
