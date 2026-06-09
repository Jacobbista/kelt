# Phase 10 - CAMARA Gateway

Deploys the CAMARA Location API gateway as the northbound exposure layer
over Open5GS. The gateway implements the official CAMARA OpenAPI from
`camaraproject/DeviceLocation`, validates incoming OAuth2 Bearer tokens
against the Keycloak realm from phase 08, and forwards device-id lookups
to the positioning engine (phase 11), with an internal mock fallback when
the engine is unreachable.

## Run

```bash
vagrant ssh ansible
cd ~/ansible-ro
ansible-playbook phases/10-camara/playbook.yml
```

Production-style deploy with overrides:

```bash
ansible-playbook phases/10-camara/playbook.yml \
  -e camara_client_secret='<strong>' \
  -e keycloak_path_prefix=/auth
```

Environment variables `CAMARA_CLIENT_SECRET`, `KEYCLOAK_REALM`, and
`KEYCLOAK_PATH_PREFIX` are honored when extra vars are absent. Resolution
order: extra-vars, then environment, then lab defaults.

## What it does

- Creates the `camara` namespace.
- Pre-pulls the gateway image on the worker.
- Applies the `camara-config` ConfigMap (Keycloak issuer URL, realm,
  upstream service URLs).
- Deploys the gateway as a single replica pinned to the worker, exposed
  on NodePort `31920`.
- Waits for the gateway readiness probe (`/health`) before returning.

## Endpoints exposed

| Route | Method | Auth |
|-------|--------|------|
| `/location-retrieval/v0.5/retrieve` | POST | Bearer (role `camara-location-read`) |
| `/location-verification/v3/verify` | POST | Bearer (role `camara-location-read`) |
| `/health` | GET | none |
| `/docs` | GET | none (OpenAPI explorer) |

The exact API version segments are pinned to a tagged release of
`camaraproject/DeviceLocation`; see the upstream image repository
(`5g-northbound`) for the recorded commit SHA.

## Image

Built from the `5g-northbound` monorepo and published to
`ghcr.io/jacobbista/5g-northbound/camara-gateway`. The default tag is
pinned in `roles/camara_setup/defaults/main.yml`. Override at deploy
time with `-e camara_gateway_image=<image>:<tag>` to roll a new build.

## See also

- [IAM](../../../docs/security/iam.md) realm structure, role matrix
- [Positioning Adapters](../../../docs/architecture/positioning-adapters.md) engine and adapter layer
- [Phase 08: IAM](../08-iam/README.md) Keycloak prerequisites
- [Phase 11: Positioning](../11-positioning/README.md) engine the gateway consumes
