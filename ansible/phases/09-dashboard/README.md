# Phase 09 - Dashboard Control Plane

This phase provisions the out-of-band 5G testbed dashboard. The backend
always runs on the `ansible` VM as a systemd service. The frontend has
two deployment targets, selected by a single mode flag:

| `dashboard_mode` | Frontend runs in | Use case |
|------------------|------------------|----------|
| `dev` | Vite dev server on the `ansible` VM (systemd `dashboard-frontend`), port 31573 | Local frontend development with hot reload |
| `prod` | nginx pod on the worker pulling `ghcr.io/jacobbista/dashboard-frontend`, NodePort 31573 | Stable deployment exposed via tunnel or reverse proxy; survives `ansible` VM reboots |

The two targets are mutually exclusive. Switching modes brings up the
selected target and tears down the other (Vite systemd stopped+disabled
when `prod`; cluster Deployment/Service/ConfigMap deleted when `dev`).
The `dashboard` namespace is left in place across switches.

The backend is the same uvicorn service in both modes. The cluster pod
reverse-proxies `/api`, `/health`, `/watchdog` to the ansible VM backend
and `/auth` to Keycloak on the worker NodePort, so the browser sees a
single origin.

## Run

The mode is sourced from `DASHBOARD_MODE` in `.testbed.env` (managed by
`./testbed-config`) or from `-e dashboard_mode=<value>`. Defaults to
`prod`.

```bash
./testbed-config run-phase 09-dashboard
```

Switch on the ansible VM with the bashrc helpers (after the phase has
run at least once):

```bash
vagrant ssh ansible
dev    # switch to dev mode (Vite on ansible VM)
prod   # switch to prod mode (cluster pod)
```

Or invoke the playbook directly:

```bash
ansible-playbook phases/09-dashboard/playbook.yml -e dashboard_mode=dev
ansible-playbook phases/09-dashboard/playbook.yml -e dashboard_mode=prod
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
| Frontend in dev mode | `http://<ansible-vm>:31573` |
| Frontend in prod mode | `http://<worker>:31573` |

External access (tunnel, reverse proxy, ssh -L) is operator-specific.
See [docs/security/external-access.md](../../../docs/security/external-access.md)
for the `DASHBOARD_EXTERNAL_ORIGIN` variable and patterns.

## Service management

Backend (always running, regardless of mode):

```bash
sudo systemctl status dashboard-backend dashboard-watchdog
sudo systemctl restart dashboard-backend
```

Dev mode frontend (Vite on the ansible VM):

```bash
sudo systemctl status dashboard-frontend
```

Prod mode frontend (cluster pod):

```bash
sudo k3s kubectl -n dashboard get pods
sudo k3s kubectl -n dashboard logs deploy/dashboard-frontend
sudo k3s kubectl -n dashboard rollout restart deploy/dashboard-frontend
```
