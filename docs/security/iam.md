# Identity and Access Management

Phase 08 deploys a Keycloak realm shared by every service in the testbed
that authenticates HTTP traffic: the dashboard (phase 09) and the northbound
services (phase 10): the CAMARA gateway and the positioning demo. Single realm,
multiple clients, role-based authorization.

## Realm topology

```
Realm: 5g-testbed (configurable via keycloak_realm)
├── Clients
│   ├── camara-gateway        confidential  client_credentials   (northbound)
│   ├── positioning-demo      public        PKCE                 (northbound)
│   ├── placement-editor-proxy confidential auth-code (oauth2-proxy) (northbound)
│   ├── dashboard             public        PKCE
│   └── dashboard-readonly    confidential  client_credentials
├── Realm roles
│   ├── dashboard-admin          (infra plane)
│   ├── dashboard-viewer         (infra plane)
│   ├── camara-location-read     (service plane: positioning VIEW)   (northbound)
│   └── positioning-edit         (service plane: positioning EDIT)   (northbound)
├── Groups
│   ├── g-dashboard-admins    → dashboard-admin
│   ├── g-dashboard-viewers   → dashboard-viewer
│   ├── g-camara-users        → camara-location-read      (northbound)
│   └── g-positioning-editors → positioning-edit          (northbound)
└── Service accounts
    ├── camara-gateway       → camara-location-read
    └── dashboard-readonly   → dashboard-viewer
```

### Two access planes

Access is split into two independent planes so a service-consumer token cannot
reach the cluster:

- **Infra plane**: managing the dashboard/cluster. `dashboard-admin` (writes,
  exec, sniffer, NF rollout, restart) and `dashboard-viewer` (reads). Unchanged.
- **Service plane**: consuming a deployed service, via per-service action roles.
  For positioning today: `camara-location-read` = VIEW (call the CAMARA Location
  API, the demo / customer view) and `positioning-edit` = EDIT (author geometry
  in placement-editor, the partner view). Granted via groups
  (`g-camara-users`, `g-positioning-editors`).

A token minted for the service plane (e.g. "view the demo") carries only its
service role, not `dashboard-*`, so it cannot hit the dashboard backend.

Tenancy (which CAMARA assets a caller sees) is enforced by the gateway on one
rule: it matches the caller's `org` token claim against `asset.org`. A caller
with no `org` claim is the operator (god-mode, sees every tenant). `org` is a
user attribute on the authenticating principal, person OR service account, emitted
by a shared `org` token mapper on the browser and consumer clients, so a single
rule covers both access modes:

- **Browser login** (`dashboard`, `positioning-demo`): `org` = the logged-in
  user's attribute. The operator `admin` has none, so admin sees all; a user with
  `org=demo` sees only that tenant.
- **CAMARA API, M2M** (`camara-api-demo`): `org` = the client's service-account
  attribute. The reference consumer is scoped to `camara_org` (default `demo`).

The testbed is single-tenant by default (`camara_org` in `all.yml`, the single
source). Multi-tenant is additive: give other principals a different `org`
attribute, no code change. The `dashboard` and `camara-gateway` operator
identities carry no `org` and stay god-mode by design.

## Clients

| Client | Type | Flow | Use case |
|--------|------|------|----------|
| `camara-gateway` | confidential | `client_credentials` | The CAMARA Location gateway's own client; no `org` attribute, so it is the operator bypass (sees all tenants). Tokens carry `camara-location-read`. |
| `camara-api-demo` | confidential | `client_credentials` | Reference per-consumer CAMARA API client. Scoped to a tenant by the `org` attribute on its service account (default `camara_org`). Model for a real integrator client. |
| `positioning-demo` | public | PKCE | Browser app for the 3D positioning visualization. Emits the `org` claim from the user's attribute, so the demo is scoped to the logged-in user's tenant. |
| `dashboard` | public | PKCE | Browser frontend of the operations dashboard. Tokens carry `dashboard-admin` or `dashboard-viewer` from the user's group, plus the `org` claim from the user's attribute (absent for the operator = god-mode). |
| `dashboard-readonly` | confidential | `client_credentials` | Headless read-only consumer (monitoring agent, public demo, CI smoke check). Tokens carry `dashboard-viewer` only. |
| `placement-editor-proxy` | confidential | authorization-code (oauth2-proxy) | Gates the no-auth `placement-editor` SPA. A `groups` protocol mapper emits group membership so oauth2-proxy admits `g-positioning-editors` (service-plane EDIT) or `g-dashboard-admins`. |

The camara/positioning/placement realm objects (the `camara-location-read`
role, `g-camara-users` group, the `camara-gateway`, `positioning-demo`, and
`placement-editor-proxy` clients, the camara service account, and the
`dashboard-admin` to `camara-location-read` composite) are emitted only when a
northbound phase is enabled (`testbed northbound on`), so an IAM-only deploy
creates no orphan clients. Keycloak imports the realm only on first boot, so
enabling northbound on an already-provisioned cluster does not auto-create the
`camara-gateway` and `positioning-demo` clients; re-import the realm or add them
via the admin console (see the realm reconcile limitation below). Exceptions
created/patched idempotently by phase 08 on every run so a running cluster
converges without a re-import: the `placement-editor-proxy` client (the front-door
gate cannot authenticate without it), the `camara-api-demo` client plus its
service-account `org` attribute and `camara-location-read` role, and the shared
`org` user-attribute mapper on the `dashboard` and `positioning-demo` clients.

## Roles → endpoint matrix (dashboard backend)

