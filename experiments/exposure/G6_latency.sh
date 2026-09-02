#!/usr/bin/env bash
# G6 · Where the response time goes. Drive N requests at a fixed rate against the
# deployed CAMARA gateway for one condition and record, per request, the
# end-to-end time AND the x-correlator the gateway coins. The per-hop stage split
# is NOT reconstructed here: northbound instruments each hop (receive/emit ts +
# x-correlator) and KELT aggregates those logs by x-correlator (role split agreed
# G6). Until that instrumentation ships, this records end-to-end + the correlator
# join key and snapshots the pod logs for later aggregation.
#
#   G6_latency.sh <fresh|hit|local> [count] [rate_per_s]
#
# Conditions (agreed with northbound):
#   fresh   maxAge=0 in the body → bypass cache → measures the real pipeline
#           (for a wittra asset this includes the WAN; subtract the adapter→vendor
#            span from the per-hop trace to get the WAN-free number — no mock deploy)
#   hit     served from cache → ~0, a separate trivial number
#   local   a local-source asset (wifi/mock) → pipeline with no external call
# A deterministic WAN-free repro is a LOCAL `make demo` concern (vendor-adapter
# repointed at the schema-driven mock-vendor via WITTRA_BASE_URL), NOT a cluster
# component. 1000 requests at ~5/s by default.
#
# Set the condition in the request body (asset + maxAge); the driver does not
# invent the contract. Confirm path/body from the profiled OpenAPI
# ("$(gateway_url)"/docs). Owner: experiments/exposure/README.md.

source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/lib/common.sh"

COND="${1:?usage: G6_latency.sh <fresh|hit|local> [count] [rate]}"
COUNT="${2:-1000}"
RATE="${3:-5}"
GW="$(gateway_url)"
TOKEN="${KELT_CAMARA_TOKEN:?set KELT_CAMARA_TOKEN to a valid CAMARA access token (do not commit it)}"
RETRIEVE_PATH="${KELT_RETRIEVE_PATH:-/location-retrieval/v0.5/retrieve}"  # gateway serves v0.5; confirm via /docs
BODY_FILE="${KELT_RETRIEVE_BODY:?set KELT_RETRIEVE_BODY to a JSON body (asset + maxAge for this condition)}"

[ -f "$BODY_FILE" ] || die "retrieve body file not found: $BODY_FILE"
command -v curl >/dev/null || die "curl required"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_DIR="$(new_run_dir "G6_latency" "$STAMP")"
"$EXP_ROOT/provenance.sh" "$RUN_DIR" "G6/$COND" >/dev/null
CSV="$RUN_DIR/requests_${COND}.csv"
echo "i,http_status,x_correlator,time_total_s,time_starttransfer_s" >"$CSV"
HDR="$(mktemp)"; trap 'rm -f "$HDR"' EXIT

log "G6 $COND: $COUNT requests at ${RATE}/s → $GW$RETRIEVE_PATH"
SLEEP="$(python3 -c "print(1.0/float($RATE))")"
for i in $(seq 1 "$COUNT"); do
  read -r status ttot tstart < <(curl -s -o /dev/null -D "$HDR" \
      -w '%{http_code} %{time_total} %{time_starttransfer}\n' \
      -X POST "$GW$RETRIEVE_PATH" \
      -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
      --data-binary "@$BODY_FILE")
  xcorr="$(grep -i '^x-correlator:' "$HDR" | tail -1 | tr -d '\r' | awk '{print $2}')"
  echo "$i,$status,${xcorr:-},$ttot,$tstart" >>"$CSV"
  sleep "$SLEEP"
done

# Per-hop aggregation is KELT's job, keyed by x-correlator. northbound emits the
# per-hop "hop" log line from v0.9.0 (gateway, engine, and every adapter). Snapshot
# the pod logs for the run window so g6_aggregate.py can join them later; the
# x-correlator column above is the join key. Capture the gateway AND every
# deployment in the positioning namespace (engine + all adapters), so the
# adapter->vendor (WAN) span is present and WAN-free can be subtracted.
kubectl logs -n "$CAMARA_NS" "deploy/${KELT_CAMARA_SVC:-camara-gateway}" --since=1h \
  >"$RUN_DIR/hoplog_gateway_${COND}.txt" 2>/dev/null || true
POS_NS="${KELT_POS_NS:-positioning}"
for dep in $(kubectl get deploy -n "$POS_NS" -o name 2>/dev/null); do
  name="${dep##*/}"
  kubectl logs -n "$POS_NS" "$dep" --since=1h >"$RUN_DIR/hoplog_${name}_${COND}.txt" 2>/dev/null || true
done
log "done → $RUN_DIR (end-to-end + x-correlator in $CSV; per-hop logs snapshotted — aggregate with g6_aggregate.py)"
