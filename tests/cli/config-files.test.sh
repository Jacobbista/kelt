#!/usr/bin/env bash
# Tests for how testbed-config writes .testbed.env, .testbed.secrets and the
# subscribers file: whole-file replacement (never half a file), the previous
# version kept as <file>.prev, restore from it, secrets never deleted and keys
# it does not own kept, subscribers never overwritten.
# Run: bash tests/cli/config-files.test.sh
set -o pipefail  # no -u: the save functions expand every config variable

CLI="$(cd "$(dirname "$0")/../.." && pwd)/testbed-config"
tmp="$(mktemp -d)"; trap 'chmod -R u+w "$tmp"; rm -rf "$tmp"' EXIT
ENV_FILE="$tmp/env"; SECRETS_FILE="$tmp/secrets"; SUBSCRIBERS_FILE="$tmp/subs.json"
SUBSCRIBERS_TEMPLATE="$tmp/template.json"
for fn in write_file_atomic save_config save_secrets do_restore ensure_subscribers_file; do
  eval "$(sed -n "/^${fn}() {/,/^}/p" "$CLI")"
done
fails=0
ok() { echo "ok   $1"; }
bad() { echo "FAIL $1"; fails=$((fails + 1)); }
expect() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }
mode() { stat -c %a "$1"; }

# ── .testbed.env ──────────────────────────────────────────────────────────────
printf 'NORTHBOUND_ENABLED=false\nKELT_OPS_MAX_MB=20\n' > "$ENV_FILE"
NORTHBOUND_ENABLED=true; save_config > /dev/null
expect "env: previous version kept as .prev" "grep -qx NORTHBOUND_ENABLED=false '$ENV_FILE.prev'"
expect "env: new value written" "grep -qx NORTHBOUND_ENABLED=true '$ENV_FILE'"
cp "$ENV_FILE.prev" "$tmp/prev-before"
save_config > /dev/null
expect "env: an unchanged save leaves .prev alone" "cmp -s '$ENV_FILE.prev' '$tmp/prev-before'"
expect "env: no temporary file left" "[ -z \"\$(find '$tmp' -name '.env.*')\" ]"

# A write that cannot complete leaves the file as it was.
cp "$ENV_FILE" "$tmp/env-before"; chmod a-w "$tmp"
NORTHBOUND_ENABLED=false
if save_config > /dev/null 2>&1; then bad "env: failed write reports failure"; else ok "env: failed write reports failure"; fi
chmod u+w "$tmp"
expect "env: failed write leaves the file intact" "cmp -s '$ENV_FILE' '$tmp/env-before'"

# ── .testbed.secrets ──────────────────────────────────────────────────────────
printf 'KEYCLOAK_ADMIN_PASSWORD=old-pass\nEXTRA_SECRET=keep-me\n' > "$SECRETS_FILE"; chmod 600 "$SECRETS_FILE"
KEYCLOAK_ADMIN_PASSWORD=new-pass; save_secrets > /dev/null
expect "secrets: new value written" "grep -qx KEYCLOAK_ADMIN_PASSWORD=new-pass '$SECRETS_FILE'"
expect "secrets: a key it does not own is kept" "grep -qx EXTRA_SECRET=keep-me '$SECRETS_FILE'"
expect "secrets: file is 0600" "[ \"\$(mode '$SECRETS_FILE')\" = 600 ]"
expect "secrets: .prev is 0600" "[ \"\$(mode '$SECRETS_FILE.prev')\" = 600 ]"
expect "secrets: .prev holds the old value" "grep -qx KEYCLOAK_ADMIN_PASSWORD=old-pass '$SECRETS_FILE.prev'"
KEYCLOAK_ADMIN_PASSWORD=""; CAMARA_CLIENT_SECRET=""; CAMARA_API_DEMO_SECRET=""; DASHBOARD_READONLY_SECRET=""
PLACEMENT_EDITOR_PROXY_SECRET=""; APPS_REGISTRY_PASSWORD=""; DASHBOARD_ADMIN_TOKEN=""
save_secrets > /dev/null
expect "secrets: all values empty never deletes the file" "[ -f '$SECRETS_FILE' ]"
expect "secrets: ...and still keeps the other keys" "grep -qx EXTRA_SECRET=keep-me '$SECRETS_FILE'"

# ── restore ───────────────────────────────────────────────────────────────────
printf 'KEYCLOAK_ADMIN_PASSWORD=a-secret\nEXTRA_SECRET=keep-me\n' > "$SECRETS_FILE.prev"; chmod 600 "$SECRETS_FILE.prev"
cp "$SECRETS_FILE" "$tmp/secrets-now"
out="$(do_restore secrets --yes 2>&1)"
expect "restore: the previous version is back" "grep -qx KEYCLOAK_ADMIN_PASSWORD=a-secret '$SECRETS_FILE'"
expect "restore: what it replaced becomes .prev (restore again undoes it)" "cmp -s '$SECRETS_FILE.prev' '$tmp/secrets-now'"
expect "restore: names the changed key" "grep -q KEYCLOAK_ADMIN_PASSWORD <<< \"\$out\""
expect "restore: never prints a value" "! grep -q a-secret <<< \"\$out\""
expect "restore: keeps 0600" "[ \"\$(mode '$SECRETS_FILE')\" = 600 ]"
rm -f "$ENV_FILE.prev"; cp "$ENV_FILE" "$tmp/env-before"
if do_restore env --yes > /dev/null 2>&1; then bad "restore: no .prev is an error"; else ok "restore: no .prev is an error"; fi
expect "restore: no .prev leaves the file alone" "cmp -s '$ENV_FILE' '$tmp/env-before'"
if do_restore bogus --yes > /dev/null 2>&1; then bad "restore: unknown file is an error"; else ok "restore: unknown file is an error"; fi
cp "$SECRETS_FILE" "$tmp/secrets-now"
if do_restore secrets < /dev/null > /dev/null 2>&1; then bad "restore: without --yes and no terminal, refuses"; else ok "restore: without --yes and no terminal, refuses"; fi
expect "restore: refused leaves the file alone" "cmp -s '$SECRETS_FILE' '$tmp/secrets-now'"

# ── subscribers ───────────────────────────────────────────────────────────────
printf '{"subscribers": [{"imsi": "001010000000001", "security": {}}]}\n' > "$SUBSCRIBERS_TEMPLATE"
ensure_subscribers_file > /dev/null
expect "subscribers: generated as valid JSON" "python3 -c 'import json,sys; json.load(open(sys.argv[1]))' '$SUBSCRIBERS_FILE'"
expect "subscribers: generated 0600" "[ \"\$(mode '$SUBSCRIBERS_FILE')\" = 600 ]"
expect "subscribers: no temporary file left" "[ -z \"\$(find '$tmp' -name '.subs.json.*')\" ]"
cp "$SUBSCRIBERS_FILE" "$tmp/subs-before"
ensure_subscribers_file > /dev/null
expect "subscribers: never overwritten" "cmp -s '$SUBSCRIBERS_FILE' '$tmp/subs-before'"

exit $fails
