# Phase 12 - Positioning Demo

Deploys the browser-based 3D positioning demo that consumes the CAMARA
gateway (phase 10) via Keycloak PKCE authentication (phase 08) and
streams live positions from the positioning engine (phase 11). The demo
runs in the `mec` namespace, alongside other multi-access edge services.

## Run

```bash
vagrant ssh ansible
cd ~/ansible-ro
ansible-playbook phases/12-positioning-demo/playbook.yml
```

## What it does

- Pre-pulls the demo image on the worker (the `mec` namespace already
  exists from phase 05; no namespace task is needed here).
- Applies the `positioning-demo-config` ConfigMap that injects runtime
  configuration as `window.__ENV__` (CAMARA API base, Keycloak URL,
  realm, client id) without rebuilding the bundle.
- Deploys the demo as a single replica pinned to the worker, exposed on
  NodePort `31940`.
- Waits for the demo readiness probe (`GET /`) before returning.

## Configuration

The demo is a pre-built Vite bundle served by nginx. Runtime URL
overrides happen via the `env-config.js` mounted from the ConfigMap and
loaded by `index.html` before React hydration. The bundle itself never
needs a rebuild to point at a different Keycloak realm, CAMARA gateway
origin, or client id; only the ConfigMap changes, and a rolling restart
applies it.

## Image

Built from the `5g-northbound` monorepo and published to
`ghcr.io/jacobbista/5g-northbound/positioning-demo`. The default tag is
pinned in `roles/demo_setup/defaults/main.yml`. Override at deploy
time with `-e positioning_demo_image=<image>:<tag>`.

## See also

- [IAM](../../../docs/security/iam.md) realm structure and the `positioning-demo` PKCE client
- [Phase 08: IAM](../08-iam/README.md) Keycloak prerequisites
- [Phase 10: CAMARA](../10-camara/README.md) the API the demo consumes
- [Phase 11: Positioning](../11-positioning/README.md) the position source
