# Identity and Access Management

Phase 08 deploys a Keycloak realm shared by every service in the testbed
that authenticates HTTP traffic: the dashboard (phase 09), the CAMARA
gateway (phase 10), and the positioning demo (phase 12). Single realm,
multiple clients, role-based authorization.

## Realm topology

```
Realm: 5g-testbed (configurable via keycloak_realm)
├── Clients
│   ├── camara-gateway       confidential  client_credentials
│   ├── positioning-demo     public        PKCE
│   ├── dashboard            public        PKCE
│   └── dashboard-readonly   confidential  client_credentials
├── Realm roles
│   ├── camara-location-read
│   ├── dashboard-admin
│   └── dashboard-viewer
├── Groups
│   ├── g-camara-users       → camara-location-read
│   ├── g-dashboard-admins   → dashboard-admin
│   └── g-dashboard-viewers  → dashboard-viewer
└── Service accounts
    ├── camara-gateway       → camara-location-read
    └── dashboard-readonly   → dashboard-viewer
```

## Clients

| Client | Type | Flow | Use case |
|--------|------|------|----------|
| `camara-gateway` | confidential | `client_credentials` | The CAMARA Location gateway calls Keycloak with its client secret on boot and at refresh; tokens carry `camara-location-read`. |
| `positioning-demo` | public | PKCE | Browser app for the 3D positioning visualization. Standard authorization-code-with-PKCE flow. |
| `dashboard` | public | PKCE | Browser frontend of the operations dashboard. Tokens carry `dashboard-admin` or `dashboard-viewer` depending on the user's group. |
| `dashboard-readonly` | confidential | `client_credentials` | Headless read-only consumer (monitoring agent, public demo, CI smoke check). Tokens carry `dashboard-viewer` only. |

## Roles → endpoint matrix (dashboard backend)

The dashboard backend validates the `Authorization: Bearer <jwt>` header
against the realm JWKS and inspects `realm_access.roles` to decide.
Enforcement lives in `dashboard/backend/app/auth.py` and is applied at
router-include time in `dashboard/backend/app/main.py`.

The matrix below documents the policy implemented today; per-route refinement
of mixed routers (where a few methods need different roles than the bulk) is
tracked as follow-up work.

| Endpoint type | `dashboard-admin` | `dashboard-viewer` |
|---------------|-------------------|---------------------|
| `GET /api/...` | ✓ | ✓ |
| `GET /health` | ✓ | ✓ |
| `POST/PUT/PATCH/DELETE /api/...` | ✓ | ✗ |
| `WS /api/v1/ws/logs/*` | ✓ | ✓ |
| `WS /api/v1/ws/exec/*` (pod shell) | ✓ | ✗ |
| `WS /api/v1/ws/sniffer/*` | ✓ | ✗ |
| `POST /api/v1/nf/update/stream` (image rollout) | ✓ | ✗ |
| `POST /watchdog/restart` | ✓ | ✗ |

Routers assigned to the **viewer-or-admin** group:
`cluster`, `kubernetes`, `pods`, `logs_ws`, `topology`, `network`, `metrics`,
`traffic`, `ue`, `time_sync`, `experiments`.

Routers assigned to the **admin-only** group:
`subscribers` (records carry K and OPc), `nf` (image rollout via ansible),
`ran` (mode switching reconfigures the data plane), `sniffer` (privileged
packet capture), `exec_ws` (pod shell).

Unauthenticated lanes:
`health` (browser useBackendHealth + watchdog probes) and the legacy `admin`
router that uses the `DASHBOARD_ADMIN_TOKEN` header for emergency restart.

The `camara-location-read` role is checked by the CAMARA gateway only and is
unrelated to the dashboard backend.

## Phased rollout

Dashboard auth now uses a single switch, `dashboard_auth_enabled`.
When true, frontend OIDC login is enabled and backend JWT validation is
enforced. When false, frontend login is disabled and backend auth bypass
is enabled as a temporary break-glass fallback. With backend bypass active,
every request is treated as if issued by a synthetic principal that holds
both `dashboard-admin` and `dashboard-viewer`; role checks still execute
but always pass.

