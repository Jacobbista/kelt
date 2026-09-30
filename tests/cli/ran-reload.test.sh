#!/usr/bin/env bash
# Tests for testbed-config's worker_needs_ran_reload: the worker is reloaded when
# the RAN adapter it should have (PHYSICAL_RAN_BRIDGE) differs from the one it
# has, attached or detached. Run: bash tests/cli/ran-reload.test.sh
set -o pipefail

CLI="$(cd "$(dirname "$0")/../.." && pwd)/testbed-config"
eval "$(sed -n '/^worker_needs_ran_reload() {/,/^}/p' "$CLI")"
fails=0
read_applied_ran_bridge() { echo "$APPLIED"; }
expect() {  # $1=name $2=wanted(reload|keep) enabled bridge applied
  PHYSICAL_RAN_ENABLED="$3" PHYSICAL_RAN_BRIDGE="$4" APPLIED="$5"
  if worker_needs_ran_reload; then got=reload; else got=keep; fi
  if [ "$got" = "$2" ]; then echo "ok   $1"; else echo "FAIL $1 (got $got)"; fails=$((fails + 1)); fi
}
expect "attached, adapter applied"          keep   true  enp88s0 enp88s0
expect "attached, adapter not applied"      reload true  enp88s0 ""
expect "detached, adapter not applied"      reload false enp88s0 ""
expect "detached, another adapter"          reload false enp89s0 enp88s0
expect "no adapter asked"                   keep   false ""      enp88s0
exit $fails
