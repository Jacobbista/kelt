# shellcheck shell=bash
# A principal of another organisation for one recorded example: a temporary
# service-account client in the realm, `KELT_OTHER_ORG_CLIENT` (default
# kelt-exp-other-org), whose service account carries `org=KELT_OTHER_ORG`
# (default other) and the realm role camara-location-read, with the same org
# mapper as camara-api-demo. Created and deleted through Keycloak's admin API;
# deleting the client deletes its service account. The client secret and the
# tokens live in files only this user reads and are never printed.
# Owner: experiments/exposure/README.md.

OO_CLIENT="${KELT_OTHER_ORG_CLIENT:-kelt-exp-other-org}"
OO_ORG="${KELT_OTHER_ORG:-other}"
OO_REALM="${KELT_KC_REALM:-5g-testbed}"
OO_KC="${KELT_KC_URL:-http://$(node_ip):$(plan_value keycloak_nodeport)/auth}"

oo_admin() { # an admin access token on stdout
  local pw
  pw="$(grep '^KEYCLOAK_ADMIN_PASSWORD' "$REPO_ROOT/.testbed.secrets" | cut -d= -f2- | tr -d "\"' ")"
  [ -n "$pw" ] || die "KEYCLOAK_ADMIN_PASSWORD not in .testbed.secrets"
  curl -s --max-time 15 -d grant_type=password -d client_id=admin-cli -d "username=${KELT_KC_ADMIN_USER:-admin}" \
    --data-urlencode "password=$pw" "$OO_KC/realms/master/protocol/openid-connect/token" \
    | python3 -c 'import json, sys; print(json.load(sys.stdin).get("access_token", ""))' | grep . \
    || die "no Keycloak admin token"
}

oo_client_id() { # <admin token>: the client's internal id, empty when absent
  curl -s --max-time 15 -H "Authorization: Bearer $1" "$OO_KC/admin/realms/$OO_REALM/clients?clientId=$OO_CLIENT" \
    | python3 -c 'import json, sys; d = json.load(sys.stdin); print(d[0]["id"] if d else "")'
}

oo_leftover() { [ -z "$(oo_client_id "$(oo_admin)")" ] || echo "Keycloak client $OO_CLIENT"; }

# oo_setup <token file>: creates the client and writes a token of it.
oo_setup() {
  local out="$1" at secret_file id su role code
  at="$(oo_admin)"
  secret_file="$(umask 077; mktemp)"
  python3 -c 'import secrets; print(secrets.token_urlsafe(32))' >"$secret_file"
  code="$(python3 - "$OO_CLIENT" "$secret_file" <<'PY' | curl -s --max-time 15 -o /dev/null -w '%{http_code}' -X POST \
      -H "Authorization: Bearer $at" -H 'Content-Type: application/json' --data-binary @- "$OO_KC/admin/realms/$OO_REALM/clients"
import json, sys
print(json.dumps({
    "clientId": sys.argv[1], "enabled": True, "publicClient": False, "serviceAccountsEnabled": True,
    "standardFlowEnabled": False, "directAccessGrantsEnabled": False, "secret": open(sys.argv[2]).read().strip(),
    "protocolMappers": [{"name": "org", "protocol": "openid-connect", "protocolMapper": "oidc-usermodel-attribute-mapper",
                         "config": {"user.attribute": "org", "claim.name": "org", "jsonType.label": "String",
                                    "id.token.claim": "true", "access.token.claim": "true",
                                    "userinfo.token.claim": "true"}}]}))
PY
)"
  [ "$code" = 201 ] || { rm -f "$secret_file"; die "creating $OO_CLIENT answered $code"; }
  id="$(oo_client_id "$at")"
  su="$(curl -s --max-time 15 -H "Authorization: Bearer $at" "$OO_KC/admin/realms/$OO_REALM/clients/$id/service-account-user" \
    | python3 -c 'import json, sys; print(json.load(sys.stdin)["id"])')"
  curl -s --max-time 15 -H "Authorization: Bearer $at" "$OO_KC/admin/realms/$OO_REALM/users/$su" \
    | python3 -c 'import json, sys; u = json.load(sys.stdin); u["attributes"] = {"org": [sys.argv[1]]}; print(json.dumps(u))' "$OO_ORG" \
    | curl -s --max-time 15 -o /dev/null -X PUT -H "Authorization: Bearer $at" -H 'Content-Type: application/json' \
        --data-binary @- "$OO_KC/admin/realms/$OO_REALM/users/$su"
  role="$(curl -s --max-time 15 -H "Authorization: Bearer $at" "$OO_KC/admin/realms/$OO_REALM/roles/camara-location-read")"
  code="$(echo "[$role]" | curl -s --max-time 15 -o /dev/null -w '%{http_code}' -X POST -H "Authorization: Bearer $at" \
    -H 'Content-Type: application/json' --data-binary @- "$OO_KC/admin/realms/$OO_REALM/users/$su/role-mappings/realm")"
  [ "$code" = 204 ] || { rm -f "$secret_file"; die "granting camara-location-read answered $code"; }
  curl -s --max-time 15 -d grant_type=client_credentials -d "client_id=$OO_CLIENT" \
    --data-urlencode "client_secret=$(cat "$secret_file")" "$OO_KC/realms/$OO_REALM/protocol/openid-connect/token" \
    | python3 -c 'import json, sys; print(json.load(sys.stdin).get("access_token", ""))' >"$out"
  rm -f "$secret_file"
  [ -s "$out" ] && grep -q . "$out" || die "no token for $OO_CLIENT"
}

oo_undo() {
  local at id
  at="$(oo_admin)" || return 0
  id="$(oo_client_id "$at")"
  [ -z "$id" ] || curl -s --max-time 15 -o /dev/null -X DELETE -H "Authorization: Bearer $at" \
    "$OO_KC/admin/realms/$OO_REALM/clients/$id" || log "WARNING: $OO_CLIENT was not deleted"
}
