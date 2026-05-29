# Phase 09 - Dashboard Control Plane

This phase provisions the out-of-band 5G testbed dashboard. The backend
always runs on the `ansible` VM as a systemd service. The frontend has
two deployment targets that can coexist: a Vite-based service on the
`ansible` VM, and an nginx pod in the cluster pulling a prebuilt image
from GHCR.

## Targets

| Target | Where | When to use |
|--------|-------|-------------|
| Backend | `ansible` VM systemd | Always; provides cluster API access for the dashboard |
| Frontend (ansible VM) | `ansible` VM systemd, port 31573 | Local development with hot reload (`dashboard_mode=dev`), or a live-rebuild preview (`dashboard_mode=prod` runs `npm run preview` on the built bundle) |
| Frontend (cluster) | nginx pod on the worker, NodePort 31573 | Production-style deployment exposed via tunnel or reverse proxy; survives `ansible` VM reboots, pulled as a versioned image from `ghcr.io/jacobbista/dashboard-frontend` |

The cluster pod is additive and disabled by default. Set
`dashboard_cluster_enabled=true` (or the `DASHBOARD_CLUSTER_ENABLED`
environment variable) to provision it. The ansible VM frontend is
controlled by the existing `dashboard_mode` variable and is unaffected
by the cluster toggle.

## Run

Default (backend + ansible VM frontend, no cluster pod):

```bash
vagrant ssh ansible
cd ~/ansible-ro
ansible-playbook phases/09-dashboard/playbook.yml
```

Development mode on the ansible VM (Vite dev server with HMR):

```bash
ansible-playbook phases/09-dashboard/playbook.yml -e dashboard_mode=dev
```

Add the cluster pod alongside the existing setup:

```bash
ansible-playbook phases/09-dashboard/playbook.yml -e dashboard_cluster_enabled=true
```

Production-style deploy with cluster pod and stable preview on the
ansible VM:

```bash
ansible-playbook phases/09-dashboard/playbook.yml \
  -e dashboard_mode=prod \
  -e dashboard_cluster_enabled=true
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
| Frontend on ansible VM | `http://<ansible-vm>:31573` |
| Frontend in cluster | `http://<worker>:31573` |

External access (tunnel, reverse proxy, ssh -L) is operator-specific.
See [docs/security/external-access.md](../../../docs/security/external-access.md)
for the `DASHBOARD_EXTERNAL_ORIGIN` variable and patterns.

## Service management

Ansible VM services:

```bash
sudo systemctl status dashboard-backend dashboard-frontend
sudo systemctl restart dashboard-backend dashboard-frontend
```

Cluster pod:

```bash
sudo k3s kubectl -n dashboard get pods
sudo k3s kubectl -n dashboard logs deploy/dashboard-frontend
sudo k3s kubectl -n dashboard rollout restart deploy/dashboard-frontend
```

## Mode switcher (ansible VM only)

A bashrc helper provides shorthand commands once the playbook has run:

```bash
prod   # switch ansible VM frontend to npm run preview (built bundle)
dev    # switch ansible VM frontend to npm run dev (HMR)
```

The cluster pod state is independent and toggled only via
`dashboard_cluster_enabled` at playbook time.
