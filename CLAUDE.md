# Agent Guidelines

This file is intended for AI agents and contributors working with this codebase. It documents conventions, architectural decisions, and constraints that are not always apparent from the code alone.

For end-user and agent operations (install, deploy, autostart, troubleshooting, full subcommand reference) see [QUICKSTART.md](QUICKSTART.md). This file (CLAUDE.md) covers contributor conventions only.

---

## Project Overview

A reproducible 5G cloud-edge testbed deployed via Vagrant + Ansible on a cluster of up to three nodes (master, worker, optional edge), brought up by a separate Ansible provisioning VM. The worker runs the Open5GS 5G core. The optional edge node runs KubeEdge EdgeCore with UERANSIM or a physical gNB. All 5G interfaces are carried over per-interface VXLAN overlays managed by OVS and Multus CNI.

The main entry point for users is `./testbed-config` (interactive TUI). `vagrant up` is the underlying mechanism.

---

## Ansible

### Phase Structure

Each deployment phase lives in `ansible/phases/0X-phase-name/` and contains:

```
0X-phase-name/
├── playbook.yml          # Phase entry point
├── README.md             # Implementation notes (not user-facing)
└── roles/
    └── role-name/
        ├── tasks/main.yml
        ├── defaults/main.yml
        └── templates/    # Jinja2 templates (.yaml.j2, .json.j2, .py.j2)
```

The main orchestrator is `ansible/phases/00-main-playbook.yml`, which imports all phases in order. When adding a new phase, register it there.

Phases fall into three classes, orthogonal to the maturity tiers in [docs/status.md](docs/status.md):

- **Core** (1 Infrastructure, 2 Kubernetes, 3 KubeEdge, 4 Overlay Network, 5 5G Core, 7 Observability, 8 IAM, 9 Dashboard): the mandatory backbone, always run. Together they are the reproducible software platform (SDN/NFV 5G core plus orchestration), deployable with no RAN hardware.
- **Optional addons** (6 UERANSIM & MEC, 10 CAMARA, 11 Positioning Engine, 12 Positioning Demo): not part of the minimal core, and never a prerequisite for it. A new optional phase must be toggleable by a dedicated `*_enabled` flag in `all.yml`, and the core must stay deployable with the addon disabled. (The current gating of the optional phases is inconsistent and is tracked for reconciliation in [docs/gaps.md](docs/gaps.md).)
- **Conditional toggles**: `edge_enabled` (edge node, default false) and `physical_ran_enabled` (physical femtocell vs simulated RAN, default false) change phase behavior rather than adding a phase.

Phase class (whether it runs by default) is independent of maturity tier (how validated it is). A Core phase may still ship Experimental features: Observability always runs, but its alerting and log dashboards are Experimental.

All shared variables belong in `ansible/group_vars/all.yml`. Do not hardcode IPs, versions, or image names in roles; reference variables defined in `all.yml`.

### Edge vs Worker

The testbed runs in two modes: with and without the edge VM. All code that touches the edge node must be gated:

```yaml
# In playbook host selection:
hosts: "masters,workers{{ ',edges' if (edge_enabled | default(false) | bool) else '' }}"

# In individual tasks:
when: edge_enabled | default(false) | bool
```

Worker nodes use the K3s containerd socket (`unix:///run/k3s/containerd/containerd.sock`). Edge nodes use standalone containerd (`unix:///run/containerd/containerd.sock`). Use the variables `worker_cri_endpoint` and `edge_cri_endpoint` defined in `all.yml`.

When a role behaves differently on edge vs worker, use `delegate_to: "{{ groups['workers'][0] }}"` and `delegate_to: "{{ groups['edges'][0] }}"` with explicit `when:` conditions. Do not mix edge and worker logic in the same task block.

### Kubectl

Inside VMs, always use `sudo k3s kubectl`, never plain `kubectl`. In Ansible tasks, use the `kubernetes.core` collection with `kubeconfig: "{{ kubeconfig_path }}"`.

### Task Naming

Task names use sentence case, verb-first. Qualify the target node when relevant:

```yaml
- name: Configure crictl on worker (k3s containerd)
- name: Configure crictl on edge (standalone containerd)
- name: Deploy gNB Services + Deployments
- name: Wait for gNB pods ready (per cell)
```

