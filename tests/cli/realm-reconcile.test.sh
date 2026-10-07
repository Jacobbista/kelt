#!/usr/bin/env bash
# Tests for the phase 08 realm reconcile gate: KEYCLOAK_REALM_RECONCILE given in
# the environment decides that one run over the default stored in .testbed.env,
# and is never written back to it.
# Run: bash tests/cli/realm-reconcile.test.sh
set -o pipefail

CLI="$(cd "$(dirname "$0")/../.." && pwd)/testbed-config"
eval "$(sed -n '/^prompt_kc_reconcile() {/,/^}/p' "$CLI")"
fails=0
ok() { echo "ok   $1"; }
bad() { echo "FAIL $1"; fails=$((fails + 1)); }
expect() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }

HAS_GUM=false
run() { KC_RECONCILE_ENV="$1"; KEYCLOAK_REALM_RECONCILE="$2"; prompt_kc_reconcile < /dev/null; }

run true ask;   expect "env true over stored ask" "[ \"\$KC_RECONCILE_THIS_RUN\" = true ]"
run false true; expect "env false over stored true" "[ \"\$KC_RECONCILE_THIS_RUN\" = false ]"
run "" true;    expect "no env: stored true" "[ \"\$KC_RECONCILE_THIS_RUN\" = true ]"
run "" ask;     expect "no env, stored ask, no terminal: false" "[ \"\$KC_RECONCILE_THIS_RUN\" = false ]"
run true ask;   expect "env never changes the stored default" "[ \"\$KEYCLOAK_REALM_RECONCILE\" = ask ]"

# The environment value is read before load_config replaces it with the stored one.
capture=$(grep -n '^KC_RECONCILE_ENV=' "$CLI" | head -1 | cut -d: -f1)
load=$(grep -n '^[[:space:]]*load_config$' "$CLI" | head -1 | cut -d: -f1)
expect "env value captured before the config is loaded" "[ -n \"$capture\" ] && [ -n \"$load\" ] && [ \"$capture\" -lt \"$load\" ]"

[ "$fails" -eq 0 ] && echo "all passed" || { echo "$fails failed"; exit 1; }
