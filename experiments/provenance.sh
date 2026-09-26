#!/usr/bin/env bash
# Capture per-run provenance from the LIVE deployment into
# <run-dir>/provenance.json. Nothing here is typed by hand: commits come from
# git, image versions from the running pods, the two deployment flags from the
# rendered cluster state.
#
# Usage: provenance.sh <run-dir> [condition-label]
#
# Owner: experiments/README.md.

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

RUN_DIR="${1:?usage: provenance.sh <run-dir> [condition]}"
CONDITION="${2:-}"
mkdir -p "$RUN_DIR"
OUT="$RUN_DIR/provenance.json"

log "capturing provenance → $OUT"

KELT_COMMIT="$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
# Not a `| head` pipeline: under pipefail the SIGPIPE it sends to git fails the
# pipeline and the flag always read false.
KELT_DIRTY="$([ -n "$(git -C "$REPO_ROOT" status --porcelain 2>/dev/null)" ] && echo true || echo false)"
DATE_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# Image tags of the deployment under measurement, read from the running pods
# (cluster = reality; all.yml only states intent). One image per deployment.
images_json() {
  local ns pairs=""
  # iam carries Keycloak, whose version the thesis method records alongside the
  # gateway/engine/adapter images; it is not in EXPOSURE_NS_RE (that one separates
  # core from exposure for the footprint campaign).
  for ns in "$CORE_NS" $(echo "$EXPOSURE_NS_RE" | tr '|' ' ') "$IAM_NS"; do
    while read -r name img; do
      [ -n "$name" ] || continue
      pairs="$pairs{\"ns\":\"$ns\",\"deploy\":\"$name\",\"image\":\"$img\"},"
    done < <(kubectl get deploy -n "$ns" \
        -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.spec.template.spec.containers[0].image}{"\n"}{end}' 2>/dev/null)
  done
  printf '[%s]' "${pairs%,}"
}

# The two deployment flags that define the measured deployment. Values come from
# the rendered cluster, not from all.yml defaults: physical RAN is "enabled" if the
# AMF is attached to a NAD on the physical RAN bridge (br-ran, phase 04);
# northbound is "enabled" if the gateway is deployed. (Owner: all.yml
# physical_ran_enabled / *_enabled derived from NORTHBOUND_ENABLED.)
physical_ran_flag() {
  local nads amf_nets
  nads="$(kubectl get net-attach-def -n "$CORE_NS" \
      -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.spec.config}{"\n"}{end}' 2>/dev/null \
    | awk '/"bridge": *"br-ran"/ {print $1}')"
  amf_nets="$(kubectl get deploy -n "$CORE_NS" amf \
      -o jsonpath='{.spec.template.metadata.annotations.k8s\.v1\.cni\.cncf\.io/networks}' 2>/dev/null)"
  for n in $nads; do
    printf '%s' "$amf_nets" | grep -q "\"name\": *\"$n\"" && { echo enabled; return; }
  done
  echo disabled
}
PHYS_RAN="$(physical_ran_flag)"
NORTHBOUND="$(kubectl get deploy -n "$CAMARA_NS" -o name 2>/dev/null | grep -qi gateway && echo enabled || echo disabled)"

# The 5g-northbound git commit behind the deployed images. The image tag (in
# `images`, e.g. :0.9.0) is the human-facing version; this is the exact source
# commit, read from the OCI org.opencontainers.image.revision label the northbound
# build stamps. Read via crictl on the node running the gateway pod. Empty when the
# label or crictl is unavailable — the tag in `images` remains the version of record.
northbound_revision() {
  local pod node img raw
  pod="$(kubectl get pod -n "$CAMARA_NS" -o name 2>/dev/null | grep -m1 gateway | sed 's|pod/||' | tr -d '\r')"
  [ -n "$pod" ] || { echo ""; return; }
  node="$(kubectl get pod -n "$CAMARA_NS" "$pod" -o jsonpath='{.spec.nodeName}' 2>/dev/null | tr -d '\r')"
  img="$(kubectl get pod -n "$CAMARA_NS" "$pod" -o jsonpath='{.spec.containers[0].image}' 2>/dev/null | tr -d '\r')"
  [ -n "$node" ] && [ -n "$img" ] || { echo ""; return; }
  if [ -n "${KELT_KUBECTL:-}" ]; then
    raw="$(sudo k3s crictl inspecti "$img" 2>/dev/null)"
  else
    raw="$(vagrant ssh "$node" -c "sudo k3s crictl inspecti '$img'" 2>/dev/null | sed '/^\[Testbed\]/d')"
  fi
  printf '%s' "$raw" | python3 -c "
import json,sys
try:
    d=json.load(sys.stdin)
    print(d.get('info',{}).get('imageSpec',{}).get('config',{}).get('Labels',{}).get('org.opencontainers.image.revision',''))
except Exception:
    print('')
" 2>/dev/null
}
NB_REV="$(northbound_revision)"

PILOT="$([ "${KELT_PILOT:-0}" = 1 ] && echo true || echo false)"
HOST_TZ="$(date +%z)"

cat > "$OUT" <<JSON
{
  "date_utc": "$DATE_UTC",
  "condition": "$CONDITION",
  "kelt_commit": "$KELT_COMMIT",
  "kelt_worktree_dirty": $KELT_DIRTY,
  "pilot": $PILOT,
  "host_tz": "$HOST_TZ",
  "exposure_namespaces": "$EXPOSURE_NS_RE",
  "northbound_revision": "$NB_REV",
  "deployment_flags": {
    "physical_ran": "$PHYS_RAN",
    "northbound": "$NORTHBOUND"
  },
  "upf_target": "$UPF_TARGET",
  "gateway_url": "$(gateway_url 2>/dev/null || echo unknown)",
  "images": $(images_json)
}
JSON

log "provenance written"
cat "$OUT"
