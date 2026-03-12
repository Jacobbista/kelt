# Phase 08 - Dashboard Control Plane

This phase prepares the out-of-band dashboard on the `ansible` VM.

## Run

```bash
vagrant ssh ansible
cd ~/ansible-ro
ansible-playbook phases/08-dashboard/playbook.yml
```

### Development mode (live reload from mounted source)

```bash
vagrant ssh ansible
cd ~/ansible-ro
ansible-playbook phases/08-dashboard/playbook.yml -e dashboard_mode=dev
```

## Result

- runtime source:
  - prod: `/home/vagrant/dashboard-work`
  - dev: `/vagrant/dashboard`
- backend dependencies installed in `/home/vagrant/.venvs/dashboard-backend`
- frontend dependencies installed in runtime `frontend/node_modules`
- systemd services created and started:
  - `dashboard-backend`
  - `dashboard-frontend`

Mode behavior:

- `dashboard_mode=prod` (default): copy/build from source, run stable services
- `dashboard_mode=dev`: run services directly from `/vagrant/dashboard` with reload/polling

## Service management

```bash
sudo systemctl status dashboard-backend dashboard-frontend
sudo systemctl restart dashboard-backend dashboard-frontend
```

## Endpoints

- UI: `http://192.168.56.13:31573`
- API: `http://192.168.56.13:31880`