Three checkpoints must clear before enforcing auth in production, in order:

1. **Phase 08 deployed** and the realm reachable:
   `curl <keycloak>/realms/5g-testbed/.well-known/openid-configuration`
   must return JSON with the expected `issuer` and `jwks_uri`.
2. **At least one human user provisioned** through the Keycloak admin
   console, joined to either `g-dashboard-admins` or `g-dashboard-viewers`.
3. **External origin variables aligned**: the realm's redirect URIs and
   the frontend's `VITE_KEYCLOAK_AUTHORITY` must agree on the dashboard
   origin (LAN, tunnel, or path-prefix layout).

Break-glass fallback (temporary):

```bash
ansible-playbook ansible/phases/09-dashboard/playbook.yml \
  -e dashboard_mode=prod \
  -e dashboard_auth_enabled=false \
  -e dashboard_keycloak_external_url=https://dashboard.example.com \
  -e dashboard_keycloak_path_prefix=/auth
```

(Omit `dashboard_keycloak_path_prefix` for subdomain layouts. Backend and
frontend use distinct URLs: the backend hits Keycloak via an internal
URL, typically the worker NodePort, while the browser uses the external
authority URL.)

## How the token reaches the backend

| Channel | Mechanism |
|---------|-----------|
| HTTP requests | `Authorization: Bearer <jwt>` header, injected by the fetch wrapper in `dashboard/frontend/src/api.js`. |
| WebSocket upgrades | `?access_token=<jwt>` query parameter, appended in `_wsUrl()` / `buildWsUrl()`. Browsers cannot set custom headers on WS upgrades; the backend accepts the token via FastAPI `Query(...)` as a fallback. |

The backend `get_principal()` dependency reads either source, so the same
role guards (`require_admin`, `require_viewer_or_admin`) apply uniformly to
REST and WebSocket endpoints.

## Provisioning users

Phase 08 seeds two end users on first deploy so the two-role model is
observable out of the box without prior Keycloak experience:

| Username | Group | Realm role |
|----------|-------|------------|
| `admin` | `g-dashboard-admins` | `dashboard-admin` |
| `viewer` | `g-dashboard-viewers` | `dashboard-viewer` |

Both passwords use `dashboard_bootstrap_admin_password` by default (the same
value resolved from `DASHBOARD_BOOTSTRAP_ADMIN_PASSWORD` or, when unset,
`keycloak_admin_password`). Each seed account is created with
`temporary: true`, which forces a password reset at first login. Phase 08
reruns never overwrite a password the operator changed via the Keycloak
console.

The `viewer` password can be overridden independently via
`DASHBOARD_BOOTSTRAP_VIEWER_PASSWORD`.

Additional users follow the same group-driven model:

```text
1. Admin console -> Users -> Add user
2. Set username and email
3. Credentials tab -> set temporary password
4. Groups tab -> join one of:
     g-camara-users
     g-dashboard-admins
     g-dashboard-viewers
5. Save
```

Group membership transitively grants the realm role; no per-user role
assignment is required.

## Retrieving tokens (M2M)

CAMARA gateway service account:

```bash
curl -s -X POST \
  https://<keycloak-origin>/realms/5g-testbed/protocol/openid-connect/token \
  --data-urlencode grant_type=client_credentials \
  --data-urlencode client_id=camara-gateway \
  --data-urlencode client_secret=<camara_client_secret>
```

Dashboard read-only token:

```bash
curl -s -X POST \
  https://<keycloak-origin>/realms/5g-testbed/protocol/openid-connect/token \
  --data-urlencode grant_type=client_credentials \
  --data-urlencode client_id=dashboard-readonly \
  --data-urlencode client_secret=<dashboard_readonly_secret>
```

