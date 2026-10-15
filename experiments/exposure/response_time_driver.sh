#!/usr/bin/env bash
# G6 · Where the response time goes. Drive N requests at a fixed rate against the
# deployed CAMARA gateway for one condition and record, per request, the
# end-to-end time AND the x-correlator the gateway coins. The per-hop stage split
# is NOT reconstructed here: northbound instruments each hop (receive/emit ts +
# x-correlator) and KELT aggregates those logs by x-correlator (role split agreed
# G6). Until that instrumentation ships, this records end-to-end + the correlator
# join key and snapshots the pod logs for later aggregation.
#
#   response_time_driver.sh <fresh|hit|local> [count] [rate_per_s]
#
# Conditions (agreed with northbound, revised for 0.19.1; run.sh sets the body):
#   hit     vendor asset, no maxAge, default rate: mostly cache hits
#   fresh   vendor asset, no maxAge, requests more than 5 s apart, with the
#           asset's other consumers closed: both caches cold, each request's
#           hop line says whether it reached the vendor
#   local   synthetic asset (on_request), maxAge=0: the stack without a vendor
# A deterministic WAN-free repro is a LOCAL `make demo` concern (vendor-adapter
# repointed at the schema-driven mock-vendor via WITTRA_BASE_URL), NOT a cluster
# component. 1000 requests at ~5/s by default.
#
# Set the condition in the request body (asset + maxAge); the driver does not
# invent the contract. Confirm path/body from the profiled OpenAPI
# ("$(gateway_url)"/docs). Owner: experiments/exposure/README.md.

source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/lib/common.sh"

COND="${1:?usage: response_time_driver.sh <fresh|hit|local> [count] [rate]}"
COUNT="${2:-1000}"
RATE="${3:-5}"
GW="$(gateway_url)"   # KELT_GATEWAY_URL if set
TOKEN="$(camara_token)"   # KELT_CAMARA_TOKEN if set, else minted from .testbed.secrets
RETRIEVE_PATH="${KELT_RETRIEVE_PATH:-/location-retrieval/v0.5/retrieve}"  # gateway serves v0.5; confirm via /docs
BODY_FILE="${KELT_RETRIEVE_BODY:?set KELT_RETRIEVE_BODY to a JSON body (asset + maxAge for this condition)}"

[ -f "$BODY_FILE" ] || die "retrieve body file not found: $BODY_FILE"
command -v curl >/dev/null || die "curl required"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_DIR="${KELT_RUN_DIR:-$(new_run_dir "response-time" "$STAMP")}"   # run.sh passes its own
mkdir -p "$RUN_DIR"
# The campaign (run.sh response-time) writes provenance once for all conditions.
[ -n "${KELT_RUN_DIR:-}" ] || "$EXP_ROOT/provenance.sh" "$RUN_DIR" "response-time/$COND" >/dev/null
CSV="$RUN_DIR/requests_${COND}.csv"
echo "i,http_status,x_correlator,time_total_s,time_starttransfer_s" >"$CSV"
HDR="$(mktemp)"; trap 'rm -f "$HDR"' EXIT

log "G6 $COND: $COUNT requests at ${RATE}/s → $GW$RETRIEVE_PATH"
# Paced on a schedule (request i starts at t0 + i/RATE), not "sleep after each
# request", which would add the request time to every interval.
PERIOD_NS="$(python3 -c "print(int(1e9/float($RATE)))")"
T0_NS="$(date +%s%N)"
window open "$RUN_DIR" "$COND"
for i in $(seq 1 "$COUNT"); do
  read -r status ttot tstart < <(curl -s -o /dev/null -D "$HDR" \
      -w '%{http_code} %{time_total} %{time_starttransfer}\n' \
      -X POST "$GW$RETRIEVE_PATH" \
      -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
      --data-binary "@$BODY_FILE")
  xcorr="$(grep -i '^x-correlator:' "$HDR" | tail -1 | tr -d '\r' | awk '{print $2}')"
  echo "$i,$status,${xcorr:-},$ttot,$tstart" >>"$CSV"
  wait_ns=$(( T0_NS + i * PERIOD_NS - $(date +%s%N) ))
  [ "$wait_ns" -le 0 ] || sleep "$(printf '%d.%09d' $((wait_ns / 1000000000)) $((wait_ns % 1000000000)))"
done
window close "$RUN_DIR" "$COND"

# Per-hop aggregation is KELT's job, keyed by x-correlator: snapshot the logs of
# the gateway and of every positioning Deployment (engine and adapters, so the
# adapter->vendor span is present) for hop_aggregate.py.
hoplog_snapshot "$RUN_DIR" "$COND"
log "done → $RUN_DIR (end-to-end + x-correlator in $CSV; per-hop logs snapshotted — aggregate with hop_aggregate.py)"