The dashboard backend validates the `Authorization: Bearer <jwt>` header
against the realm JWKS and inspects `realm_access.roles` to decide.
Enforcement lives in `dashboard/backend/app/auth.py` and is applied at
router-include time in `dashboard/backend/app/main.py`.

The matrix below documents the policy implemented today. Routers whose bulk is
read-only but which carry a few mutating routes apply the admin dependency on
those routes individually, listed under "Admin-only routes on viewer routers"
below.

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

Admin-only routes on viewer routers (each carries its own `require_admin`
dependency, so a viewer gets 403 on them):

| Route | Why |
|-------|-----|
| `POST /api/v1/deployments/{name}/restart` | restarts a workload |
| `POST /api/v1/deployments/{name}/scale` | can scale an NF to zero |
| `POST /api/v1/pods/amf-controllers/scale` | same, for the AMF controllers |
| `PATCH /api/v1/nf/{deployment}/log-level` | rewrites the NF ConfigMap and restarts it |
| `PUT /api/v1/configmaps/{name}` | direct ConfigMap write |
| `POST /api/v1/time/force-sync` | steps the clock on every VM |
| `PUT|DELETE /api/v1/ue/personalizations/{imsi}` | persists operator-authored data |

Diagnostics that a viewer may run, even though they are POSTs, because they
only probe and return a result: `POST /api/v1/network/health/run`,
`POST /api/v1/ue/test/ping`, `POST /api/v1/ue/test/iperf`. The viewer role is
meant for looking around a live testbed without breaking it, not for hiding it.

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
   origin. Under the single-base model they all derive from
   `external_base_domain` (dashboard at `kelt.<base>`, Keycloak under `/auth`);
   in LAN mode they fall back to the worker NodePorts. See
   [external-access.md](external-access.md).

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

Phase 08 seeds end users on first deploy so the role model and the tenancy
model are observable out of the box without prior Keycloak experience:

| Username | Group | Realm role | `org` attribute |
|----------|-------|------------|-----------------|
| `admin` | `g-dashboard-admins` | `dashboard-admin` | none (operator, sees all tenants) |
| `viewer` | `g-dashboard-viewers` | `dashboard-viewer` | none (operator, sees all tenants) |
| `demo` | `g-camara-users` + `g-dashboard-viewers` | `camara-location-read` + `dashboard-viewer` | `camara_org` (tenant-scoped) |

The `demo` account is the browser-login counterpart of the `camara-api-demo`
service account: same mechanism, one is a person and the other a machine. It is
created only when a northbound phase is enabled, since `g-camara-users` exists
only then. Its username comes from `camara_tenant_username` in `all.yml`
(override `DASHBOARD_BOOTSTRAP_TENANT_USERNAME`), and the dashboard IAM page
reads the same value.

Keycloak 26 validates user attributes against the realm declarative user
profile and drops undeclared ones, so phase 08 declares `org` in that profile
(admin view and edit only) before assigning it. Service accounts are not
subject to the profile, which is why the M2M path needed no such declaration.

The `admin` password is `dashboard_bootstrap_admin_password` (resolved from
`DASHBOARD_BOOTSTRAP_ADMIN_PASSWORD` or, when unset, `keycloak_admin_password`).
`viewer` and `demo` do not reuse it: they default to `kelt-viewer` and
`kelt-demo`, overridable with `DASHBOARD_BOOTSTRAP_VIEWER_PASSWORD` and
`DASHBOARD_BOOTSTRAP_TENANT_PASSWORD`. Those two accounts are the ones handed
out for a demo, while the operator password is often set to something short for
convenience.

Each seed account is created with `temporary: true`, which forces a password
reset at first login. Phase 08 reruns never overwrite a password the operator
changed via the Keycloak console, so changing a default above only affects
accounts that do not exist yet.

Additional users follow the same group-driven model:

```text
1. Admin console -> Users -> Add user
2. Set username and email
3. Credentials tab -> set temporary password
4. Groups tab -> join one of:
     g-camara-users
     g-dashboard-admins
     g-dashboard-viewers
     g-positioning-editors
5. Attributes tab -> set org = <tenant> (tenant users only; leaving it
   unset makes the account an operator that sees every tenant)
6. Save
```

Group membership transitively grants the realm role; no per-user role
assignment is required. The `org` attribute is orthogonal to the groups: the
groups decide what the account can do, `org` decides which tenant's assets it
sees.

## Retrieving tokens (M2M)

CAMARA gateway service account (operator, no `org` claim, sees all tenants):

```bash
curl -s -X POST \
  https://<keycloak-origin>/realms/5g-testbed/protocol/openid-connect/token \
  --data-urlencode grant_type=client_credentials \
  --data-urlencode client_id=camara-gateway \
  --data-urlencode client_secret=<camara_client_secret>
```

CAMARA API consumer (end-user path, `org` claim scopes it to its tenant):

```bash
curl -s -X POST \
  https://<keycloak-origin>/realms/5g-testbed/protocol/openid-connect/token \
  --data-urlencode grant_type=client_credentials \
  --data-urlencode client_id=camara-api-demo \
  --data-urlencode client_secret=<camara_api_demo_secret>
```

The resulting token carries `org=<camara_org>` (default `demo`), so a call to the
CAMARA Location API returns only that tenant's assets. This is the reference for
an integrator client: a new consumer client plus an `org` attribute on its service
account is a new tenant, no code change.

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
- [Phase 10: Northbound](https://github.com/Jacobbista/kelt/blob/main/ansible/phases/10-northbound/README.md): gateway JWT validation, the front-door gate