Client secrets are generated as base64 and can contain `+` and `/`. The
token endpoint consumes `application/x-www-form-urlencoded`, where `+`
decodes to a space, so a secret passed with plain `curl -d` is corrupted and
the request fails with `unauthorized_client`. Always pass the secret with
`--data-urlencode` (or url-encode it in application code; standard HTTP
client libraries handle this automatically).

Response includes `access_token` (JWT) with the configured realm role.
Decode with `jwt.io` or `jose-jwt` to inspect `realm_access.roles`.

## Realm idempotency and reconcile

Keycloak's `--import-realm` flag imports the realm JSON only when the realm
does not yet exist. Subsequent runs of phase 08 do not re-import the realm,
so changes to the realm template (`templates/keycloak-realm.json.j2`) do not
take effect on already-provisioned clusters by default.

Phase 08 includes an opt-in reconcile step that re-applies the resolved
values via the Keycloak admin API (no DB reset). The reconciled fields are:

- `dashboard` client `redirectUris`, `webOrigins`, `post.logout.redirect.uris`
- realm roles, groups, and composite links (managed by separate idempotent
  tasks; safe to re-run)

The reconcile gate is interactive: launching phase 08 via `testbed run-phase
08-iam` prompts the operator with a description of what reconcile does and
collects an answer for that run, with the option to persist it as the new
default. Non-interactive callers honor the stored value or the explicit
environment variable:

```bash
# One-off reconcile from a script or CI run:
KEYCLOAK_REALM_RECONCILE=true testbed run-phase 08-iam

# Persist the default (no further prompts):
testbed iam reconcile on
testbed iam reconcile off
testbed iam reconcile ask     # restore the per-run prompt

# Inspect the persisted default:
testbed iam reconcile status
```

`KEYCLOAK_REALM_RECONCILE` accepts `true`, `false`, or `ask`. The persisted
default lives in `.testbed.env`. Reconcile never touches end users,
passwords, or active sessions.

Fall-back options when reconcile cannot express the change (for example, a
client added to the realm template after the initial import):

1. Manual edit via the admin console.
2. Destroy and reimport: delete the realm via the admin console, then re-run
   phase 08. All end users and runtime state in the realm are lost.

Phase 08 does not yet support flag-gated forced reimport. See
[docs/gaps.md](../gaps.md) for the open task.

## Admin password is import-once

`KEYCLOAK_ADMIN` and `KEYCLOAK_ADMIN_PASSWORD` (variables
`keycloak_admin_user` and `keycloak_admin_password`) are read only on the
first Keycloak boot, when the bootstrap admin account is written to
PostgreSQL. The credential then lives in the `keycloak-db-data` PVC.
Changing the variable on a later phase 08 run has no effect, and the master
realm token request fails with `invalid_grant` / `Invalid user credentials`
while the realm itself stays reachable.

Two recovery paths:

1. **Reset via an authenticated admin session**: log into the admin console
   with the current password and change it under the master realm Users.
2. **Destroy and reimport**: delete the backing PVC and redeploy. This also
   discards realm runtime state and all end users.

```bash
sudo k3s kubectl -n iam scale deploy/keycloak --replicas=0
sudo k3s kubectl -n iam scale deploy/keycloak-db --replicas=0
sudo k3s kubectl -n iam delete pvc keycloak-db-data
ansible-playbook ansible/phases/08-iam/playbook.yml
```

The same constraint applies to the bootstrap end-user created by phase 08:
its password is set only on creation (`temporary: true`) and is not
overwritten on subsequent runs, so an operator password change in the
console is preserved.

## See also

- [External Access](external-access.md): tunnel layout, single-origin reverse proxy
- [Phase 08: IAM](https://github.com/Jacobbista/kelt/blob/main/ansible/phases/08-iam/README.md): implementation notes
- [Phase 09: Dashboard](https://github.com/Jacobbista/kelt/blob/main/ansible/phases/09-dashboard/README.md): backend JWT middleware (planned)
- [Phase 10: CAMARA](https://github.com/Jacobbista/kelt/blob/main/ansible/phases/10-camara/README.md): gateway JWT validation
