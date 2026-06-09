# 5G NF Platform — Architecture Specification

This document specifies the design of a companion repository (`5g-nf-platform`) that manages
the lifecycle of 5G Network Function container images used by this testbed. It also defines
the integration contract between that repository and this one.

---

## Purpose

The testbed requires container images for each Open5GS NF (AMF, SMF, UPF, UDM, AUSF, UDR, NRF,
PCF, BSF, NSSF) and optionally for experimental NFs sourced from free5GC, third-party
implementations, or custom development. The NF platform repository:

- Tracks upstream source for each NF independently.
- Applies research patches without modifying upstream release branches.
- Builds per-NF container images via CI and publishes them to a container registry.
- Provides a versioned, reproducible artifact for every NF deployed in the testbed.

---

## Scope Boundary

| Responsibility | Repository |
|---|---|
| NF source, patches, Dockerfiles, build CI | `5g-nf-platform` |
| Cluster provisioning, Kubernetes manifests, networking | `kelt` (this repo) |
| Dashboard, monitoring, subscriber management | `kelt` (this repo) |

The testbed consumes NF images by tag only. It has no dependency on NF source code or build tooling.

---

## Repository Structure

```
5g-nf-platform/
├── nfs/
│   ├── amf/
│   │   ├── upstream/          # git subtree or submodule tracking open5gs
│   │   ├── patches/           # .patch files applied on top of upstream
│   │   └── Dockerfile
│   ├── smf/
│   │   ├── upstream/
│   │   ├── patches/
│   │   │   └── 0001-session-info-endpoint.patch
│   │   └── Dockerfile
│   ├── upf/
│   ├── udm/
│   ├── ausf/
│   ├── udr/
│   ├── nrf/
│   ├── pcf/
│   ├── bsf/
│   ├── nssf/
│   └── experimental/
│       ├── nef/               # NEF from free5GC or other source
│       └── lmf/               # LMF custom or third-party
├── .github/
│   └── workflows/
│       ├── build-open5gs.yml  # triggered on upstream tag or patch change
│       └── build-experimental.yml
└── versions.json              # canonical image tag map, consumed by testbed
```

---

## Versioning

Each NF image is tagged as:

```
ghcr.io/<owner>/5g-nf-platform/<nf-name>:<upstream-version>-p<patch-rev>
```

Examples:
- `ghcr.io/owner/5g-nf-platform/smf:2.7.2-p3`
- `ghcr.io/owner/5g-nf-platform/nef:free5gc-3.4.1-p0`

`patch-rev` increments when a patch is added or modified without an upstream version change.
`p0` means no patches applied beyond the upstream tag.

A `versions.json` file in the repository root tracks the canonical current tag for each NF:

```json
{
  "amf":  "ghcr.io/owner/5g-nf-platform/amf:2.7.2-p0",
  "smf":  "ghcr.io/owner/5g-nf-platform/smf:2.7.2-p3",
  "upf":  "ghcr.io/owner/5g-nf-platform/upf:2.7.2-p0",
  "nef":  "ghcr.io/owner/5g-nf-platform/nef:free5gc-3.4.1-p0"
}
```

---

## Patch Management

Patches live as numbered `.patch` files per NF. They are applied sequentially during the
Docker build. Each patch file has a header comment stating the purpose and, if applicable,
a reference to a known issue or upstream bug report.

Patch naming: `NNNN-short-description.patch` (zero-padded four digits).

When upstream merges a patch's functionality, the patch file is removed and `patch-rev`
resets to 0 at the next upstream version bump.

---

## SMF Session Info Patch (Immediate Requirement)

The SMF management server (port 9090) currently exposes no endpoint for active PDU session
state. A patch adds `GET /session-info` returning the list of active sessions:

```json
{
  "sessions": [
    {
      "imsi": "001010123456786",
      "dnn":  "internet",
      "ipv4": "10.45.0.6",
      "ipv6": "",
      "snssai": { "sst": 1, "sd": "000001" }
    }
  ]
}
```

This endpoint is consumed by the testbed dashboard backend (`ue_service.py`) as a replacement
for SMF log parsing. The dashboard calls it via `kubectl exec` on the SMF pod, the same
mechanism used for `GET /gnb-info` on the AMF.

Implementation location in Open5GS source: `src/smf/app.c` (management HTTP handler).

