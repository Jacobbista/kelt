# KELT Quickstart

End-user and agent operations guide. For development and contribution
conventions, see [CLAUDE.md](CLAUDE.md).

---

## 1. First-time install

```bash
git clone <repo-url>
cd kelt
./testbed-config
```

Tested on Ubuntu 24.04 LTS (Server and Desktop). Auto-fixes use `apt`; on other
distributions install the missing packages with your package manager. macOS and
Windows are a v1 target and currently untested.

On first run, the onboarding wizard checks the host and offers
auto-fixes:

**Required:**
- `vagrant` binary present
- VirtualBox (`vboxmanage`) present
- CPU virtualization extensions (`vmx`/`svm`) enabled in BIOS/UEFI
- `gum` TUI helper installed
- Local `.testbed.env` initialized
- Shell alias `testbed` installed in `~/.bashrc`

**Optional:**
- User in `vboxusers` group, only relevant for VirtualBox USB
  passthrough. Plain VM bring-up works without it; the wizard
  reports it as a hint, not a failure.

Items the wizard can fix automatically:

| Item | Fix |
|---|---|
| `gum` missing | apt install via `repo.charm.sh` |
| Alias missing | Writes `testbed` alias + bash completion to `~/.bashrc` |
| Config missing | Prompts for deployment profile |

Items requiring manual install:

| Item | Command (Ubuntu/Debian) |
|---|---|
| `vagrant` + VirtualBox | `sudo apt-get install -y vagrant virtualbox virtualbox-dkms` |
| CPU virtualization | Enable VT-x or AMD-V in BIOS/UEFI |

After onboarding, the wizard writes a marker at
`~/.config/5g-testbed/onboarded` so subsequent runs skip straight to
the menu. Re-run the wizard any time with `testbed onboarding`.

---

## 2. Daily operation

After onboarding, the `testbed` alias works from any directory.

```bash
testbed                  # interactive TUI menu (gum)
testbed up               # bring up the cluster (vagrant up)
testbed provision        # full Ansible provision
testbed show             # show current configuration
testbed help             # man-style reference
```

### Navigation

Every interactive picker exposes a `← Cancel` entry as the last option
and respects **Esc** as a cancel keystroke. Submenus also have a
`← Back` entry. Pressing either drops you back to the parent menu
without committing a change.

### Menu layout

The interactive menu groups commands into sections:

| Section | Contains |
|---|---|
| Deploy ▸ | up, provision, single-phase run, autostart toggle |
| Configure ▸ | profile, edge VM, physical RAN, UERANSIM toggle |
| External access ▸ | auth-network sub-menu, dashboard dev frontend, auth on/off |
| Secrets ▸ | IAM admin password, IAM/CAMARA client secrets |
| Tests | end-to-end and protocol test suites |
| Show full status | dump of current configuration |
| Re-run onboarding checks | re-validate host requirements |
| Install CLI alias + completion | bashrc setup |
| Help | this reference |

---

## 3. Auto-start at boot

Useful on a dedicated testbed host that should come up after a reboot
(power loss, kernel upgrade, etc.).

```bash
testbed autostart on        # install + enable systemd unit
testbed autostart status    # show current state
testbed autostart off       # disable + remove unit
```

The unit is system-wide at `/etc/systemd/system/5g-testbed.service`,
runs `vagrant up` as the current user, and waits for
`network-online.target`. Follow the cluster bring-up with
`journalctl -u 5g-testbed.service -f`.

Stop semantics: `systemctl stop 5g-testbed.service` runs
`vagrant halt`. The unit is `Type=oneshot RemainAfterExit=yes` so the
"active" state reflects the cluster having been brought up, not a
long-running process.

---

## 4. External access (tunnels, public domains)

Internal LAN access uses NodePorts on the worker VM IP (default
`192.168.56.11`). To expose the dashboard, CAMARA gateway, or
positioning demo over a public domain, see
[docs/security/external-access.md](docs/security/external-access.md).
For the concrete list of bypass apps required on a Zero-Trust gateway
(Keycloak realm endpoints and theme assets, dashboard WebSockets, dev
frontend HMR), see
[docs/deployment/external-tunnel.md](docs/deployment/external-tunnel.md).

Shortcut for the supported sub-domain convention:

```bash
testbed auth-network preset-cloudflare yourdomain.com
```

This sets `DASHBOARD_EXTERNAL_ORIGIN=https://core.yourdomain.com`,
`CAMARA_GATEWAY_EXTERNAL_ORIGIN=https://api.yourdomain.com`,
`POSITIONING_DEMO_EXTERNAL_ORIGIN=https://demo.yourdomain.com`, and
the dev frontend at `https://dev.yourdomain.com`. The tunnel itself
(Cloudflare, ngrok, ssh-based, etc.) is configured outside the
testbed and is documented separately.

---

## 5. Agent reference (non-interactive)

Every menu action has a subcommand. Agents and CI scripts use these
directly; no flags are required, all values are positional.

### Setup

