# AGENTS.md

KELT is a reproducible 5G cloud-edge testbed: Vagrant provisions up to three VMs
(master, worker, optional edge), Ansible deploys K3s, KubeEdge, an Open5GS 5G
core, and an operations dashboard on top of per-interface VXLAN overlays.

`./testbed-config` drives everything. It carries two interfaces over one
implementation: an interactive TUI, which is how people are meant to operate the
testbed, and positional subcommands, which are the surface for agents, scripts,
and CI. Every menu action has a matching subcommand, so nothing needs a prompt
to be reached. Use the subcommands.

## Before assuming anything, read the owner document

Every topic in this project has exactly one document that owns it. Facts are
written at the owner and linked from everywhere else, so an answer reconstructed
from code or from memory is likely to be a stale copy.

Start at [docs/README.md](docs/README.md). It indexes every document and carries
the ownership map that says which document owns which topic. Read the owner
first, then check the code only to confirm the document is not out of date. When
the code and its owner document disagree, say so instead of picking one.

`docs/status.md` says how validated a component is, `docs/gaps.md` lists
confirmed gaps and bugs, and `docs/known-issues/` explains platform limitations
whose workarounds are load-bearing.

## Commands

```bash
./testbed-config              # interactive TUI: configure, deploy, operate
./testbed-config up           # deploy non-interactively
./testbed-config run-phase    # re-run a single deployment phase
./testbed-config endpoints    # print the reachable URLs
cd tests && make test         # run the automated suites
```

Prefer these over driving `vagrant` and `ansible-playbook` by hand: the CLI
loads the operator's saved configuration and secrets, which a raw playbook run
does not. The full subcommand reference is in `QUICKSTART.md`.

Inside a VM, Kubernetes is K3s: use `sudo k3s kubectl`, never plain `kubectl`.

## Hard constraints

Breaking any of these breaks a deployment in a way that is slow to diagnose.

- Never hardcode an IP, version, port, or image name in a role, template, or
  source file. Declare it once in `ansible/group_vars/all.yml` and reference it.
- Gate every edge-specific task on the edge toggle; the testbed must stay
  deployable with no edge node.
- `automountServiceAccountToken: false` is required on edge pod specs, and the
  edge primary CNI keeps `isDefaultGateway: false`. Both are documented
  workarounds. Read `docs/known-issues/` before touching either.
- Every Ansible task must be safe to run again. Prefer a module that is
  idempotent by design over `shell`.
- A new 5G interface needs its subnet and VXLAN VNI in `all.yml` and a row in
  the interface matrix document.
- Classify every feature in `docs/status.md`. Nothing becomes Supported without
  end-to-end validation.
- Annotate any workaround inline with a link to the document that explains it.

## Conventions

Coding standards, phase layout, template rules, documentation tone, and the
commit and release workflow live in
[docs/development/contributing.md](docs/development/contributing.md). Read it
before editing Ansible, the dashboard, or the CLI.

Commits follow Conventional Commits with a one-line subject. Do not add
co-author trailers.
