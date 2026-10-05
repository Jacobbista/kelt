#!/usr/bin/env bash
# One entry point for the thesis measurements. Each campaign checks what it
# needs, runs, and leaves a summary next to the raw data in
# runs/<campaign>/<utc>/ (provenance.json, window.json, raw/, summary.*).
# Names follow the thesis sections:
#
#   run.sh resource-use idle                     CPU/memory of the testbed at rest (4.10.2, 5.11.3)
#   run.sh verification                          one exchange per profile behaviour (5.11.1)
#   run.sh response-time                         where a location request's time goes (5.11.2)
#   run.sh throughput                            TCP goodput UE <-> measurement server (4.10.1.1)
#   run.sh rtt                                   RTT UE <-> measurement server, core/access split (4.10.1.2)
#   run.sh resume [run-dir]                      continue a network run that did not finish
#   run.sh stop                                  end a network job on the UE, keep what it measured
#   run.sh report                                every run, one line each
#
# The network campaigns measure from the UE named by KELT_UE_SSH (network/campaign.sh). KELT_PILOT=1 marks a
# trial run: recorded, never in the tables (tables.py). Other knobs are listed
# at the top of each campaign function. Owner: experiments/README.md.

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
# shellcheck source=network/campaign.sh
source "$EXP_ROOT/network/campaign.sh"

WHAT="${1:?usage: run.sh <resource-use|verification|response-time|throughput|rtt|resume|stop|report>}"

# ── shared helpers ────────────────────────────────────────────────────────────

retrieve_path() { echo "${KELT_RETRIEVE_PATH:-/location-retrieval/v0.5/retrieve}"; }

# The assets the gateway knows, as "assetId source" lines.
assets() {
  curl -s --max-time 15 -H "Authorization: Bearer $1" "$2/assets" | python3 -c '
import sys, json
d = json.load(sys.stdin)
items = d if isinstance(d, list) else d.get("assets", d.get("items", []))
for a in items:
    for c in a.get("capabilities", []):
        print(a.get("assetId"), c.get("source"))'
}

# One fresh retrieve: prints "<http status> <fix age s or ->".
retrieve_probe() {
  local token="$1" gw="$2" asset="$3" body tmp status
  tmp="$(mktemp)"; body="{\"device\":{\"assetId\":\"$asset\"},\"maxAge\":0}"
  status="$(curl -s --max-time 30 -o "$tmp" -w '%{http_code}' -X POST "$gw$(retrieve_path)" \
    -H "Authorization: Bearer $token" -H 'Content-Type: application/json' --data "$body")"
  python3 - "$status" "$tmp" <<'PY'
import sys, json, datetime
status, path = sys.argv[1], sys.argv[2]
age = "-"
try:
    d = json.load(open(path))
    t = d.get("lastLocationTime")
    if t:
        ts = datetime.datetime.fromisoformat(t.replace("Z", "+00:00"))
        age = f"{(datetime.datetime.now(datetime.timezone.utc) - ts).total_seconds():.0f}"
except Exception:
    pass
print(status, age)
PY
  rm -f "$tmp"
}

# ── resource-use: CPU/memory per pod over measured windows ────────────────────
#   run.sh resource-use idle                 5 min at rest, its own window
#   run.sh resource-use from <run-dir>...    the windows other campaigns recorded
run_resource_use() {
  local mode="${1:?usage: run.sh resource-use idle}" run_dir secs="${KELT_RESOURCE_S:-300}" start end
  [ "$mode" = idle ] || die "run.sh resource-use idle: the load conditions are recorded by the campaigns that load the testbed"
  run_dir="$(begin_run resource-use idle)"
  log "resource-use idle: nothing else must run for $secs s"
  footprint_start idle "$((secs + 60))" "$run_dir/raw/footprint/1"
  window open "$run_dir" idle; start="$(date +%s.%N)"
  sleep "$secs"
  end="$(date +%s.%N)"; window close "$run_dir" idle
  footprint_stop idle "$run_dir/raw/footprint/1"
  footprint_summarise "$run_dir" --window idle "$start" "$end"
  check_run "$run_dir"
  log "resource-use -> $run_dir/footprint.md"
}

