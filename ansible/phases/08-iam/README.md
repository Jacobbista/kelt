# Phase 08 - IAM (Keycloak + PostgreSQL)

Deploys a Keycloak OAuth2/OIDC server backed by PostgreSQL inside the
`iam` namespace. Provides JWT authentication for every downstream service
that needs it: dashboard (phase 09), CAMARA gateway (phase 10), positioning
demo (phase 12).

## Run

```bash
vagrant ssh ansible
cd ~/ansible-ro
ansible-playbook phases/08-iam/playbook.yml
```

Production-style deploy with overrides:

```bash
ansible-playbook phases/08-iam/playbook.yml \
  -e keycloak_admin_password='<strong>' \
  -e keycloak_db_password='<strong>' \
  -e camara_client_secret='<strong>' \
  -e dashboard_readonly_secret='<strong>' \
  -e dashboard_external_origin=https://dashboard.example.com \
  -e positioning_demo_external_origin=https://demo.example.com \
  -e keycloak_path_prefix=/auth
```

Automatic deploy from environment variables (useful for notebooks/pipelines):

```bash
export KEYCLOAK_ADMIN_PASSWORD='<strong>'
export KEYCLOAK_DB_PASSWORD='<strong>'
export CAMARA_CLIENT_SECRET='<strong>'
export DASHBOARD_READONLY_SECRET='<strong>'
export DASHBOARD_EXTERNAL_ORIGIN='https://dashboard.example.com'
export POSITIONING_DEMO_EXTERNAL_ORIGIN='https://demo.example.com'
export KEYCLOAK_PATH_PREFIX='/auth'
ansible-playbook phases/08-iam/playbook.yml
```

## What it does

- Creates the `iam` namespace.
- Provisions a PostgreSQL Deployment with a PVC (`local-path` StorageClass)
  for the realm and user database.
- Deploys Keycloak 26 with `--import-realm`, mounting the rendered realm
  JSON as a ConfigMap. The realm contains four clients, three realm roles,
  and three groups; full schema lives in
  [docs/security/iam.md](../../../docs/security/iam.md).
- Exposes Keycloak via NodePort (default `31910`).
- Waits for readiness on the Keycloak management endpoint (`:9000/health/ready`).

## Path prefix vs subdomain

The realm import places the OIDC issuer at
`<external-origin>${keycloak_path_prefix}/realms/<realm>`. Two layouts are
supported:

- `keycloak_path_prefix: ""` (default): Keycloak at the root of its own
  hostname. Requires a second tunnel/DNS entry for production exposure.
- `keycloak_path_prefix: "/auth"`: Keycloak served behind a path-prefix
  reverse proxy on the same origin as the dashboard. Single tunnel,
  fewer moving parts.

The choice affects the deployment template (`KC_HTTP_RELATIVE_PATH` env).
Health probes use the Keycloak management endpoint and are independent from
the public path prefix. The dashboard frontend (phase 09) reverse-proxies
`/auth/*` to Keycloak when the path-prefix layout is enabled.

## Idempotency notes

- Namespace and PVC creation use `kubernetes.core.k8s` with
  `state: present` and are safe to re-run.
- The ConfigMap holding the realm JSON is regenerated on every run; a
  checksum annotation on the Keycloak Deployment template triggers a
  rollout when the realm content changes.
- Keycloak's `--import-realm` flag is **import-once**: the realm is
  imported on first start and skipped if it already exists. Subsequent
  edits to the realm JSON are not applied automatically. See
  [docs/security/iam.md](../../../docs/security/iam.md) for update
  strategies.
- The admin password is also **import-once**: `KEYCLOAK_ADMIN_PASSWORD` is
  honored only on the first boot, after which the credential lives in the
  `keycloak-db-data` PVC. Recovery requires resetting it from an
  authenticated session or destroying the PVC. See
  [docs/security/iam.md](../../../docs/security/iam.md#admin-password-is-import-once).

## Variables of interest

All variables live in `roles/keycloak_setup/defaults/main.yml`. Resolution
order is: `-e` extra-vars, then environment variables (`KEYCLOAK_*`,
`DASHBOARD_*`, `CAMARA_CLIENT_SECRET`), then the lab-safe defaults (`changeme-*`,
LAN URLs). See [docs/security/external-access.md](../../../docs/security/external-access.md)
for the full list.

## Verification

The checks below are automated by the `iam` test suite
(`cd tests && make iam`), which validates pods, discovery, clients, roles,
and a CAMARA service-account token in one run. The manual equivalents:

```bash
# Pods Running
sudo k3s kubectl -n iam get pods

# Realm reachable (root-path layout)
curl -fsS http://<worker>:31910/realms/5g-testbed/.well-known/openid-configuration | jq .issuer

# Realm reachable (path-prefix /auth layout)
curl -fsS http://<worker>:31910/auth/realms/5g-testbed/.well-known/openid-configuration | jq .issuer

# Service account token (CAMARA gateway). --data-urlencode is required:
# base64 secrets can contain "+", which form-encoding decodes to a space.
curl -fsS -X POST \
  http://<worker>:31910/realms/5g-testbed/protocol/openid-connect/token \
  --data-urlencode grant_type=client_credentials \
  --data-urlencode client_id=camara-gateway \
  --data-urlencode client_secret=<camara_client_secret> | jq .access_token
```

## See also

- [IAM](../../../docs/security/iam.md) — realm structure, role matrix, user provisioning
- [External Access](../../../docs/security/external-access.md) — tunnel layout
- [Phase 09: Dashboard](../09-dashboard/README.md)
- [Phase 10: CAMARA](../10-camara/README.md)
