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
| Local registry (`registry:2`) | phase 12, role `local_registry`, namespace `apps` | image store; insecure HTTP + basic-auth, NodePort only |
| `mec` namespace | phase 12, role `apps_platform` | target for deployed app pods (the MEC data network `n6m-net` lives here too) |
| Worker registry mirror | phase 12, `/etc/rancher/k3s/registries.yaml` | lets k3s containerd pull pushed images |
| Apps console | dashboard backend `/api/v1/apps` + frontend Apps page | deploy / list / delete / update apps |
| Dynamic route | phase 11 front-door | proxies `kelt-<name>.<base>` to the same-named Service in `mec` |

App pods deploy into the `mec` namespace (they are MEC apps, and the `n6m-net` NAD
is there) while the registry is platform infrastructure in its own `apps` namespace.
Variables are defined once in [`ansible/group_vars/all.yml`](../../ansible/group_vars/all.yml)
(`apps_enabled`, `apps_namespace` = `mec`, `apps_registry_namespace` = `apps`,
`apps_registry_*`). Registry credentials live in `.testbed.secrets`
(`APPS_REGISTRY_PASSWORD`, managed by `testbed secrets`).

## Image flow

```
build (your machine / CI)  -->  push to local registry  -->  deploy from console  -->  reachable
docker build -t <host>/face:dev .   docker push <host>/face:dev   Apps page: image=<host>/face:dev   kelt-face.<base>
```

`<host>` is `apps_registry_host` (default: the worker LAN address `:31501`), and it
must match the mirror key in `registries.yaml` so containerd resolves the pull.
There is no in-cluster build: images are always built outside and pushed.

## Deploy model

The backend (`AppsService`) builds, in the `mec` namespace:

- a `Deployment` pinned to the worker (`nodeSelector: kubernetes.io/hostname: worker`),
  with the image **pinned to a digest**: the tag the operator picks is resolved
  against the registry at deploy time and stored as `host/repo@sha256:...`, with the
  original tag kept in the pod annotation `kelt.io/image-tag`. A tag in the local
  registry is mutable by design (an operator iterates by re-pushing the same one), so
  deploying the tag left the running image decided by whatever the node had cached,
  and re-resolved on any restart or reschedule. With a digest the deploy means one
  exact image and `imagePullPolicy: IfNotPresent` is correct: there is nothing to
  re-pull. Re-resolving a tag is the explicit "switch version" action, which also
  covers a re-push of the same tag. Apps deployed before pinning keep their tag
  reference until the next version switch,
  a **TCP readiness probe** on the container port (an arbitrary image need not expose
  `/health`), default resource limits, and env from a `<name>-config` ConfigMap +
  `<name>-secrets` Secret via `envFrom`;
- when `expose` is set, a `Service` published on **port 80** to the container port,
  so the front-door reaches it without knowing the container port.

Writes require the `dashboard-admin` role and the `allow_workload_create` policy
gate (phase 09), exactly like the Northbound deploy-from-image endpoint. See
[../security/iam.md](../security/iam.md).

## Dynamic exposure

The front-door ([phase 11](../../ansible/phases/11-frontdoor/)) carries one regex
server block (emitted only when `apps_enabled`):

```nginx
server_name ~^kelt-(?<app>[^.]+)\.<base>$;   # kelt-<app>.<base>; prefix stripped to the Service name
location / {
  set $up http://$app.<apps_namespace>.svc.cluster.local;
  proxy_pass $up;
  error_page 502 504 =404 /notfound.html;   # app unreachable / nonexistent -> branded 404
}
```

