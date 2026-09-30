#!/usr/bin/env bash
# Tests for testbed-config's save_config: it rewrites the keys it owns and keeps
# the ones it does not (written by the pieces runner or the dashboard, e.g. the
# operations record limits). Run: bash tests/cli/save-config.test.sh
set -o pipefail  # no -u: save_config expands every config variable

CLI="$(cd "$(dirname "$0")/../.." && pwd)/testbed-config"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
ENV_FILE="$tmp/env"
eval "$(sed -n '/^write_file_atomic() {/,/^}/p; /^save_config() {/,/^}/p' "$CLI")"
# The CLI runs under `set -e`: a save must return 0 with or without kept keys.
save() { ( set -e; save_config > /dev/null; echo done ) | grep -qx done || { echo "FAIL save_config aborted under set -e"; fails=$((fails + 1)); }; }
fails=0
check() { if grep -qx "$2" "$ENV_FILE"; then echo "ok   $1"; else echo "FAIL $1: no line '$2'"; fails=$((fails + 1)); fi; }
nocheck() { if grep -q "$2" "$ENV_FILE"; then echo "FAIL $1: found '$2'"; fails=$((fails + 1)); else echo "ok   $1"; fi; }

printf '# Generated\nNORTHBOUND_ENABLED=false\nKELT_OPS_MAX_AGE_DAYS=7\nKELT_OPS_MAX_MB=20\n' > "$ENV_FILE"
NORTHBOUND_ENABLED=true
save
check "owned key rewritten" "NORTHBOUND_ENABLED=true"
check "unowned key kept (days)" "KELT_OPS_MAX_AGE_DAYS=7"
check "unowned key kept (MB)" "KELT_OPS_MAX_MB=20"
save
[ "$(grep -c '^KELT_OPS_MAX_MB=' "$ENV_FILE")" = 1 ] && echo "ok   kept once after two saves" || { echo "FAIL kept key duplicated"; fails=$((fails + 1)); }
nocheck "old owned value gone" "NORTHBOUND_ENABLED=false"

printf 'NORTHBOUND_ENABLED=false\n' > "$ENV_FILE"; save
check "nothing to keep" "NORTHBOUND_ENABLED=true"
rm -f "$ENV_FILE"; save
check "first save without a file" "NORTHBOUND_ENABLED=true"

exit $fails