### Commenting

Any workaround or non-obvious decision in the code must have an inline comment referencing the relevant documentation:

```yaml
# KubeEdge workaround — see docs/known-issues/kubeedge-serviceaccount-token.md
automountServiceAccountToken: false

# DISABLED: CNI route conflict on edge node — see docs/known-issues/upf-edge-cni-route-conflict.md
replicas: 0
```

Use `# ====` section markers for major blocks in long task files.

### Idempotency

Every task must be safe to run multiple times without side effects. Use modules that are idempotent by design:

| Need | Idempotent module |
|------|-------------------|
| Write a file | `copy`, `template` |
| Insert a block into an existing file | `blockinfile` (uses `marker:` to identify and replace) |
| Create/update a directory | `file` with `state: directory` |
| Install packages | `apt` with `state: present` |
| Manage systemd units | `systemd` |
| Apply Kubernetes manifests | `kubernetes.core.k8s` |
| Reinstall language deps (`npm install`, `pip install -r`, etc.) | `command` with a `stat`-then-`when` guard comparing the manifest mtime (`package.json`, `requirements.txt`) to the install marker (`node_modules/.package-lock.json`, venv `pyvenv.cfg`). Running these unconditionally rewrites lockfile timestamps and can invalidate downstream watchers, see [Dashboard frontend](#dashboard-frontend). |

When `command` or `shell` is unavoidable, guard it with one of:
- `creates:` skips if a file already exists
- `changed_when: false` marks it as never changed (read-only probes)
- `changed_when: <condition>` for explicit change detection
- `when:` skips entirely if the precondition is not met

Never use `shell` to write files, install packages, or manage services when a dedicated module exists.

### Templates

Jinja2 templates are named `<component>-<resource-type>.yaml.j2` and live in `roles/<role>/templates/`. Use Jinja2 block comments (`{# ... #}`) to explain non-obvious template logic. Reference variables from `all.yml` or role defaults; do not hardcode values in templates.

After editing a role's defaults or removing a variable, dry-render every `*.j2` in that role's `templates/` with `StrictUndefined` against the role's `defaults/main.yml`. This surfaces stale references before they fail at deploy time.

---

## Dashboard frontend

The dashboard frontend lives in `dashboard/frontend/` (Vite + React 18, Tailwind, `oidc-client-ts` for PKCE). Two deploy targets coexist:

- Cluster pod baseline at the worker NodePort (`dashboard_cluster_enabled`, always on by default).
- Vite dev server on the ansible VM (`dashboard_dev_enabled`, opt-in).

Adding a new page requires four touch points:

1. Component file under `dashboard/frontend/src/pages/<Name>Page.jsx`.
2. Route entry in `dashboard/frontend/src/App.jsx` (both the `ROUTES` dict and a `<Route path=... element=... />` line).
3. Sidebar entry in `dashboard/frontend/src/components/Sidebar.jsx` `NAV_ITEMS` array. Set `adminOnly: true` to gate the nav button behind `dashboard-admin`; the array filter at render time already enforces it.
4. Backend router (if the page needs new endpoints) included in `dashboard/backend/app/main.py` with `dependencies=_viewer` or `dependencies=_admin`.

Frontend role gating reads from `useAuth().roles`. Backend gating is enforced at router-include time via FastAPI `Depends`. The role model is two-tier: `dashboard-admin` (writes, exec, sniffer, subscribers, NF rollout, restart) and `dashboard-viewer` (GET + log stream). See `docs/security/iam.md` for the per-route matrix.

Runtime configuration reaches the bundle through `dashboard/frontend/public/env-config.js`, populated at deploy time by `ansible/phases/09-dashboard/roles/dashboard_setup/templates/dashboard-frontend-configmap.yaml.j2` (cluster) or the `.env` file written to the source mount (dev). Read values via `env("VITE_*", "fallback")` from `dashboard/frontend/src/runtime-env.js`. Do not import `import.meta.env.*` directly; the wrapper falls back to it but checks `window.__ENV__` first so a single bundle works for both deploy targets.

When shipping a new bundle, bump `dashboard/frontend/package.json` `version` and tag the commit `dashboard-frontend-v<semver>`; CI builds `ghcr.io/jacobbista/dashboard-frontend:<semver>` and `:latest`. The cluster deployment uses `imagePullPolicy: Always` and gets a forced `rollout restart` from phase 09 reapply, so `:latest` always reflects the most recent tag.

Vite optimize cache caveat: any task that touches `package-lock.json` mtime (an unconditional `npm install`, for example) invalidates the pre-bundle cache and forces a `?v=<hash>` rotation on every dependency chunk URL. Tabs that loaded the previous hash end up with two copies of React in memory and crash with "Invalid hook call". Gate dependency installs on `package.json.mtime > node_modules/.package-lock.json.mtime` (see the pattern in `ansible/phases/09-dashboard/roles/dashboard_setup/tasks/main.yml`).

---

## Bash CLI conventions (testbed-config)

`testbed-config` is the operator entry point and the agent surface. Subcommand grammar follows `testbed <noun> [subnoun] [value]`. Each interactive flow has a matching positional CLI invocation; no flags.

When adding a new prompt or selector:

- Use `gum_choose_or_cancel` for pickers; it appends a `← Cancel` entry and treats Esc as cancel.
- After every terminal action in a submenu, call `prompt_continue` so the operator can read command output before the menu redraws.
- Submenus expose a `← Back` entry; the top-level menu intercepts Esc and asks for a second Esc to exit.

A bash function that drives a `gum` (or any TTY-bound) prompt must not be wrapped in `$(...)`. The capture redirects the function's stdout into a pipe, which detaches gum from `/dev/tty` and folds raw ANSI bytes into the caller's variable. Return values via a global variable, and emit every UI line on stderr (`gum style ... >&2`, `cat <<EOF >&2`). See `prompt_kc_reconcile` and its caller in `do_run_phase` for the canonical shape.

Persisted operator choices live in `.testbed.env` (config) and `.testbed.secrets` (sensitive). `load_config` initializes from defaults, then overrides from the env file. `save_config` rewrites the whole env file; add new variables to both functions and to the `env` subcommand output.

---

## KubeEdge Constraints

Before modifying anything that involves edge node workloads, read `docs/known-issues/` in full. Several non-obvious workarounds are implemented in the edge pod specs and CNI configuration; removing or changing them will break the edge deployment. Open issues and planned investigations are tracked in `docs/gaps.md`.

---

## Networking

Each 5G interface runs on a dedicated VXLAN overlay with its own VNI. Do not share overlays between interfaces. When adding a new interface:
1. Define the subnet and VNI in `ansible/group_vars/all.yml`
2. Create a Multus NetworkAttachmentDefinition in the appropriate phase
3. Add a row to `docs/architecture/5g-interfaces.md`

The primary CNI on edge uses `isDefaultGateway: false` deliberately; changing this will reintroduce the UPF-Edge route conflict.

---

## Documentation

### Tone and Format

- English only.
- Impersonal and factual. No "we", no "I", no "you should".
- No em-dashes in prose. Use commas or restructure the sentence.
- No editorial commentary ("Note that...", "Keep in mind...", "It is important to...").

### Documentation Map (ownership charter)

Every topic has ONE canonical document that owns it. Other documents link to the owner and never restate its content, especially concrete values (IPs, VNIs, ports, counts). This is what prevents the same fact drifting in two places.

**Entry points** (one per audience; they link out, they do not duplicate each other):

| Document | Audience | Owns |
|----------|----------|------|
| `README.md` | Anyone arriving | Pitch, status summary, stack, links out |
| `QUICKSTART.md` | Operators, agents | Install, deploy, daily ops, `testbed` subcommands |
| `CLAUDE.md` | Contributors, agents editing | Conventions and this map |
| `docs/README.md` | Anyone navigating | The index: links to every doc, grouped by area (must include Security) |

**Topic owners** (single source of truth; everyone else links, never restates):

| Topic | Owner |
|-------|-------|
| Interface matrix (subnets, static IPs, VXLAN VNIs) | `docs/architecture/5g-interfaces.md` |
| Node/VM topology and IPs | `docs/architecture/overview.md` |
| System design (layers, network, NFs, positioning) | `docs/architecture/` |
| Deployment phases and how to run them | `docs/deployment/phases.md` |
| External exposure and tunnels | `docs/security/external-access.md`, `docs/deployment/external-tunnel.md` |
| Dashboard architecture, access, security summary | `docs/dashboard/overview.md` |
| Dashboard modules | `docs/dashboard/modules.md` |
| Dashboard REST/WS endpoints | `docs/dashboard/api-reference.md` |
| IAM: roles, OIDC clients, per-route matrix | `docs/security/iam.md` |
| Diagnostics and troubleshooting | `docs/operations/troubleshooting.md` + `docs/runbooks/` |
| Feature maturity | `docs/status.md` |
| Future direction | `docs/roadmap.md` |
| Verified gaps and open bugs | `docs/gaps.md` |
| Platform limitations and workarounds | `docs/known-issues/` |
| Per-phase implementation notes | `ansible/phases/0X/README.md` |
| Test suites and how to run them | `docs/development/testing.md` |
| Coding standards and contribution workflow | `docs/development/contributing.md` |
| Operator quick-reference (consolidated IPs, ports, commands) | `docs/operations/handbook.md` (cheat-sheet only; links the owners above for detail) |

When documenting a fact, write it at its owner and link from elsewhere. Concrete values are referenced, never copy-pasted. `docs/README.md` must list every doc.

### Known Issues Format

Known-issue files document a KubeEdge or platform limitation and the solution implemented in this testbed. They are not debugging narratives. Structure:

1. One-sentence description of the platform behavior.
2. How the testbed handles it (the implemented solution).
3. Which files implement it (with paths).

Do not include: symptom logs, failed approaches, debugging steps.

### Gaps File

Every entry in `docs/gaps.md` must correspond to something verifiable: a file that does not exist, a feature that is disabled in the code, or a confirmed bug. Do not add speculative or aspirational entries.

### Feature Maturity

Every component is classified in [docs/status.md](docs/status.md), the canonical maturity matrix. The README carries a condensed summary only; `docs/status.md` is the source of truth.

Three tiers:

- **Supported**: deploys through the standard flow, is documented, and has been exercised end to end (thesis result, automated test, or manual validation). Reproducible on a clean install. Enabled by default.
- **Experimental**: code and manifests exist and deploy, but the path is not validated end to end, depends on an experimental component, or has no exercised use case. Often disabled by default (`replicas: 0` or opt-in).
- **Planned**: a documented direction with no working code yet.

Tier assignment rule (one-drop): a component is Supported only when all three conditions hold (deploys through the standard flow, documented, exercised end to end). If one condition fails it is Experimental. With no working code it is Planned. Abandoned code is removed, not tiered.

When adding or changing a feature, classify it in `docs/status.md` and record the evidence in the Validated by column. Do not promote a component to Supported without end-to-end validation.

---

## Common Commands

```bash
# Configure and deploy (interactive)
./testbed-config

# Deploy non-interactively
./testbed-config set-profile laptop && ./testbed-config up

# Verify cluster after deployment
vagrant ssh master
sudo k3s kubectl get nodes
sudo k3s kubectl get pods -n 5g

# Re-run a specific phase
vagrant ssh ansible
ansible-playbook ~/ansible-ro/phases/05-5g-core/playbook.yml -i ~/ansible-ro/inventory.ini

# Run tests (all suites; individual suites and details in docs/development/testing.md)
cd tests
make test
```

---

## Constraints Summary

- Never use plain `kubectl` inside VMs; always `sudo k3s kubectl`.
- Never hardcode IPs, versions, or image names in roles or templates.
- Always gate edge-specific code with `when: edge_enabled | default(false) | bool`.
- Always add a `# See docs/known-issues/...` comment when applying a KubeEdge workaround.
- Never add a new 5G interface without updating `docs/architecture/5g-interfaces.md` and `all.yml`.
- Never change `isDefaultGateway` on the edge primary CNI config.
- `automountServiceAccountToken: false` is required on all edge pod specs; do not remove it.
- Classify every feature in `docs/status.md` by maturity tier. Never mark a component Supported without end-to-end validation.
