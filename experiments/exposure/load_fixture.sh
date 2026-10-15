# shellcheck shell=bash
# The synthetic assets of the response-time load condition (`local50`). Every
# change has an undo, and run.sh installs its EXIT trap before the first one.
# Owner: experiments/exposure/README.md.
#
# load_setup adds one asset per load id to the gateway's map (operator token)
# and places each in the room through the gateway; placing an id the synthetic
# adapter does not declare creates that device (northbound 0.19.3). load_undo
# removes the placements, which ends the devices, and takes the assets off the
# map. The synthetic adapter keeps its placements in memory: it runs as one
# replica and is not restarted during the run.

LOAD_PREFIX="${KELT_LOAD_PREFIX:-kelt-load-}"
LOAD_POS_NS="${KELT_POS_NS:-$(plan_value positioning_namespace)}"
LOAD_DEPLOY="${KELT_LOAD_DEPLOY:-synthetic-adapter}"

load_ids() { python3 "$EXP_ROOT/exposure/load_assets.py" ids "$LOAD_PREFIX" "$1" | tr ',' ' '; }

load_store() { # <operator token> <gateway>
  curl -s --max-time 15 -H "Authorization: Bearer $1" "$2/assets"
}
load_put_store() { # <operator token> <gateway>, the store on stdin
  curl -s --max-time 15 -o /dev/null -w '%{http_code}' -X PUT "$2/assets" \
    -H "Authorization: Bearer $1" -H 'Content-Type: application/json' --data-binary @-
}

# The room the synthetic adapter walks (the blueprint's first): "width depth".
load_room() { # <token> <gateway>
  curl -s --max-time 15 -H "Authorization: Bearer $1" "$2/blueprint" \
    | python3 -c 'import json, sys; r = json.load(sys.stdin)["rooms"][0]; print(r["width_m"], r["depth_m"])'
}

# The load ids the gateway's map holds, one per line.
load_assets_in_map() { # <operator token> <gateway>
  load_store "$1" "$2" | python3 -c '
import json, sys
print("\n".join(a["assetId"] for a in json.load(sys.stdin).get("assets", []) if a.get("assetId", "").startswith(sys.argv[1])))' "$LOAD_PREFIX"
}

# The load devices the synthetic adapter lists (GET /devices, through the API
# server's service proxy), one per line.
load_devices_in_adapter() {
  kubectl get --raw "/api/v1/namespaces/$LOAD_POS_NS/services/$LOAD_DEPLOY:8080/proxy/devices" | python3 -c '
import json, sys
print("\n".join(d["id"] for d in json.load(sys.stdin).get("devices", []) if d["id"].startswith(sys.argv[1])))' "$LOAD_PREFIX"
}

# Anything an earlier, aborted run may have left behind, and the one-replica
# condition the placements need.
load_leftovers() { # <gateway>
  local replicas
  replicas="$(kubectl get deploy -n "$LOAD_POS_NS" "$LOAD_DEPLOY" -o jsonpath='{.spec.replicas}' | tr -d '\r')"
  [ "$replicas" = 1 ] || echo "deploy/$LOAD_DEPLOY has $replicas replicas, not 1"
  load_assets_in_map "$(camara_operator_token)" "$1" | grep . | sed "s/^/asset /" || true
  load_devices_in_adapter | grep . | sed "s/^/device /" || true
}

load_setup() { # <n> <token> <gateway>
  local n="$1" token="$2" gw="$3" op code x y id status ids k=0
  ids=($(load_ids "$n"))
  op="$(camara_operator_token)"
  code="$(load_store "$op" "$gw" | python3 "$EXP_ROOT/exposure/load_assets.py" add "$LOAD_PREFIX" "$n" "${KELT_LOAD_ORG:-demo}" \
    | load_put_store "$op" "$gw")"
  [ "$code" = 200 ] || [ "$code" = 204 ] || die "the gateway refused the load assets ($code)"
  while read -r x y; do
    id="${ids[$k]}"; k=$((k + 1))
    code="$(curl -s --max-time 10 -o /dev/null -w '%{http_code}' -X PUT "$gw/assets/$id/placement" \
      -H "Authorization: Bearer $token" -H 'Content-Type: application/json' -d "{\"x\":$x,\"y\":$y}")"
    [ "$code" = 200 ] || die "placing $id answered $code"
  done < <(python3 "$EXP_ROOT/exposure/load_assets.py" points "$n" $(load_room "$token" "$gw"))
  sleep 3
  for id in "${ids[@]}"; do
    read -r status _ < <(retrieve_probe "$token" "$gw" "$id" "{\"device\":{\"assetId\":\"$id\"},\"maxAge\":0}")
    [ "$status" = 200 ] || die "load asset $id answers $status, not 200"
  done
}

load_undo() { # <gateway>
  local op token id code
  op="$(camara_operator_token)" || return 0
  token="$(camara_token)" || return 0
  for id in $(load_assets_in_map "$op" "$1"); do
    code="$(curl -s --max-time 10 -o /dev/null -w '%{http_code}' -X DELETE "$1/assets/$id/placement" \
      -H "Authorization: Bearer $token")"
    [ "$code" = 200 ] || log "WARNING: removing the placement of $id answered $code"
  done
  code="$(load_store "$op" "$1" | python3 "$EXP_ROOT/exposure/load_assets.py" remove "$LOAD_PREFIX" | load_put_store "$op" "$1")"
  [ "$code" = 200 ] || [ "$code" = 204 ] || log "WARNING: removing the load assets answered $code"
}
