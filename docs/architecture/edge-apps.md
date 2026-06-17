# Edge apps platform

Canonical owner for the operator-deployed application platform: the in-cluster
local registry, the deploy-from-image console, and the dynamic front-door route.
Opt-in, off by default. Enable with `testbed apps on` (see [QUICKSTART.md](../../QUICKSTART.md)).

## Purpose

Run an operator's own container image (for example a MEC demo) as a pod on the
worker node and reach its frontend at its own subdomain, without editing any
template or re-running a phase per app. The image is built outside the cluster and
pushed to an in-cluster registry; the dashboard deploys it; the front-door routes
to it the moment its Service exists.

## Components

| Piece | Where | Role |
|---|---|---|
| Local registry (`registry:2`) | phase 12, role `local_registry` | image store; insecure HTTP + basic-auth, NodePort only |
| `apps` namespace | phase 12, role `apps_platform` | target for deployed app pods |
| Worker registry mirror | phase 12, `/etc/rancher/k3s/registries.yaml` | lets k3s containerd pull pushed images |
| Apps console | dashboard backend `/api/v1/apps` + frontend Apps page | deploy / list / delete apps |
| Dynamic route | phase 11 front-door | proxies `<name>.<base>` to the same-named Service in `apps` |

Variables are defined once in [`ansible/group_vars/all.yml`](../../ansible/group_vars/all.yml)
(`apps_enabled`, `apps_namespace`, `apps_registry_*`). Registry credentials live in
`.testbed.secrets` (`APPS_REGISTRY_PASSWORD`, managed by `testbed secrets`).

## Image flow

```
build (your machine / CI)  -->  push to local registry  -->  deploy from console  -->  reachable
docker build -t <host>/face:dev .   docker push <host>/face:dev   Apps page: image=<host>/face:dev   face.<base>
```

`<host>` is `apps_registry_host` (default: the worker LAN address `:31501`), and it
must match the mirror key in `registries.yaml` so containerd resolves the pull.
There is no in-cluster build: images are always built outside and pushed.

## Deploy model

The backend (`AppsService`) builds, in the `apps` namespace:

- a `Deployment` pinned to the worker (`nodeSelector: kubernetes.io/hostname: worker`),
  with a **TCP readiness probe** on the container port (an arbitrary image need not
  expose `/health`), default resource limits, and env from a `<name>-config`
  ConfigMap + `<name>-secrets` Secret via `envFrom`;
- when `expose` is set, a `Service` published on **port 80** to the container port,
  so the front-door reaches it without knowing the container port.

Writes require the `dashboard-admin` role and the `allow_workload_create` policy
gate (phase 09), exactly like the Northbound deploy-from-image endpoint. See
[../security/iam.md](../security/iam.md).

## Dynamic exposure

The front-door ([phase 11](../../ansible/phases/11-frontdoor/)) carries one regex
server block (emitted only when `apps_enabled`):

```nginx
server_name ~^(?<app>[^.]+)\.<base>$;
location / { set $up http://$app.<apps_namespace>.svc.cluster.local; proxy_pass $up; }
```

Named subdomains (`kelt`, `api`, `demo`, `placement`, `dev`) win by exact match;
any other single-label subdomain is treated as an app name and resolved at request
time. A new app is reachable as soon as its Service exists, with no template edit or
front-door re-run. Trade-off: a mistyped known subdomain falls into this block and
yields 502 instead of the welcome page.

## Security

- The registry speaks **plain HTTP** and is exposed on a **NodePort only** — it is
  never routed through the front-door / Cloudflare tunnel. It is protected by
  basic-auth and the trusted LAN/Tailscale transport (Tailscale already encrypts
  the transport end to end). Push from a host that can reach the worker NodePort.
- Writing `registries.yaml` on the worker is the only node-level change; phase 12
  **restarts k3s on the worker only when that file changes**.
- App frontends exposed at `<name>.<base>` have **no application-level auth** by
  default; they sit behind the same optional Cloudflare Access perimeter as every
  other surface (see [../security/external-access.md](../security/external-access.md)).
  An app that needs login can be fronted by the reusable `frontdoor_gate`
  (oauth2-proxy) building block.

## Verification

See the end-to-end checklist in the phase notes:
[`ansible/phases/12-apps/README.md`](../../ansible/phases/12-apps/README.md) and the
deploy walkthrough in [QUICKSTART.md](../../QUICKSTART.md).
