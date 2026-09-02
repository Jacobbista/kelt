#!/usr/bin/env bash
# G3 · Private-asset contract conformance. The claim is directional:
# response-compatible, request-incompatible. No new instrumentation — a client
# only. Since northbound formalised the machine-readable profile, the old
# divergences are conformant, so these cases VERIFY conformance (they no longer
# record mismatches). Expected outcomes below track the profile, not memory —
# the authority is 5g-northbound/spec/private-profile (see README).
#
#   G3_conformance.sh <cases-dir>
#
# <cases-dir> holds one <case>.json request body per case, plus an optional
# <case>.meta file with "expect_status=<n>" and "token=<none|norole|crosstenant|
# valid>". Suggested cases and CONFORMANT expectations:
#   public_phone   public identifier (telephone)     → 422 UNSUPPORTED_IDENTIFIER
#   asset_id       private asset identifier          → 200
#   nai_private    network access id, private scheme  → 200, same asset
#   no_token       → 401 · no_role → 403 · cross_tenant → 404
#   unknown_asset  → 404 · no_current_fix → 422 UNABLE_TO_LOCATE
#   (engine unreachable → 503 UNAVAILABLE; every response carries x-correlator)
#
# Confirm the retrieve path and body shape from the deployment OpenAPI (generated
# from the profile): "$(gateway_url)"/docs → set KELT_RETRIEVE_PATH if it differs.
# Owner: experiments/exposure/README.md.

source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/lib/common.sh"

CASES_DIR="${1:?usage: G3_conformance.sh <cases-dir>}"
[ -d "$CASES_DIR" ] || die "cases dir not found: $CASES_DIR"
GW="$(gateway_url)"
RETRIEVE_PATH="${KELT_RETRIEVE_PATH:-/location-retrieval/v0.5/retrieve}"  # gateway serves v0.5; confirm via /docs
VALID_TOKEN="${KELT_CAMARA_TOKEN:-}"          # required for cases whose meta says token=valid
CROSS_TOKEN="${KELT_CAMARA_TOKEN_CROSS:-}"    # a token from another org, for cross_tenant

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_DIR="$(new_run_dir "G3_conformance" "$STAMP")"
"$EXP_ROOT/provenance.sh" "$RUN_DIR" "G3_conformance" >/dev/null
CSV="$RUN_DIR/conformance.csv"
echo "case,token_kind,expect_status,http_status,camara_code,pass" >"$CSV"

for body in "$CASES_DIR"/*.json; do
  [ -f "$body" ] || continue
  case="$(basename "$body" .json)"
  meta="$CASES_DIR/$case.meta"
  expect="$(sed -n 's/^expect_status=//p' "$meta" 2>/dev/null || true)"
  tkind="$(sed -n 's/^token=//p' "$meta" 2>/dev/null || echo valid)"
  auth=()
  case "$tkind" in
    none)        auth=() ;;
    crosstenant) auth=(-H "Authorization: Bearer $CROSS_TOKEN") ;;
    *)           auth=(-H "Authorization: Bearer $VALID_TOKEN") ;;
  esac
  resp="$(curl -s -w '\n%{http_code}' -X POST "$GW$RETRIEVE_PATH" \
        "${auth[@]}" -H 'Content-Type: application/json' --data-binary "@$body")"
  status="$(printf '%s' "$resp" | tail -1)"
  code="$(printf '%s' "$resp" | sed '$d' | python3 -c 'import sys,json;
try: print(json.load(sys.stdin).get("code",""))
except Exception: print("")' 2>/dev/null || true)"
  pass="?"; [ -n "$expect" ] && { [ "$status" = "$expect" ] && pass="yes" || pass="no"; }
  echo "$case,$tkind,${expect:-},$status,$code,$pass" >>"$CSV"
  printf '%s' "$resp" | sed '$d' >"$RUN_DIR/${case}.response.json"
done

log "conformance → $CSV"
column -s, -t "$CSV" >&2 || cat "$CSV" >&2
