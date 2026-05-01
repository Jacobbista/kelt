# Agent Guidelines

This file is intended for AI agents and contributors working with this codebase. It documents conventions, architectural decisions, and constraints that are not always apparent from the code alone.

---

## Project Overview

A reproducible 5G cloud-edge testbed deployed via Vagrant + Ansible on a 3-VM cluster (master, worker, edge). The worker runs the Open5GS 5G core. The edge node runs KubeEdge EdgeCore with UERANSIM or a physical gNB. All 5G interfaces are carried over per-interface VXLAN overlays managed by OVS and Multus CNI.

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

All shared variables belong in `ansible/group_vars/all.yml`. Do not hardcode IPs, versions, or image names in roles — reference variables defined in `all.yml`.

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

When `command` or `shell` is unavoidable, guard it with one of:
- `creates:` — skip if a file already exists
- `changed_when: false` — mark as never changed (read-only probes)
- `changed_when: <condition>` — explicit change detection
- `when:` — skip entirely if precondition is not met

Never use `shell` to write files, install packages, or manage services when a dedicated module exists.

### Templates

Jinja2 templates are named `<component>-<resource-type>.yaml.j2` and live in `roles/<role>/templates/`. Use Jinja2 block comments (`{# ... #}`) to explain non-obvious template logic. Reference variables from `all.yml` or role defaults — do not hardcode values in templates.

---

## KubeEdge Constraints

Before modifying anything that involves edge node workloads, read `docs/known-issues/` in full. Several non-obvious workarounds are implemented in the edge pod specs and CNI configuration — removing or changing them will break the edge deployment. Open issues and planned investigations are tracked in `docs/gaps.md`.

---

## Networking

Each 5G interface runs on a dedicated VXLAN overlay with its own VNI. Do not share overlays between interfaces. When adding a new interface:
1. Define the subnet and VNI in `ansible/group_vars/all.yml`
2. Create a Multus NetworkAttachmentDefinition in the appropriate phase
3. Add a row to `docs/architecture/5g-interfaces.md`

The primary CNI on edge uses `isDefaultGateway: false` deliberately — changing this will reintroduce the UPF-Edge route conflict.

---

## Documentation

### Tone and Format

- English only.
- Impersonal and factual. No "we", no "I", no "you should".
- No em-dashes in prose. Use commas or restructure the sentence.
- No editorial commentary ("Note that...", "Keep in mind...", "It is important to...").

### What Goes Where

| Content | Location |
|---------|----------|
| User-facing guides | `docs/` |
| Implementation notes for a phase | `ansible/phases/0X-phase-name/README.md` |
| Platform limitations and their solutions | `docs/known-issues/` |
| Verified missing documentation or open bugs | `docs/gaps.md` |
| Planned features and future direction | `docs/roadmap.md` |

### Known Issues Format

Known-issue files document a KubeEdge or platform limitation and the solution implemented in this testbed. They are not debugging narratives. Structure:

1. One-sentence description of the platform behavior.
2. How the testbed handles it (the implemented solution).
3. Which files implement it (with paths).

Do not include: symptom logs, failed approaches, debugging steps.

### Gaps File

Every entry in `docs/gaps.md` must correspond to something verifiable: a file that does not exist, a feature that is disabled in the code, or a confirmed bug. Do not add speculative or aspirational entries.

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

# Run tests
cd tests
make e2e
make protocols
```

---

## Constraints Summary

- Never use plain `kubectl` inside VMs — always `sudo k3s kubectl`.
- Never hardcode IPs, versions, or image names in roles or templates.
- Always gate edge-specific code with `when: edge_enabled | default(false) | bool`.
- Always add a `# See docs/known-issues/...` comment when applying a KubeEdge workaround.
- Never add a new 5G interface without updating `docs/architecture/5g-interfaces.md` and `all.yml`.
- Never change `isDefaultGateway` on the edge primary CNI config.
- `automountServiceAccountToken: false` is required on all edge pod specs — do not remove it.
