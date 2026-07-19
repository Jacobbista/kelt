# 5G NF Platform: Development Plan

Standalone specification for the `5g-nf-platform` repository. Copy this document to the
root of that repository as `AGENTS.md` or `SPEC.md` before starting development.

---

## What this repository is

A container image factory for 5G Network Functions. It pulls source from one or more
upstream projects (Open5GS, free5GC, custom), applies research patches, builds one Docker
image per NF, and publishes versioned images to a container registry. It does not deploy
anything. Deployment is handled by consumers (e.g., `kelt`).

---

## What this repository is not

- A fork of Open5GS or any other upstream project.
- A deployment tool.
- A testbed or simulation environment.
- A monorepo containing all NF source code.

Upstream source is pulled at build time via `git clone` at a pinned tag. It is not vendored
into this repository.

---

## Build order: do these in sequence

### Phase 1: Scaffold and first working image

1. Create the directory structure (see below).
2. Write `Dockerfile` for one NF only: SMF. Reason: SMF is the first NF that needs a patch
   (`session-info` endpoint). Getting one NF building end-to-end validates the full pipeline
   before scaling to the others.
3. Write the `versions.json` schema (see below).
4. Write GitHub Actions workflow `build.yml` that builds and pushes SMF only.
5. Verify: `docker pull ghcr.io/<owner>/5g-nf-platform/smf:<tag>` works.

### Phase 2: SMF session-info patch

1. Clone Open5GS at the pinned tag locally.
2. Add the HTTP handler to `src/smf/app.c` (see patch spec below).
3. Export as `nfs/smf/patches/0001-session-info-endpoint.patch`.
4. Update SMF Dockerfile to apply the patch before building.
5. Verify: `curl http://127.0.0.1:9090/session-info` inside SMF container returns JSON.

### Phase 3: Remaining Open5GS NFs

Add one Dockerfile per NF: AMF, UPF, UDM, AUSF, UDR, NRF, PCF, BSF, NSSF. No patches on
these initially. Extend `build.yml` to build all of them. Update `versions.json`.

### Phase 4: CI automation for upstream updates

1. Add `check-upstream.yml` scheduled workflow (daily).
2. Workflow: query GitHub releases API for open5gs/open5gs, compare with pinned tag in
   `upstream-versions.json`, open a PR if newer tag exists.
3. PR updates the pinned tag per NF, increments `patch-rev` to 0 if no patches change.

### Phase 5: First experimental NF

Choose one: NEF from free5GC or a custom LMF stub. This phase validates the experimental NF
path end-to-end including NRF registration compatibility with Open5GS NRF.

---

## Directory structure

```
5g-nf-platform/
├── nfs/
│   ├── amf/
│   │   ├── patches/           # empty if no patches
│   │   └── Dockerfile
│   ├── smf/
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
│       ├── nef/
│       └── lmf/
├── .github/
│   └── workflows/
│       ├── build.yml
│       └── check-upstream.yml
├── upstream-versions.json     # pinned upstream tag per NF
├── versions.json              # published image tag per NF (updated by CI)
├── SPEC.md                    # this document
└── README.md
```

---

## Dockerfile pattern (per NF)

Each Dockerfile follows the same pattern: clone upstream at pinned tag, apply patches,
build, produce a minimal runtime image.

```dockerfile
# Baseline tested with v2.7.5 (version from original manual build).
# v2.7.5 is the pinned default; update upstream-versions.json to change it.
ARG UPSTREAM_TAG=v2.7.5
ARG NF_NAME=smf

FROM ubuntu:22.04 AS builder
RUN apt-get update && apt-get install -y \
    git cmake gcc g++ libsctp-dev libgnutls28-dev \
    libgcrypt-dev libssl-dev libidn11-dev libmongoc-dev \
    libbson-dev libmicrohttpd-dev libyaml-dev \
    python3-pip meson ninja-build pkg-config
RUN pip3 install meson

ARG UPSTREAM_TAG
RUN git clone --depth 1 --branch ${UPSTREAM_TAG} \
    https://github.com/open5gs/open5gs /src/open5gs

# WebUI (Node.js) is intentionally excluded. Subscriber management and
# monitoring are handled by the kelt dashboard.

COPY patches/ /patches/
RUN cd /src/open5gs && \
    for p in $(ls /patches/*.patch 2>/dev/null | sort); do \
      git apply "$p"; \
    done

ARG NF_NAME
RUN cd /src/open5gs && \
    meson build --prefix=/install && \
    ninja -C build install

FROM ubuntu:22.04
RUN apt-get update && apt-get install -y \
    libsctp1 libgnutls30 libgcrypt20 libssl3 \
    libidn11 libmongoc-1.0-0 libyaml-0-2 \
    && rm -rf /var/lib/apt/lists/*

ARG NF_NAME
COPY --from=builder /install/bin/open5gs-${NF_NAME}d /usr/local/bin/
COPY --from=builder /install/lib/ /usr/local/lib/
RUN ldconfig

# All Open5GS NFs expose a management HTTP server on port 9090.
# The healthcheck polls it; a 200 response means the NF is operational.
HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -sf http://127.0.0.1:9090/ || exit 1

ENTRYPOINT ["/bin/sh"]
```

Note: the testbed's ansible phase uses an init script as the actual entrypoint. The
Dockerfile entrypoint is intentionally minimal.

---

## versions.json schema

