# Phase 09 - Dashboard Control Plane

This phase provisions the out-of-band 5G testbed dashboard. The backend
always runs on the `ansible` VM as a systemd service. The frontend has
two deployment targets controlled by two independent flags, not a single
mutually exclusive mode.

## Targets

| Flag | Default | Frontend runs in |
|------|---------|------------------|
| `dashboard_cluster_enabled` | `true` | nginx pod on the worker pulling `ghcr.io/jacobbista/dashboard-frontend`, NodePort 31573. Baseline production frontend, always provisioned unless explicitly disabled. |
| `dashboard_dev_enabled` | `false` | Vite dev server on the `ansible` VM (systemd `dashboard-frontend`), port 31573. Opt-in extra for live frontend development with hot reload. |

The two targets are independent and coexist on different IPs (worker
NodePort vs ansible VM port), so no port conflict. The cluster pod is
always the stable target; the dev frontend can be turned on or off
without affecting it.

The backend uvicorn service is the same in both cases. The cluster pod
reverse-proxies `/api`, `/health`, `/watchdog` to the ansible VM backend
and `/auth` to Keycloak on the worker NodePort, so the browser sees a
single origin.

## Run

The dev frontend toggle is sourced from `DASHBOARD_DEV_ENABLED` in
`.testbed.env` (managed by `./testbed-config`) or from
`-e dashboard_dev_enabled=<value>`. The cluster pod toggle is sourced
from `DASHBOARD_CLUSTER_ENABLED` (env or `-e dashboard_cluster_enabled`)
and defaults to `true`.

```bash
./testbed-config run-phase 09-dashboard
```

Toggle the dev frontend from the interactive menu (`Configure
auth/network` -> `Toggle dashboard dev frontend`) or with the CLI
shortcut:

```bash
./testbed-config dashboard-dev true
./testbed-config dashboard-dev false
```

Or invoke the playbook directly (fallback):

```bash
ansible-playbook phases/09-dashboard/playbook.yml -e dashboard_dev_enabled=true
ansible-playbook phases/09-dashboard/playbook.yml -e dashboard_cluster_enabled=false
```

## Image

The cluster pod pulls `ghcr.io/jacobbista/dashboard-frontend:<tag>`,
built by `.github/workflows/dashboard-frontend.yml` in this repository
when a tag matching `dashboard-frontend-v*` is pushed. The image is
generic: all runtime configuration (Keycloak authority, dev-mode
external URL, reverse-proxy targets) is supplied at deploy time via the
ConfigMap mounted at `/etc/nginx/conf.d/default.conf` and
`/usr/share/nginx/html/env-config.js`. No operator-specific values are
baked into the image.

## Endpoints

| Path | URL (LAN) |
|------|-----------|
| Backend API | `http://<ansible-vm>:31880` |
| Cluster frontend | `http://<worker>:31573` |
| Dev frontend (when enabled) | `http://<ansible-vm>:31573` |

External access (tunnel, reverse proxy, ssh -L) is operator-specific.
See [docs/security/external-access.md](../../../docs/security/external-access.md)
for the `DASHBOARD_EXTERNAL_ORIGIN` variable and patterns.

## Service management

Backend (always running):

```bash
sudo systemctl status dashboard-backend dashboard-watchdog
sudo systemctl restart dashboard-backend
```

Dev frontend (only present when `dashboard_dev_enabled=true`):

```bash
sudo systemctl status dashboard-frontend
sudo systemctl start dashboard-frontend
sudo systemctl stop dashboard-frontend
```

The backend has scoped sudoers rules to start, stop, and probe the dev
frontend systemd unit, so the prod UI can toggle it through
`/api/v1/dev-frontend/{status,enable,disable}` without granting
arbitrary privileges.

Cluster frontend:

```bash
sudo k3s kubectl -n dashboard get pods
sudo k3s kubectl -n dashboard logs deploy/dashboard-frontend
sudo k3s kubectl -n dashboard rollout restart deploy/dashboard-frontend
```