| Command | Args | Notes |
|---|---|---|
| `testbed onboarding` | — | Re-run wizard, idempotent |
| `testbed install` | — | Install alias + completion (asks for gum if missing) |

### Deploy

| Command | Args | Effect |
|---|---|---|
| `testbed up` | — | `vagrant up` with current profile |
| `testbed provision` | — | Full Ansible playbook |
| `testbed run-phase` | `<phase-dir>` | Single phase, e.g. `09-dashboard` |
| `testbed autostart` | `on \| off \| status` | systemd unit toggle |

### Configure

| Command | Args |
|---|---|
| `testbed set-profile` | `laptop \| server` |
| `testbed edge` | `on \| off \| true \| false` |
| `testbed ran` | `<nic> \| disable` |
| `testbed northbound` | `on \| off` (positioning/CAMARA feature: phases 10-12 + placement-editor, in one command) |

### External access

| Command | Args |
|---|---|
| `testbed auth-network` | `status \| dev \| auth \| origin \| positioning-origin \| prefix \| keycloak-url \| preset-cloudflare <root-domain>` |
| `testbed dashboard-dev` | `true \| false` |
| `testbed dashboard-auth` | `enabled \| disabled` |

### IAM

| Command | Args |
|---|---|
| `testbed iam reconcile` | `on \| off \| ask \| status` (controls phase 08 realm reconcile gate) |

Phase 08 seeds two end users on first deploy: `admin` (group `g-dashboard-admins`)
and `viewer` (group `g-dashboard-viewers`). Both are created with
`temporary: true`, so the password must be reset at first login. Phase 08
reruns never overwrite a password changed via the Keycloak console.

When the reconcile gate is set to `ask` (default), `testbed run-phase 08-iam`
prompts the operator with a short description of what reconcile does and
collects an answer for that run. The IAM page in the dashboard
(admin-only) lists the role model, seed users, and clients without leaving
the SPA. Full reference: [docs/security/iam.md](docs/security/iam.md).

### Secrets

| Command | Args |
|---|---|
| `testbed iam-admin-password` | `<password> \| --clear` |
| `testbed secrets` | `generate-missing \| manual \| rotate \| status \| clear` |

### Inspect

| Command | Args |
|---|---|
| `testbed show` | — |
| `testbed endpoints` | — (URLs for dashboard, demo, CAMARA, Keycloak — external if configured, else worker NodePort) |
| `testbed env` | — (prints `export` lines, eval-safe) |
| `testbed tests` | `[suite...]` |

---

## 6. Troubleshooting

| Symptom | Action |
|---|---|
| `vagrant up` fails after host reboot | `testbed onboarding` to re-check requirements; if VMs partially up, `vagrant halt && testbed up` |
| Dashboard shows API errors right after boot | Initial pod boot can take ~2 min; the SPA splash screen waits on backend + Keycloak. If it never clears, check `sudo k3s kubectl -n iam get pods` on the master |
| `testbed` alias not found in new shell | `source ~/.bashrc` or open a new shell. Re-install with `./testbed-config install` |
| `gum` install fails behind a proxy | Manual: `sudo apt-get install -y gum` after adding the Charm repo (see install_gum_apt in `testbed-config`) |
| Want to switch domain | `testbed auth-network preset-cloudflare newdomain.com && testbed run-phase 08-iam` |
| Dev frontend reloads in a loop, splash flashes | Vite HMR WebSocket blocked by the tunnel. Either disable HMR (`DASHBOARD_DEV_HMR_ENABLED=false`) or add a Zero-Trust bypass for `<dev-host>/__vite_hmr*` (the path is baked in by phase 09). Reference: `docs/deployment/external-tunnel.md`. |
| Browser console: `Invalid hook call ... more than one copy of React` | Vite pre-bundle cache rotated mid-session, leaving two chunk generations live in the tab. Hard-refresh (`Ctrl+Shift+R`). Persistent occurrence means a task ran `npm install` unconditionally; see CLAUDE.md "Dashboard frontend" for the mtime gate. |
| Sidebar mode badge shows the wrong environment (e.g. `PROD` on the dev URL) | Bundle stale. Hard-refresh; if it persists after, ship a new bundle with `testbed run-phase 09-dashboard` and tag a new `dashboard-frontend-v*`. |

---

## 7. Files

| Path | Purpose | Committed? |
|---|---|---|
| `.testbed.env` | Persisted non-secret config (profile, edge, URLs) | Yes (template) |
| `.testbed.secrets` | Passwords + client secrets | No, gitignored |
| `~/.config/5g-testbed/onboarded` | First-run marker | N/A (user-local) |
| `/etc/systemd/system/5g-testbed.service` | Autostart unit (when enabled) | N/A (system-local) |

For deeper docs:

- Architecture: [docs/architecture/](docs/architecture/)
- Known issues: [docs/known-issues/](docs/known-issues/)
- IAM realm structure: [docs/security/iam.md](docs/security/iam.md)
- External exposure patterns: [docs/security/external-access.md](docs/security/external-access.md)
