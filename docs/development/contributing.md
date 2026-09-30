# Contributing Guide

Conventions for changing this codebase. The short entry point for agents and new
contributors is [AGENTS.md](https://github.com/Jacobbista/kelt/blob/main/AGENTS.md);
this document holds the detail it points to.

Before writing a fact into any document, find its owner in the
[Documentation Map](../README.md#documentation-map). Facts are written once, at
the owner, and linked from everywhere else.

---

## Ansible

### Phase structure

Each deployment phase lives in `ansible/phases/0X-phase-name/` and contains a
`playbook.yml` entry point, a `README.md` with implementation notes (not
user-facing), and its roles under `roles/<role-name>/` with the usual
`tasks/`, `defaults/`, `templates/`, `handlers/` layout.

The orchestrator `ansible/phases/00-main-playbook.yml` imports every phase in
order. A new phase is registered there, documented in
[deployment/phases.md](../deployment/phases.md), and given tests under `tests/`.

Phases fall into three classes, orthogonal to the maturity tiers in
[status.md](../status.md):

- **Core**: the mandatory backbone, always run. Together they are the
  reproducible software platform, deployable with no RAN hardware.
- **Optional addons**: not part of the minimal core and never a prerequisite for
  it. A new optional phase must be toggleable by its own `*_enabled` flag in
  `all.yml`, and the core must stay deployable with the addon disabled.
- **Conditional toggles**: flags that change phase behavior rather than adding a
  phase, such as the edge node and physical-RAN toggles.

Which phases belong to which class is listed in
[deployment/phases.md](../deployment/phases.md).

Phase class (whether it runs by default) is independent of maturity tier (how
validated it is). A Core phase may still ship Experimental features.

All shared variables belong in `ansible/group_vars/all.yml`. Do not hardcode
IPs, versions, or image names in roles.

`all.yml` holds the testbed's plan: subnets, addresses, versions, images, and
the ports the testbed exposes. The ports an NF implementation listens on (SBI,
NGAP, its management API) are a property of that implementation, not of the
plan: they are declared once where the NF is wired, in
`ansible/phases/05-5g-core/roles/nf_deployments/defaults/main.yml`, which names
them on the pod (`sbi`, `ngap`, `metrics`). Code that needs one reads it by
name from the running pod (`k8s_service.pod_port`, or a port name passed to
`nf_api_get`), so wiring another core changes only that file. NGAP's 38412 is
the standard port (3GPP TS 38.412) and is read the same way.

### Component image versions

Every container image tag is defined once in `all.yml` and referenced from
roles, templates, and the frontend. Never hardcode a tag in a role default, a
`.j2`, or a source file.

The companion images use a baseline-plus-override model: a committed registry
and tag map in `all.yml`, and a live override so a phase re-run keeps an
operator-rolled image instead of downgrading it. An override entry must never
pin below the baseline, which reintroduces the downgrade this model removes.
Filtered CI advances each companion image independently, so each carries its own
tag and there is no shared release tag. To bump a version, edit the tag map in
`all.yml` only.

### Edge vs worker

The testbed runs with and without the edge VM, so all code touching the edge
node must be gated, both in playbook host selection and in individual tasks:

```yaml
hosts: "masters,workers{{ ',edges' if (edge_enabled | default(false) | bool) else '' }}"

when: edge_enabled | default(false) | bool
```

Worker nodes use the K3s containerd socket; edge nodes use standalone
containerd. Use the `worker_cri_endpoint` and `edge_cri_endpoint` variables from
`all.yml` rather than writing a socket path.

When a role behaves differently on edge and worker, delegate explicitly to the
relevant host group with its own `when:`. Do not mix edge and worker logic in
one task block.

### Kubectl

Inside VMs, always `sudo k3s kubectl`, never plain `kubectl`. In Ansible tasks,
use the `kubernetes.core` collection with `kubeconfig: "{{ kubeconfig_path }}"`.

### Task naming

Sentence case, verb first, qualifying the target node when relevant:

```yaml
- name: Configure crictl on worker (k3s containerd)
- name: Configure crictl on edge (standalone containerd)
- name: Wait for gNB pods ready (per cell)
```

### Commenting

Any workaround or non-obvious decision must carry an inline comment pointing at
the document that explains it:

```yaml
# KubeEdge workaround — see docs/known-issues/kubeedge-serviceaccount-token.md
automountServiceAccountToken: false
```

Use `# ====` section markers for major blocks in long task files.

### Idempotency

Every task must be safe to run repeatedly. Use modules that are idempotent by
design:

| Need | Idempotent module |
|------|-------------------|
| Write a file | `copy`, `template` |
| Insert a block into an existing file | `blockinfile` (uses `marker:`) |
| Create/update a directory | `file` with `state: directory` |
| Install packages | `apt` with `state: present` |
| Manage systemd units | `systemd` |
| Apply Kubernetes manifests | `kubernetes.core.k8s` |
| Reinstall language deps (`npm install`, `pip install -r`) | `command` with a `stat`-then-`when` guard comparing manifest mtime to the install marker |

That last row matters: running dependency installs unconditionally rewrites
lockfile timestamps and can invalidate downstream watchers, see
[Dashboard frontend](#dashboard-frontend).

When `command` or `shell` is unavoidable, guard it with `creates:`,
`changed_when: false` for read-only probes, an explicit `changed_when:`, or a
`when:` precondition. Never use `shell` to write files, install packages, or
manage services when a dedicated module exists.

### Pieces

A piece is one named, idempotent part of a phase: a phase playbook plus the tags
that select it. Pieces are how the dashboard changes the testbed, and the CLI runs
the same ones, so an action behaves the same from both sides.

- **Registry**: `ansible/pieces.yml`. Each piece has a `title`, `runs` (a list of
  `{phase, tags}` run in order), optional fixed `extra_vars` (never taken from a
  request), optional `set_env` (keys written to `.testbed.env` before the run),
  a `tier` (`change`: brings the testbed to the state it should already be in;
  `disrupt`: interrupts traffic or removes something, and must say what it
  `stops`), `changes`, `takes_s`, an optional `check`, a read-back the
  dashboard runs after the playbooks exit 0, an optional `lock` (a group: pieces
  that share it never run at once, e.g. the RAN pieces share `ran`), and an
  optional `confirm_word` (a word the dashboard makes the admin type before a
  piece that cuts devices off, e.g. `detach`).
- **Runner**: `ansible/tools/kelt-piece`, on the ansible VM. It loads
  `.testbed.env` and `.testbed.secrets` the way `kelt run-phase` does, holds one
  lock per piece or lock group (a second start of the same piece gets the
  running one's id, another piece of the group is refused with the running
  one's name; the lock also tells whether a run is still alive), and writes a record
  per run to `~/.kelt/operations/` (private to the user: `<id>.json`, and the playbook output as
  `<id>.log.gz`). Records older than `KELT_OPS_MAX_AGE_DAYS` (30) or beyond
  `KELT_OPS_MAX_MB` (50 MB in total) are pruned after each run; a running one
  never is. Tests: `python3 -m unittest test_kelt_piece` in `ansible/tools/`.
- **CLI**: `kelt run-piece <piece>`, `kelt operations [pieces|show <id>|<state>]`.
  A run started from the dashboard runs as its own transient system unit
  (`kelt-piece-<id>`), so restarting the dashboard backend does not stop it.
- **Dashboard**: `GET /api/v1/pieces`, `POST /api/v1/pieces/<name>/run`,
  `GET /api/v1/operations[/<id>]`, `GET`/`PUT /api/v1/operations/retention`
  (admin). "Done" means the piece's check passed, not only that the playbooks
  exited 0. In the UI, `PieceButton` shows the confirmation from the registry;
  the header's Operations button and the Operations page show the runs; the
  limits are editable in Settings → Operations record. `kelt-piece usage` and
  `kelt-piece retention [--max-age-days N] [--max-mb M]` report and set them.

To add a piece: put its tasks in a play of the phase with the tags
`[<piece>, never]` (`never` keeps a whole-phase run from executing it), make
every task safe to run again, add the entry to `pieces.yml`, and, if the
dashboard should confirm the result, a check in
`dashboard/backend/app/routers/pieces.py`.

### Templates

Jinja2 templates are named `<component>-<resource-type>.yaml.j2` and live in
`roles/<role>/templates/`. Use Jinja2 block comments (`{# ... #}`) to explain
non-obvious logic, and reference variables rather than hardcoding values.

**Never indent a `{# ... #}` comment inside YAML structure.** Ansible renders
with `trim_blocks=True`, which removes the newline after `#}`. The comment's own
leading whitespace then merges with the following line, pushing it deeper than
its parent key, and the manifest fails to parse with "mapping values are not
allowed here". Put the comment at column 0, or use a plain YAML `#` comment,
which survives into the rendered manifest and reads fine there. Indented Jinja
comments are only safe inside a literal block scalar whose content is not YAML
(an embedded script, say), where stray leading spaces do not change meaning.

After editing a role's defaults or removing a variable, dry-render every `*.j2`
in that role with `StrictUndefined` against the role's `defaults/main.yml`. This
surfaces stale references before they fail at deploy time.

---

## Dashboard frontend

The frontend lives in `dashboard/frontend/` (Vite + React, Tailwind,
`oidc-client-ts` for PKCE). Two deploy targets coexist: a cluster pod at the
worker NodePort, and an opt-in Vite dev server on the ansible VM.

How pages behave and look (tiers and colours, confirmations, the loader, data,
operations) is set by [dashboard-design.md](dashboard-design.md); its rules are
binding for any page that is added or redesigned.

Adding a page requires four touch points:

1. A component file under `src/pages/`.
2. A `<Route>` line in `src/App.jsx`, wrapped in `AdminOnly` when its backend
   routes are admin-only.
3. An entry in `src/navigation.js`: in a sidebar group, or in `SUBPAGES` with
   its parent for a page reached from another. Set `adminOnly: true` to hide
   the sidebar entry from viewers. The sidebar, the route ids and the header's
   breadcrumb all read this file.
4. A backend router, if the page needs new endpoints, included in
   `dashboard/backend/app/main.py` with the viewer or admin dependency.

Frontend role gating reads from `useAuth().roles`. Backend gating is enforced at
router-include time via FastAPI `Depends`, and per-route on mixed routers. The
role model and its per-route matrix are owned by
[security/iam.md](../security/iam.md).

Runtime configuration reaches the bundle through `public/env-config.js`,
populated at deploy time by the phase-09 configmap template (cluster) or the
`.env` written to the source mount (dev). Read values via `env()` from
`src/runtime-env.js`. Do not import `import.meta.env.*` directly: the wrapper
checks `window.__ENV__` first, which is what lets one bundle serve both targets.

When shipping a new bundle, bump the frontend `package.json` version and tag the
commit, see [Publishing images](#publishing-images-what-needs-a-tag).

**Vite optimize cache caveat.** Any task that touches `package-lock.json` mtime
invalidates the pre-bundle cache and forces a `?v=<hash>` rotation on every
dependency chunk URL. Tabs holding the previous hash end up with two copies of
React and crash with "Invalid hook call". Gate dependency installs on the
manifest mtime, as the phase-09 tasks do.

---

## Bash CLI conventions (testbed-config)

`testbed-config` carries two interfaces over one implementation, and both are
first-class. The interactive TUI is the intended way to operate the testbed: it
shows state before asking, and confirms destructive actions. The positional
subcommands serve direct terminal control, CI, and agents that read the
repository and act on their own.

That makes parity a hard rule, not a nicety: **every interactive flow must have a
matching positional invocation**, because an agent cannot drive a prompt. A
capability reachable only through a menu is a bug. Subcommand grammar is
`testbed <noun> [subnoun] [value]`; no flags, values are positional.

Both paths must call the same underlying function and read and write the same
persisted state, so an operator can mix them within a session. See
[QUICKSTART](https://github.com/Jacobbista/kelt/blob/main/QUICKSTART.md), which
owns the subcommand reference.

When adding a prompt or selector:

- Use `gum_choose_or_cancel` for pickers; it appends a cancel entry and treats
  Esc as cancel.
- Call `prompt_continue` after every terminal action in a submenu, so the
  operator can read command output before the menu redraws.
- Give submenus a back entry; the top-level menu asks for a second Esc to exit.

A function driving a `gum` (or any TTY-bound) prompt must not be wrapped in
`$(...)`. The capture redirects its stdout into a pipe, detaching gum from
`/dev/tty` and folding raw ANSI bytes into the caller's variable. Return values
through a global variable and emit every UI line on stderr. `prompt_kc_reconcile`
and its caller in `do_run_phase` are the canonical shape.

Persisted operator choices live in `.testbed.env` (config) and
`.testbed.secrets` (sensitive). `load_config` initializes from defaults then
overrides from the env file; `save_config` rewrites the whole file. A new
variable goes in both functions and in the `env` subcommand output.

---

## Networking

Each 5G interface runs on a dedicated VXLAN overlay with its own VNI. Do not
share overlays between interfaces. Adding an interface means:

1. Define the subnet and VNI in `ansible/group_vars/all.yml`.
2. Create a Multus NetworkAttachmentDefinition in the appropriate phase.
3. Add a row to [architecture/5g-interfaces.md](../architecture/5g-interfaces.md),
   which owns the matrix.

The primary CNI on edge uses `isDefaultGateway: false` deliberately. Changing it
reintroduces the UPF-Edge route conflict documented in `known-issues/`.

---

## KubeEdge constraints

Before modifying anything involving edge node workloads, read
[known-issues/](../known-issues/) in full. Several non-obvious workarounds are
implemented in the edge pod specs and CNI configuration; removing them breaks
the edge deployment. Open investigations are tracked in [gaps.md](../gaps.md).

---

## Python (backend and tests)

- Follow PEP 8, use type hints, and include docstrings.
- Handle exceptions explicitly rather than letting a request fail opaquely.

---

## Documentation

### Tone and format

- English only.
- Impersonal and factual. No "we", no "I", no "you should".
- No em-dashes in prose. Use commas or restructure the sentence. Tables and code
  blocks are unaffected.
- No editorial commentary ("Note that...", "Keep in mind...").
- H1 for the title, H2 for major sections, code blocks with language hints,
  tables for structured data.

### Where a document goes

`docs/architecture/` for system design, `docs/deployment/` for setup guides,
`docs/operations/` for procedures, `docs/development/` for developer guides,
`docs/runbooks/` for diagnostics, `docs/known-issues/` for platform
limitations, `docs/security/` for the access model. Every new document is added
to the [docs index](../README.md).

### Known-issue format

A known-issue file documents a platform limitation and the solution implemented
here. It is not a debugging narrative. Structure: one sentence describing the
platform behavior, how the testbed handles it, and which files implement that,
with paths. Do not include symptom logs, failed approaches, or debugging steps.

### Gaps file

Every entry in [gaps.md](../gaps.md) must correspond to something verifiable: a
file that does not exist, a feature disabled in the code, or a confirmed bug. No
speculative or aspirational entries.

### Feature maturity

[status.md](../status.md) is the canonical maturity matrix; the README carries a
condensed summary only. Three tiers:

- **Supported**: deploys through the standard flow, is documented, and has been
  exercised end to end. Reproducible on a clean install. Enabled by default.
- **Experimental**: code and manifests exist and deploy, but the path is not
  validated end to end, depends on an experimental component, or has no
  exercised use case. Often disabled by default.
- **Planned**: a documented direction with no working code yet.

One-drop rule: a component is Supported only when all three Supported conditions
hold. If one fails it is Experimental. With no working code it is Planned.
Abandoned code is removed, not tiered.

When adding or changing a feature, classify it in `status.md` and record the
evidence in the Validated by column. Do not promote a component to Supported
without end-to-end validation.

---

## Development workflow

```bash
git clone https://github.com/Jacobbista/kelt.git
cd kelt
./testbed-config              # configure and deploy
```

Make changes, re-run the affected phase with `./testbed-config run-phase`, then
run the suites in `tests/`. Testing is owned by [testing.md](testing.md).

### Commit messages

Conventional Commits, one-line subject:

```
type(scope): description
```

Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `perf`, `ci`,
`build`, `style`, `revert`. Scope and a breaking-change `!` are optional. The
optional `commit-msg` hook enforces the format locally.

Do not add co-author trailers.

### Git hooks (developer-only)

Local hooks, opt-in and not part of the operator install. Anyone who commits
should turn them on once:

```
testbed dev-hooks on       # install (sets core.hooksPath=.githooks), and gitleaks if missing
testbed dev-hooks status   # show hook + release state
testbed dev-hooks off      # uninstall
```

- **pre-commit** blocks three things before they leave your machine:
  - files that are local by design, even when forced past `.gitignore`:
    `.local/` (specs, plans, previews), `.superpowers/`, `.claude/`,
    `experiments/runs/`, and the operator files `.testbed.*`;
  - the real SIM keys (K, OP, OPc) of the local `.testbed.subscribers.json`,
    anywhere in the staged changes; values already in the committed tree, like
    the public UERANSIM test key, are not a leak. Keys are never printed;
  - secrets found by `gitleaks` in the staged changes, the same scan CI runs on
    push.

  `dev-hooks on` installs the `gitleaks` release pinned as `gitleaks_version` in
  `ansible/group_vars/all.yml` into `~/.local/bin`, after checking its SHA-256
  against the release's checksum file. A `gitleaks` already on `PATH` is left
  alone. Tests for the hook: `bash .githooks/tests/pre-commit.test.sh`.
- **commit-msg** blocks a non-conforming subject. Pure bash, no Node dependency.
  Merge, revert, and rebase autosquash subjects pass through.
- **pre-push** is advisory only: it flags a frontend change that needs a version
  tag, and WIP commits.

### Publishing images (what needs a tag)

Each image has its own release lifecycle, decoupled from the others:

- **Dashboard frontend** publishes only on a `dashboard-frontend-v<semver>` git
  tag. Editing the frontend does not change the published image until you tag:
  ```
  testbed dev-hooks release    # bumps package.json and the deploy baseline,
                               # commits, tags, and pushes
  ```
  The cluster runs that semver **pinned** from `dashboard_frontend_tag` in
  `all.yml`, with `imagePullPolicy: IfNotPresent`. One tag is one image, so the
  rollout happens because the pod spec changed and `kubectl rollout undo` means
  something. The two versions are deliberately separate: `package.json` is what
  CI builds, the baseline is what the cluster runs, and they legitimately differ
  while a release is pending. `dev-hooks release` moves both in one commit and
  tags it, so the baseline can never name a tag that was never published.

  A pinned deploy cannot pull an image that does not exist yet, so wait for the
  CI build to finish before re-running phase 09.
- **Docs** publish automatically: the docs workflow rebuilds on any push touching
  `docs/**`, and re-running phase 09 forces a rollout. This image is deliberately
  **not** pinned. It is continuously published and carries no semver, so pinning
  it would mean editing a digest on every documentation commit. It stays on
  `:latest` with `imagePullPolicy: Always`, and the dashboard detects a new build
  by comparing image digests rather than tags.
- **NF and northbound images** live in their own repositories and are tagged
  there, not from this repo.

### Review checklist

- [ ] No hardcoded values: plan values from `all.yml`, NF ports by name from the NF wiring
- [ ] Ansible tasks are idempotent, workarounds carry a doc backlink
- [ ] Edge-specific code is gated
- [ ] Facts written at their owner document, linked elsewhere
- [ ] Feature classified in `status.md`
- [ ] Tests pass
- [ ] Commit subject follows the convention

---

## License

By contributing, you agree that your contributions will be licensed under the
Apache 2.0 License.