# ── verification: one recorded exchange per behaviour of the profile ──────────
run_verification() {
  local token gw run_dir left
  # shellcheck source=exposure/faults.sh
  source "$EXP_ROOT/exposure/faults.sh"
  left="$(faults_leftovers)"
  [ -z "$left" ] || die "leftover from an earlier run, restore it first: $(echo $left)"
  token="$(camara_token)"; gw="$(gateway_url)"
  curl -s --max-time 10 -o /dev/null -w '%{http_code}' "$gw/health" | grep -q '^200$' || die "gateway $gw/health is not 200"
  run_dir="$(begin_run verification "profile,data,fault")"
  trap faults_undo_all EXIT
  window open "$run_dir" verification
  KELT_CAMARA_TOKEN="$token" python3 "$EXP_ROOT/exposure/verification.py" --gateway "$gw" --run-dir "$run_dir" \
    --injections "${KELT_FAULT_INJECTIONS:-3}" --fault-timeout "${KELT_FAULT_TIMEOUT_S:-120}" \
    ${KELT_SKIP_FAULTS:+--skip-faults} | tee "$run_dir/console.txt"
  window close "$run_dir" verification
  faults_undo_all
  left="$(faults_leftovers)"
  [ -z "$left" ] || log "WARNING: still left after undo: $(echo $left)"
  log "verification -> $run_dir/summary.md"
}

# ── response-time: where the time of a location request goes ─────────────────
#   hit    no maxAge: the gateway may answer from its cache
#   fresh  the vendor asset, maxAge=0 (the adapter still keeps the vendor answer
#          for cacheTtl s; the summary counts the requests that reached the vendor)
#   local  the synthetic asset, maxAge=0: the stack with no external call
run_response_time() {
  local token gw run_dir cond asset body src
  local n="${KELT_LAT_COUNT:-1000}" rate="${KELT_LAT_RATE:-5}" warm="${KELT_LAT_WARMUP:-50}"
  token="$(camara_token)"; gw="$(gateway_url)"
  curl -s --max-time 10 -o /dev/null -w '%{http_code}' "$gw/health" | grep -q '^200$' || die "gateway $gw/health is not 200"
  run_dir="$(begin_run response-time "${KELT_LAT_CONDITIONS:-hit fresh local}")"
  for cond in ${KELT_LAT_CONDITIONS:-hit fresh local}; do
    case "$cond" in
      hit|fresh) src=wittra ;;
      local)     src=synthetic ;;
      *) die "unknown condition $cond (hit|fresh|local)" ;;
    esac
    asset="$(assets "$token" "$gw" | awk -v s="$src" '$2==s{print $1; exit}')"
    [ -n "$asset" ] || die "no asset with source $src for condition $cond"
    case "$cond" in
      hit) body="{\"device\":{\"assetId\":\"$asset\"}}" ;;
      *)   body="{\"device\":{\"assetId\":\"$asset\"},\"maxAge\":0}" ;;
    esac
    read -r status _ < <(retrieve_probe "$token" "$gw" "$asset")
    [ "$status" = 200 ] || die "$cond: $asset answers $status, not 200 (synthetic asset not placed? place it first)"
    mkdir -p "$run_dir/$cond"; echo "$body" >"$run_dir/$cond/body.json"
    log "response-time $cond: $n requests at ${rate}/s on $asset"
    KELT_RUN_DIR="$run_dir/$cond" KELT_CAMARA_TOKEN="$token" KELT_RETRIEVE_BODY="$run_dir/$cond/body.json" \
      "$EXP_ROOT/exposure/response_time_driver.sh" "$cond" "$n" "$rate"
    python3 "$EXP_ROOT/exposure/hop_aggregate.py" "$run_dir/$cond" --warmup "$warm" \
      --json "$run_dir/$cond/stages.json" >"$run_dir/$cond/aggregate.txt"
    python3 "$EXP_ROOT/exposure/response_time_summary.py" "$run_dir/$cond" vendor >/dev/null
  done
  log "response-time -> $run_dir"
}

case "$WHAT" in
  resource-use) shift; run_resource_use "$@" ;;
  verification) run_verification ;;
  response-time) run_response_time ;;
  throughput) run_throughput ;;
  rtt) run_rtt ;;
  resume) run_resume "${2:-}" ;;
  stop) run_stop ;;
  report) exec "$EXP_ROOT/report.sh" "${2:-}" ;;
  *) die "unknown: $WHAT (resource-use | verification | response-time | throughput | rtt | resume | stop | report)" ;;
esac