```json
{
  "_comment": "Canonical image tags. Updated by CI on each successful build.",
  "amf":  "ghcr.io/<owner>/5g-nf-platform/amf:2.7.2-p0",
  "smf":  "ghcr.io/<owner>/5g-nf-platform/smf:2.7.2-p3",
  "upf":  "ghcr.io/<owner>/5g-nf-platform/upf:2.7.2-p0",
  "udm":  "ghcr.io/<owner>/5g-nf-platform/udm:2.7.2-p0",
  "ausf": "ghcr.io/<owner>/5g-nf-platform/ausf:2.7.2-p0",
  "udr":  "ghcr.io/<owner>/5g-nf-platform/udr:2.7.2-p0",
  "nrf":  "ghcr.io/<owner>/5g-nf-platform/nrf:2.7.2-p0",
  "pcf":  "ghcr.io/<owner>/5g-nf-platform/pcf:2.7.2-p0",
  "bsf":  "ghcr.io/<owner>/5g-nf-platform/bsf:2.7.2-p0",
  "nssf": "ghcr.io/<owner>/5g-nf-platform/nssf:2.7.2-p0"
}
```

Tag format: `<upstream-version>-p<patch-rev>`. Patch revision increments per patch added
or changed. `p0` means upstream only, no patches.

---

## upstream-versions.json schema

```json
{
  "open5gs": "v2.7.5",
  "free5gc":  "v3.4.1"
}
```

This file is the single source of truth for which upstream tag each build uses. CI reads it.
PRs that bump an upstream tag update only this file.

---

## Versioning rules

- Never change the upstream tag without bumping `upstream-versions.json` via PR.
- Never change a patch without incrementing `patch-rev` in the image tag.
- `versions.json` is written only by CI, never manually.
- A tag once pushed to the registry is immutable. Do not retag.

---

## SMF session-info patch specification

**File to modify**: `src/smf/app.c` (or wherever the Open5GS management HTTP server
registers routes, confirm with `grep -r "ogs_app_management" src/smf/`).

**Endpoint**: `GET /session-info`

**Response body**:
```json
{
  "sessions": [
    {
      "imsi":   "001010123456786",
      "dnn":    "internet",
      "ipv4":   "10.45.0.6",
      "ipv6":   "",
      "snssai": { "sst": 1, "sd": "000001" }
    }
  ]
}
```

**Implementation approach**:
1. Register a new HTTP GET handler for `/session-info` in the SMF management server.
2. Iterate `smf_sess_t` linked list (the in-memory session table).
3. For each session: extract IMSI from `sess->guti`, DNN from `sess->dnn`, IPv4 from
   `sess->ue_ip.addr`, S-NSSAI from `sess->s_nssai`.
4. Serialize to JSON using Open5GS's existing `ogs_json_*` helpers.
5. Return HTTP 200 with `Content-Type: application/json`.

The patch must not affect any 5G protocol behavior. It is read-only access to existing
in-memory state.

---

## Adding an experimental NF

### Requirements checklist before starting

- [ ] Source is available under a compatible license (Apache 2.0 or similar preferred).
- [ ] NF registers with NRF using HTTP/JSON (SBI). Confirm schema compatibility with
      Open5GS NRF (`/nnrf-nfm/v1/nf-instances`).
- [ ] NF does not require modifications to other NFs to function.
- [ ] A minimal smoke test exists: NF starts, registers with NRF, responds to a discovery
      query.

### Steps

1. Create `nfs/experimental/<nf-name>/` with `Dockerfile` and `patches/`.
2. Add entry to `versions.json` and `upstream-versions.json`.
3. Add build job to `build.yml`.
4. Document interop status in `nfs/experimental/<nf-name>/README.md`: which Open5GS
   version it was tested with, what works, what does not.

### NRF compatibility

Open5GS NRF expects specific JSON fields in NF profile registration. If the experimental
NF uses a different schema (e.g., free5GC NRF schema), a compatibility shim may be needed.
Document any shim as a patch in `nfs/experimental/<nf-name>/patches/`.

---

## Licensing

Each NF directory must contain a `LICENSE` or `UPSTREAM_LICENSE` file identifying the
license of the upstream source. This repository does not hold upstream source code; the
license applies to the patches and Dockerfiles, which are authored here.

| Open5GS-derived NFs | AGPLv3 — if this repository is public, patched source is already visible here. No additional action required. |
| free5GC-derived NFs | Apache 2.0 — permissive, no copyleft obligations. |
| Custom NFs | Owner's choice. |

Patches and Dockerfiles in this repository are authored works. Apply whatever license is
appropriate for the project (AGPLv3 for consistency with Open5GS, or MIT/Apache for custom
work). Choose before making the repository public.

---

## Integration contract with kelt

This repository publishes `versions.json` to the default branch. The testbed reads it.

The testbed's `ansible/group_vars/all.yml` references image tags. When the testbed operator
wants to update an NF:

1. Check `versions.json` in this repository for the new tag.
2. Update the corresponding `nf_image_<name>` variable in `all.yml`.
3. Run `testbed run-phase 05-5g-core`.

The testbed dashboard (future feature) automates steps 1 and 2 via the GitHub API and a
confirmation prompt.

**No other coupling exists between the two repositories.** This repository does not know
about the testbed's Kubernetes manifests, Vagrant setup, or networking. The testbed does
not know about upstream sources, patches, or build tooling.

---

## Open questions to resolve before Phase 5

1. Which NRF, Open5GS or a standalone one, will serve as the registry for mixed-source
   deployments? Open5GS NRF has specific behavior that free5GC NFs may not match exactly.
2. Is the experimental NEF needed as a full SBI NF (registered with NRF, consumed by AMF)
   or as a sidecar service (standalone HTTP API)? The answer changes the integration
   complexity significantly.
3. For a custom LMF: does it need to integrate with an existing gNB LPP implementation,
   or is it a standalone positioning server? This determines the air interface requirements.
