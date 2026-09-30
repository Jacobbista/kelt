# shellcheck shell=bash
# Shared plumbing for the KELT measurement campaigns.
#
# Design rule (AGENTS.md "Hard constraints"): nothing here hardcodes an address,
# port, version, or image. Dynamic facts (node IP, NodePort, image tags, git
# commits) are read from the LIVE deployment at run time; the few names that
# identify owner-declared objects (namespaces) sit in one block below with a
# pointer to the document/file that owns them, and every one is overridable by an
# environment variable so a differently-named deployment still works.
#
# Owner docs: experiments/README.md (runbook + scope), docs/tools/5g-probe.md
# (the probe), ansible/group_vars/all.yml (the deployment values).

set -euo pipefail

EXP_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$EXP_ROOT/.." && pwd)"
RUNS_DIR="${KELT_EXP_RUNS_DIR:-$EXP_ROOT/runs}"

# ── Names and addresses from ansible/group_vars/all.yml (override via env) ─────
plan_value() {
  python3 -c 'import sys, yaml; print(yaml.safe_load(open(sys.argv[1]))[sys.argv[2]])' \
    "$REPO_ROOT/ansible/group_vars/all.yml" "$1"
}
CORE_NS="${KELT_CORE_NS:-$(plan_value namespace_5g)}"
# Exposure stack for the resource-use campaign: northbound (phase 10) only. The
# apps namespace holds the mec measurement server and user apps; identity is
# reported apart.
EXPOSURE_NS_RE="${KELT_EXPOSURE_NS_RE:-$(plan_value positioning_namespace)|$(plan_value camara_namespace)}"
APPS_NS="${KELT_APPS_NS:-$(plan_value apps_namespace)}"
CAMARA_NS="${KELT_CAMARA_NS:-$(plan_value camara_namespace)}"
MONITORING_NS="${KELT_MONITORING_NS:-$(plan_value monitoring_namespace)}"
IAM_NS="${KELT_IAM_NS:-$(plan_value iam_namespace)}"

# UPF PDU anchor for the probe: the internet DNN gateway on the UPF (ogstun).
# The probe's own default lives in 5g-probe/probe/config.py (FIVEG_PROBE_UPF_TARGET).
UPF_TARGET="${FIVEG_PROBE_UPF_TARGET:-$(plan_value ue_internet_gateway)}"
# The network campaigns' endpoint: the measurement server in the apps namespace
# on N6m, on the path the edge application uses and apart from the UPF.
NET_TARGET="${KELT_NET_TARGET:-$(plan_value apps_measurement_server_n6m_ip)}"
NET_TARGET="${NET_TARGET%/*}"

# kubectl access. Inside a VM Kubernetes is K3s (AGENTS.md): use `sudo k3s
# kubectl`. From the host we reach it through the master VM. Override KELT_KUBECTL
# to run on-node or against another kubeconfig.
kubectl() {
  if [ -n "${KELT_KUBECTL:-}" ]; then
    # shellcheck disable=SC2086
    $KELT_KUBECTL "$@"
  else
    # `vagrant ssh -c` takes ONE command string, so each argument is shell-escaped
    # with %q and rebuilt: a bare "$*" would flatten quoting and break any jsonpath
    # carrying quotes/spaces (e.g. node InternalIP, the image range). The login
    # shell also prints the testbed banner ("[Testbed] Profile: ...") on stdout
    # ahead of the output, so strip it or it contaminates every parsed value.
    local remote="sudo k3s kubectl" a
    for a in "$@"; do remote+=" $(printf '%q' "$a")"; done
    vagrant ssh master -c "$remote" 2>/dev/null < /dev/null | sed '/^\[Testbed\]/d'
  fi
}

# First reachable node IP (NodePort services answer on any node).
node_ip() {
  kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}' \
    | tr -d '\r' | awk '{print $1}'
}

# NodePort of a service: svc_nodeport <namespace> <service> [port-index]
svc_nodeport() {
  local ns="$1" svc="$2" idx="${3:-0}"
  kubectl get svc -n "$ns" "$svc" -o jsonpath="{.spec.ports[$idx].nodePort}" | tr -d '\r'
}

# Base URL of the deployed CAMARA gateway (derived, never hardcoded).
# Override with KELT_GATEWAY_URL when a front-door hostname is preferred.
gateway_url() {
  if [ -n "${KELT_GATEWAY_URL:-}" ]; then echo "$KELT_GATEWAY_URL"; return; fi
  local svc="${KELT_CAMARA_SVC:-camara-gateway}"
  echo "http://$(node_ip):$(svc_nodeport "$CAMARA_NS" "$svc")"
}

# A fresh, timestamped run directory for a campaign: new_run_dir throughput
# Uses a caller-supplied UTC stamp so the layout stays reproducible/testable.
new_run_dir() {
  local campaign="$1" stamp="${2:-$(date -u +%Y%m%dT%H%M%SZ)}"
  local d="$RUNS_DIR/$campaign/$stamp"
  mkdir -p "$d"
  echo "$d"
}

# A CAMARA access token for the demo consumer (client_credentials on the
# `camara-api-demo` client, org-scoped). The secret comes from .testbed.secrets
# and never lands in a run directory or a log line. Override KELT_CAMARA_TOKEN
# to bring your own. Owner of the client: docs/security/iam.md.
camara_token() {
  if [ -n "${KELT_CAMARA_TOKEN:-}" ]; then echo "$KELT_CAMARA_TOKEN"; return; fi
  local secret kc realm
  secret="$(grep '^CAMARA_API_DEMO_SECRET' "$REPO_ROOT/.testbed.secrets" 2>/dev/null | cut -d= -f2- | tr -d '"'"'"' ')"
  [ -n "$secret" ] || die "CAMARA_API_DEMO_SECRET not in .testbed.secrets"
  kc="${KELT_KC_URL:-http://$(node_ip):$(plan_value keycloak_nodeport)/auth}"
  realm="${KELT_KC_REALM:-5g-testbed}"
  curl -s --max-time 15 \
    -d grant_type=client_credentials -d client_id=camara-api-demo \
    --data-urlencode "client_secret=$secret" \
    "$kc/realms/$realm/protocol/openid-connect/token" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("access_token",""))' 2>/dev/null \
    | grep . || die "token mint failed (check KELT_KC_URL=$kc / realm $realm)"
}

