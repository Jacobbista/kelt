# shellcheck shell=bash
# Fault injections for the verification campaign. Every function that changes
# the cluster has an undo, and run.sh installs `trap faults_undo_all EXIT` before
# the first one. Owner: experiments/README.md.

FAULT_LABEL="app.kubernetes.io/managed-by=kelt-experiments"
FAULT_POS_NS="${KELT_POS_NS:-$(plan_value positioning_namespace)}"
FAULT_SCALE_FILE="${KELT_FAULT_STATE:-/tmp/kelt-exp-fault-scale}"

# Anything an earlier, aborted run may have left behind.
faults_leftovers() {
  kubectl get networkpolicy -A -l "$FAULT_LABEL" -o name 2>/dev/null
  kubectl get deploy -n "$FAULT_POS_NS" \
    -o jsonpath='{range .items[?(@.spec.replicas==0)]}deploy/{.metadata.name}{"\n"}{end}' 2>/dev/null
}

# Cut the vendor adapter off the public internet: an Egress policy that allows
# only the private ranges phase 13 declares (DNS, cluster, management stay
# reachable). No other egress policy selects the pod, so this one isolates it.
fault_block_vendor() { # <app label>
  local app="$1" blocks
  blocks="$(python3 - "$REPO_ROOT/ansible/phases/13-network-policies/roles/network_policies/defaults/main.yml" <<'PY'
import sys, yaml
for cidr in yaml.safe_load(open(sys.argv[1]))["network_policy_private_ranges"]:
    print(f"        - ipBlock: {{cidr: {cidr}}}")
PY
)"
  kubectl apply -f - <<YAML
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: kelt-exp-block-vendor
  namespace: $FAULT_POS_NS
  labels: {app.kubernetes.io/managed-by: kelt-experiments}
spec:
  podSelector: {matchLabels: {app: $app}}
  policyTypes: [Egress]
  egress:
    - to:
$blocks
YAML
}
fault_unblock_vendor() { kubectl delete networkpolicy -n "$FAULT_POS_NS" kelt-exp-block-vendor --ignore-not-found; }

# Scale a positioning Deployment to 0, remembering its replicas in a file so the
# undo works from the EXIT trap of the calling shell.
fault_scale_down() { # <deploy>
  local r; r="$(kubectl get deploy -n "$FAULT_POS_NS" "$1" -o jsonpath='{.spec.replicas}' | tr -d '\r')"
  [ -n "$r" ] || die "cannot read replicas of $1"
  echo "$1 $r" >"$FAULT_SCALE_FILE"
  kubectl scale deploy -n "$FAULT_POS_NS" "$1" --replicas=0
}
fault_restore_scale() {
  [ -s "$FAULT_SCALE_FILE" ] || return 0
  local d r; read -r d r <"$FAULT_SCALE_FILE"
  kubectl scale deploy -n "$FAULT_POS_NS" "$d" --replicas="$r" && rm -f "$FAULT_SCALE_FILE"
}

faults_undo_all() { fault_unblock_vendor >/dev/null 2>&1 || true; fault_restore_scale >/dev/null 2>&1 || true; }