---

## Experimental NF Integration

### Interoperability constraints

All NFs communicate via 5G SBI (HTTP/2, OpenAPI 3.0) and register with NRF. An NF from a
different implementation (free5GC, custom) must satisfy:

1. NRF registration and heartbeat using the `open5gs` NRF's expected schema.
2. SBI interface compliance for the specific NF service (e.g., Nnef, Nlmf).
3. MongoDB access only through the standard subscriber schema for subscriber data reads.

Interoperability is not guaranteed and must be validated per-NF. The testbed is the
validation environment.

### Licensing

| Source | License | Notes |
|---|---|---|
| open5gs | AGPLv3 | Modifications require source availability if distributed |
| free5GC | Apache 2.0 | Permissive, no copyleft |
| Custom NF | Author's choice | No constraint from other licenses in the repo |

AGPLv3 and Apache 2.0 coexist in the same repository as separate modules without license
conflict. AGPLv3 copyleft applies only to the open5gs-derived NF code and does not extend
to Apache-licensed or custom NFs in the same repository. If the repository is public,
modified open5gs NF source must be accessible (a public fork satisfies this).

---

## CI/CD Workflow

Build triggers:
- A new upstream release tag is detected via scheduled workflow (daily check against GitHub
  releases API).
- A patch file is added or modified.
- A Dockerfile is modified.

On trigger:
1. Clone upstream source at the pinned tag.
2. Apply patches in order.
3. Build Docker image.
4. Run smoke test: start container, verify process starts, check management endpoint responds.
5. Push to `ghcr.io` with the versioned tag.
6. Update `versions.json` and open a PR to the `5g-nf-platform` repository.

The testbed repository is updated separately: a PR updates `all.yml` image references to the
new tags from `versions.json`.

---

## Update Detection in Dashboard

The testbed dashboard includes an update check for deployed NF images. The check:

1. Reads currently deployed image tags from Kubernetes pod annotations.
2. Fetches `versions.json` from the `5g-nf-platform` repository via GitHub API.
3. Compares tags per NF.
4. Displays a badge in the 5G Core page when a newer image is available.
5. On operator confirmation, triggers the ansible phase 05 playbook with the updated image
   tags, pulling and redeploying only the NFs with new images.

This feature requires the testbed backend to have network access to the GitHub API and
the ansible binary available in the execution environment (already present on the ansible VM).

---

## Changes Implemented

The following changes to this repository are complete.

### `ansible/group_vars/all.yml` — implemented

`nf_images` dict added with per-NF image references to `ghcr.io/jacobbista/5g-nf-platform`.
The global `nf_image` fallback is retained for backward compatibility.

### `ansible/phases/05-5g-core/roles/nf_deployments/defaults/main.yml` — implemented

Each NF entry has an `image: "{{ nf_images.<name> | default(nf_image) }}"` field.
MongoDB migrated to `mongo:6` official image (no longer depends on monolithic build).
`experimental_nfs: []` list added for opt-in experimental NF deployment.

### `ansible/phases/05-5g-core/templates/nf-deployment.yaml.j2` — implemented

`image: {{ nf.image | default(nf_image) }}`

### `ansible/phases/05-5g-core/roles/nf_deployments/tasks/main.yml` — implemented

Experimental NF deploy loop added after core NF loop.

### `dashboard/backend/app/services/ue_service.py` — implemented

`get_smf_sessions()` added: queries SMF `/session-info` endpoint, overlays results onto
log-derived UE state in `get_active_ues()`. Log parsing retained as fallback.

### `dashboard/backend/app/services/nf_service.py` — implemented (new)

`NFService`: deployed image comparison against `versions.json`, ansible-triggered NF update.

### `dashboard/backend/app/routers/nf.py` — implemented (new)

`GET /api/v1/nf/versions` — per-NF deployed vs available comparison.
`POST /api/v1/nf/update/stream` — NDJSON-streamed ansible redeployment for a single NF.

### `dashboard/frontend/src/pages/CorePage.jsx` — implemented

Version badge per NF card (green = current, amber = update available with clickable update trigger).
Manual "check updates" button. Update confirmation modal with streamed ansible output.

### `dashboard/frontend/src/components/NfCard.jsx` — implemented

`versionInfo` and `onUpdate` props added. Version badge rendered in card header.