log() { printf '[%s] %s\n' "$(date -u +%H:%M:%SZ)" "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

# A new run of <slug>: writes provenance (KELT_PILOT=1 marks a trial run, never
# reported), echoes the run dir.
begin_run() {
  local slug="$1" condition="${2:-}" d
  d="$(new_run_dir "$slug")"
  mkdir -p "$d/raw"
  KELT_PILOT="${KELT_PILOT:-0}" "$EXP_ROOT/provenance.sh" "$d" "$condition" >/dev/null
  echo "$d"
}

# window open|close <run_dir> <label>: the measured part of a run, read by resource-use.
window() { (cd "$EXP_ROOT" && python3 -m lib.runmeta "$1" "$2" "$3"); }

# ── footprint: CPU and memory at 1 s (lib/footprint-sampler.sh) ──────────────
# On both VMs (every pod and the VM) and on this host (the machine only), from
# footprint_start to footprint_stop; resource-use/footprint.py reads the files.

# vagrant ssh reads stdin: never let it take the caller's (a loop fed on stdin).
vm_sh() { (cd "$REPO_ROOT" && SSH_AUTH_SOCK= vagrant ssh "$1" -c "$2" 2>/dev/null < /dev/null) | sed '/^\[Testbed\]/d'; }

FOOTPRINT_VMS="master worker"
FOOTPRINT_TMP="${TMPDIR:-/tmp}"

footprint_pods() {
  kubectl get pods -A -o jsonpath='{range .items[*]}{.metadata.uid}{"\t"}{.metadata.namespace}{"\t"}{.metadata.name}{"\n"}{end}' \
    | tr -d '\r'
}

# footprint_start <tag> <seconds> <dest dir>: the samplers stop on their own
# after <seconds> if footprint_stop never comes. The pod map is taken now and
# again at the stop (a pod recreated in between keeps its name).
footprint_start() {
  local tag="$1" secs="$2" dest="$3" b64 vm
  mkdir -p "$dest"
  footprint_pods > "$dest/pods.tsv"
  b64="$(base64 -w0 "$EXP_ROOT/lib/footprint-sampler.sh")"
  for vm in $FOOTPRINT_VMS; do
    # Two commands, not `a && b &`: that would put the copy and the sampler in
    # one background shell holding the SSH session open until the sampler ends.
    vm_sh "$vm" "echo $b64 | base64 -d > /tmp/kelt-fp-$tag.sh
      setsid nohup bash /tmp/kelt-fp-$tag.sh /tmp/kelt-fp-$tag.txt $secs > /dev/null 2>&1 < /dev/null & echo \$! > /tmp/kelt-fp-$tag.pid"
  done
  FOOTPRINT_NO_PODS=1 FOOTPRINT_VBOX=1 setsid nohup bash "$EXP_ROOT/lib/footprint-sampler.sh" "$FOOTPRINT_TMP/kelt-fp-$tag-host.txt" "$secs" \
    > /dev/null 2>&1 < /dev/null & echo $! > "$FOOTPRINT_TMP/kelt-fp-$tag-host.pid"
}

# footprint_stop <tag> <dest dir>: stops the samplers and copies their files
# into <dest>/<machine>.txt.gz (base64: vagrant ssh is not binary safe).
footprint_stop() {
  local tag="$1" dest="$2" vm h
  h="$FOOTPRINT_TMP/kelt-fp-$tag-host"
  mkdir -p "$dest"
  for vm in $FOOTPRINT_VMS; do
    vm_sh "$vm" "kill \$(cat /tmp/kelt-fp-$tag.pid 2>/dev/null) 2>/dev/null; [ -f /tmp/kelt-fp-$tag.txt ] || exit 1
      gzip -c /tmp/kelt-fp-$tag.txt | base64 -w0 && rm -f /tmp/kelt-fp-$tag.txt /tmp/kelt-fp-$tag.sh /tmp/kelt-fp-$tag.pid" \
      | base64 -d > "$dest/$vm.txt.gz" && [ -s "$dest/$vm.txt.gz" ] || { rm -f "$dest/$vm.txt.gz"; log "no footprint samples from $vm"; }
  done
  kill "$(cat "$h.pid" 2>/dev/null)" 2>/dev/null || true
  if [ -s "$h.txt" ]; then gzip -c "$h.txt" > "$dest/host.txt.gz"; else log "no footprint samples from this host"; fi
  rm -f "$h.txt" "$h.pid"
  footprint_pods >> "$dest/pods.tsv"
  sort -u -o "$dest/pods.tsv" "$dest/pods.tsv"
}

# footprint_summarise <run dir> [footprint.py args]: groups as resource-use reports them.
footprint_summarise() {
  local run_dir="$1"; shift
  python3 "$EXP_ROOT/resource-use/footprint.py" "$run_dir" "$@" \
    --core "$CORE_NS" --exposure "$EXPOSURE_NS_RE" --identity "$IAM_NS" --apps "$APPS_NS" \
    --probe "${KELT_PROBE_DEPLOY:-netshoot}" >/dev/null
}
