# 5G Dashboard (Out-of-Band)

This dashboard adds a dedicated management plane for the testbed without deploying additional workloads in namespace `5g`.

## Why this hosting model

Backend and frontend run on the `ansible` VM by design:

- avoids contention with 5G runtime pods (AMF/SMF/UPF, etc.)
- keeps blast radius low for control-plane UI errors
- mirrors professional private/proprietary environments where control surfaces are separated from production data/control plane

## Structure

- `backend/`: FastAPI service (Kubernetes + OVS adapters, WebSockets, guardrails)
- `frontend/`: React + Tailwind + React Flow + xterm.js UI
- `run-backend.sh`: local backend launcher
- `run-backend-watch.sh`: backend with auto-restart on crash (for manual runs)
- `run-frontend.sh`: local frontend launcher

## Module Coverage

### Module 1: Control Room (implemented)

- live pod monitor for namespace `5g`
- deployment restart action (admin token required)
- log streaming over WebSocket (`kubectl logs -f` equivalent)
- ConfigMap API:
  - read supported
  - write path implemented but disabled by default (`DASHBOARD_ALLOW_CONFIGMAP_WRITE=false`)

### Module 2: Topology Map (implemented)

- React Flow topology
- pod nodes + OVS bridge nodes
- edge metadata from Multus network-status (IPs, MAC, MTU, interface)
- OVS flow inspection (`ovs-ofctl dump-flows`) from bridge inspector

### Module 3: Subscriber Management (implemented)

- full CRUD for Open5GS subscribers via MongoDB
- "Initialize from playbook" to import default subscribers via Ansible

### Module 4: UE Monitoring (implemented)

- real-time summary from AMF/SMF Prometheus metrics (gNBs, RAN UEs, sessions, registrations)
- log-parsed UE event feed (registration, PDU session establishment, gNB attach)
- active UE table with IMSI, status, PDU session IPs, DNN
- connectivity tests from UERANSIM UE pods (ping, iperf3)
- manual command hints for physical UE dongles

### Module 5: Metrics (implemented)

- node-level CPU, memory, disk from Prometheus/node-exporter
- NF-level CPU, memory, restart counts
- time-series charts

### Module 6: Physical RAN Config (implemented)

- bridge interface detection on worker
- OVS bridge + NAD creation via Ansible playbooks
- AMF Multus annotation patching via K8s API
- vagrant command generation for host bridging

### Module 7: Network Health & Traffic Observer (implemented)

- per-interface connectivity checks (N2/N3/N4/N6) via in-pod `kubectl exec` probes
- real-time OVS bridge traffic counters (PPS/throughput) streamed over WebSocket
- interface health cards with status, latency, live PPS/Bps
- animated data path diagram (UE -> gNB -> AMF -> SMF -> UPF -> DN) reflecting active traffic
- live traffic animation on the Logical Topology view (edges animate and scale with PPS)
- on-demand "Run Health Check" button for immediate connectivity verification

### Future hooks (stubbed)

- `POST /api/v1/experiments/run` placeholder
- `POST /api/v1/snapshot/create` placeholder

## Security and Guardrails

- read operations are open by default
- mutating operations require `Authorization: Bearer <DASHBOARD_ADMIN_TOKEN>`
- OVS shell operations are restricted by allowlist:
  - `sudo ovs-vsctl list-br`
  - `sudo ovs-vsctl list-ports <bridge>`
  - `sudo ovs-ofctl dump-flows <bridge>`
- remote command timeout and output size cap enforced
- mutating actions are audit-logged to `backend/logs/audit.log` (NDJSON)

## Runtime prerequisites

On `ansible` VM:

- SSH alias `worker` available (already configured by provisioning)
- `kubectl` and kubeconfig available
- Node/npm installed for frontend

## Runtime mode

The dashboard is deployed and started automatically by Phase 8.

### Prepare/reconcile via dedicated phase

```bash
cd ~/ansible-ro
ansible-playbook phases/08-dashboard/playbook.yml
```

### Development mode (live reload)

```bash
cd ~/ansible-ro
ansible-playbook phases/08-dashboard/playbook.yml -e dashboard_mode=dev
```

Mode behavior:

- `prod` (default): services run from `/home/vagrant/dashboard-work`
- `dev`: services run from `/vagrant/dashboard` with backend/frontend reload enabled

Access:

- UI: `http://<ansible-ip>:31573`
- API docs: `http://<ansible-ip>:31880/docs`

Systemd services:

```bash
sudo systemctl status dashboard-backend
sudo systemctl status dashboard-frontend
sudo systemctl restart dashboard-backend dashboard-frontend
```

### Resilience

- **With systemd** (Ansible deploy): `Restart=always` and `RestartSec=3` — backend auto-restarts on crash.
- **Manual run**: use `./run-backend-watch.sh` for a loop that restarts the process every 3 seconds if it exits.
- **Frontend**: when backend is unreachable, a banner "Backend unreachable — reconnecting…" with Retry button is shown; polling detects when it comes back online.

## Backend environment

Copy `backend/.env.example` to `backend/.env` and adapt if needed.

Minimum recommended values:

```env
DASHBOARD_KUBECONFIG_PATH=/home/vagrant/.kube/config
DASHBOARD_WORKER_SSH_HOST=worker
DASHBOARD_ADMIN_TOKEN=<strong-random-token>
DASHBOARD_ALLOW_CONFIGMAP_WRITE=false
```

## API quick reference

- `GET /health`
- `GET /api/v1/pods?namespace=5g`
- `GET /api/v1/pods/{pod}/describe?namespace=5g`
- `POST /api/v1/deployments/{deployment}/restart` (admin)
- `GET /api/v1/configmaps/{name}?namespace=5g`
- `PUT /api/v1/configmaps/{name}?namespace=5g` (admin + feature flag)
- `WS /api/v1/ws/logs/{namespace}/{pod}?container=<name>`
- `GET /api/v1/topology?namespace=5g`
- `GET /api/v1/ovs/bridges/{bridge}/flows`
- `GET /api/v1/network/nads?namespace=5g`
- `GET /api/v1/subscribers` / `POST` / `PUT /{imsi}` / `DELETE /{imsi}`
- `POST /api/v1/subscribers/init`
- `GET /api/v1/ue/summary` (Prometheus-backed gauges/counters)
- `GET /api/v1/ue/events?minutes=10` (AMF/SMF log-parsed events)
- `GET /api/v1/ue/active` (active UE list from log reconstruction)
- `GET /api/v1/ue/pods` (UERANSIM UE pods)
- `POST /api/v1/ue/test/ping` / `POST /api/v1/ue/test/iperf`
- `GET /api/v1/metrics/nodes` / `GET /api/v1/metrics/nf`
- `GET /api/v1/ran/status` / `POST /api/v1/ran/enable` / `POST /api/v1/ran/disable`
- `GET /api/v1/network/health` (cached N-interface connectivity results)
- `POST /api/v1/network/health/run` (trigger immediate health check)
- `WS /api/v1/ws/traffic/intensity` (real-time OVS bridge counter deltas)