The catalogue (`kelt.<base>`) and the named service blocks (`kelt-dashboard`,
`kelt-camara`, `kelt-demo`, `kelt-placement`, `kelt-dev`) win by exact match; any
other `kelt-<name>` host is treated as an app (the prefix is stripped: `kelt-face`
-> Service `face`) and resolved at request time. A new app is reachable as soon as
its Service exists, with no template edit or front-door re-run, and the single
first-level wildcard `*.<base>` already covers its TLS and DNS. The branded 404
(with a button to the catalogue) is served only when KELT cannot reach the app: the
Service name does not resolve (no such app) or the upstream refuses / times out,
both surfacing as nginx-generated 502/504. `proxy_intercept_errors` stays off, so an
app that IS reachable and returns its own 5xx (for example a 503 maintenance page)
is passed through untouched rather than masked. See
[../security/external-access.md](../security/external-access.md).

## Security

- The registry speaks **plain HTTP** and is exposed on a **NodePort only**; it is
  never routed through the front-door / Cloudflare tunnel. It is protected by
  basic-auth and the trusted LAN/Tailscale transport (Tailscale already encrypts
  the transport end to end). Push from a host that can reach the worker NodePort.
- Writing `registries.yaml` on the worker is the only node-level change; phase 12
  **restarts k3s on the worker only when that file changes**.
- App frontends exposed at `kelt-<name>.<base>` have **no application-level auth** by
  default; they sit behind the same optional Cloudflare Access perimeter as every
  other surface (see [../security/external-access.md](../security/external-access.md)).
  An app that needs login can be fronted by the reusable `frontdoor_gate`
  (oauth2-proxy) building block.

## MEC data-network reachability (n6m)

An edge app can also be a true MEC application: reached by 5G UEs over the user
plane, not from the management network. The N6m data network itself (subnet,
bridge, VNI, the "MEC via UPF-Cloud" rationale, and the static-IP convention) is
owned by [5g-interfaces.md](5g-interfaces.md#n6-upf--data-network); this section
covers only what the apps platform adds on top.

```
UE  --GTP-U-->  gNB  -->  UPF-Cloud  --route-->  n6m DN  -->  app pod
```

Because UPF-Cloud is attached to the MEC DN, a UE whose traffic transits it reaches
a MEC app by IP with **no dedicated DNN or slice** required. A different DNN/slice
is an isolation/steering choice (or needed once the app moves behind UPF-Edge on
the edge node), not a reachability requirement.

A MEC app:

- **attaches** to the DN by joining `n6m-net` (Multus annotation, namespace `mec`);
  its HTTP UI stays exposed via the front-door as usual;
- **gets a fixed IP** (so UEs have a stable target) the same way the NFs get their
  N1-N4 addresses: an `ips` entry in the annotation
  (`{"name": "n6m-net", "namespace": "mec", "ips": ["10.208.0.x/24"]}`), whereabouts
  honoring it directly (see the static-IP convention in 5g-interfaces.md). Apps that
  do not care take a dynamic pool IP.

The video/data plane (e.g. a Raspberry Pi UE sending H264/RTP over UDP) targets the
app's n6m IP and rides the GTP tunnel. For a one-way ingest (UE to app) no return
route is needed.

Return routing for two-way MEC apps is a **per-app** concern, added to the app pod
only. It must NOT live on the `n6m-net` NAD: the UPF-Cloud also attaches that NAD
and owns the UE pools (10.45/10.46) on `ogstun`, so a UE-pool route via n6m on the
shared NAD hijacks the UE downlink and breaks internet for all UEs.

The Apps console drives the attach: the deploy form has an "attach to MEC network
(n6m)" toggle, an optional fixed-IP field (reserved band in
[5g-interfaces.md](5g-interfaces.md#static-ip-assignment-reference)), and a list of
extra container UDP ingest ports (for example `5005` for an RTP video stream that
arrives on n6m rather than through the front-door). The inventory marks an attached
app with its n6m IP.

## Verification

See the end-to-end checklist in the phase notes:
[`ansible/phases/12-apps/README.md`](../../ansible/phases/12-apps/README.md) and the
deploy walkthrough in [QUICKSTART.md](../../QUICKSTART.md).
