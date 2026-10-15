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
# One retrieve with the body a condition will send ($4; default: the asset, no
# maxAge). Prints the status and the age of the position in seconds.
retrieve_probe() {
  local token="$1" gw="$2" asset="$3" body="${4:-}" tmp status
  tmp="$(mktemp)"; [ -n "$body" ] || body="{\"device\":{\"assetId\":\"$asset\"}}"
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
#   rest     nothing sent, KELT_RESOURCE_S (300 s): the footprint at rest, and
#            the WiFi scans the adapter received (its hop lines, one per
#            ingest; its /devices at the start and the end says whether a
#            scanner was located); with KELT_SCANNER_SSH, the scanner's journal
#            of the window too (unit KELT_SCANNER_UNIT, default scanner)
#   hit      the vendor asset, no maxAge, at the default rate: mostly gateway and
#            adapter cache hits
#   fresh    the vendor asset, no maxAge, one request every 1/rate s with rate below
#            1/5 s, past both 5 s caches. Each request's hop line says cache hit
#            or miss; the summary counts them. Under northbound 0.19 maxAge=0 is
#            refused for a position that is not current by the source's
#            declaration, so it cannot select the full path.
#   local    the synthetic asset (reporting on_request, no adapter cache), maxAge=0:
#            the stack with no external call; placed for the run if it has no
#            position, and removed after only if placed here
#   local50  KELT_LAT_LOAD_ASSETS (50) synthetic assets, each asked once a second
#            with maxAge=0, open loop (exposure/response_time_load.py), for
#            KELT_LAT_LOAD_WARMUP_S (10) + KELT_LAT_LOAD_S (60) s; the assets are
#            added and placed for the condition, and removed after
#            (exposure/load_fixture.sh)
# Close the vendor asset's other consumers for the whole campaign (location-app
# map and detail panel, position-stream clients, dashboards): they keep the
# adapter's cache warm, and they load the stack. The footprint samplers run
# from the first condition to the last; each condition's window starts after
# its warm-up. The summary splits the traces by HTTP status and by where they
# were served (gateway cache, adapter cache, vendor) from their hop lines.
RT_GW=""; RT_PLACED=""; RT_LOAD=0
rt_undo() {
  [ -z "$RT_PLACED" ] || curl -s --max-time 10 -o /dev/null -X DELETE "$RT_GW/assets/$RT_PLACED/placement" \
    -H "Authorization: Bearer $(camara_token)" || true
  RT_PLACED=""
  [ "$RT_LOAD" = 0 ] || load_undo "$RT_GW" || log "WARNING: the load assets were not fully removed: run.sh response-time refuses to start until they are"
  RT_LOAD=0
}

# The WiFi adapter's devices with the time of their last scan, as JSON, read
# through the API server's service proxy: a process started in the pod would be
# counted in the pod's own CPU.
wifi_seen() {
  kubectl get --raw "/api/v1/namespaces/$LOAD_POS_NS/services/${KELT_WIFI_DEPLOY:-wifi-adapter}:8080/proxy/devices" \
    | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except ValueError:
    print("null"); sys.exit()
print(json.dumps([{"id": x["id"], "lastSeen": x.get("lastSeen"), "located": x.get("position") is not None}
                  for x in d.get("devices", [])]))'
}

# window_args <dir> <label> <skip s>: "--window <label> <start> <end>" in epoch
# seconds, from the condition's window.json, the first <skip> s left out.
window_args() {
  (cd "$EXP_ROOT" && python3 -c '
import datetime, sys
from lib.runmeta import windows
w = next(x for x in windows(sys.argv[1]) if x["label"] == sys.argv[2])
t = lambda k: datetime.datetime.strptime(w[k], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc).timestamp()
print("--window", sys.argv[2], t("start_utc") + float(sys.argv[3]), t("end_utc"))' "$1" "$2" "$3")
}

run_response_time() {
  local token gw run_dir cond asset body src left fp budget cn cr cw status
  local conds="${KELT_LAT_CONDITIONS:-rest hit fresh local local50}"
  local n="${KELT_LAT_COUNT:-1000}" rate="${KELT_LAT_RATE:-5}" warm="${KELT_LAT_WARMUP:-50}"
  local fresh_n="${KELT_LAT_FRESH_COUNT:-300}" fresh_rate="${KELT_LAT_FRESH_RATE:-0.15}"
  local rest_s="${KELT_RESOURCE_S:-300}" load_n="${KELT_LAT_LOAD_ASSETS:-50}"
  local load_rate="${KELT_LAT_LOAD_RATE:-${KELT_LAT_LOAD_ASSETS:-50}}" load_s="${KELT_LAT_LOAD_S:-60}" load_warm_s="${KELT_LAT_LOAD_WARMUP_S:-10}"
  local fpwin=()
  # shellcheck source=exposure/load_fixture.sh
  source "$EXP_ROOT/exposure/load_fixture.sh"
  gw="$(gateway_url)"
  curl -s --max-time 10 -o /dev/null -w '%{http_code}' "$gw/health" | grep -q '^200$' || die "gateway $gw/health is not 200"
  if [[ " $conds " == *" local50 "* ]]; then
    left="$(load_leftovers "$gw")"
    [ -z "$left" ] || die "leftover from an earlier run, remove it first: $(echo $left)"
  fi
  run_dir="$(begin_run response-time "$conds")"
  RT_GW="$gw"; trap rt_undo EXIT
  # The samplers stop on their own after the longest the campaign can take.
  budget="$(python3 -c "print(int($rest_s + 2 * $n / $rate + $fresh_n / $fresh_rate + $load_s + $load_warm_s + 1800))")"
  fp="rt-$(basename "$run_dir")"
  footprint_start "$fp" "$budget" "$run_dir/raw/footprint/1"
  for cond in $conds; do
    # Tokens live an hour and the campaign is longer: one per condition.
    token="$(camara_token)"
    mkdir -p "$run_dir/$cond"
    cn="$n"; cr="$rate"; cw="$warm"
    case "$cond" in
      rest)
        log "response-time rest: nothing sent for $rest_s s"
        wifi_seen >"$run_dir/rest/wifi-devices-start.json"
        window open "$run_dir/rest" rest; sleep "$rest_s"; window close "$run_dir/rest" rest
        wifi_seen >"$run_dir/rest/wifi-devices-end.json"
        # The adapter's hop lines: one per scan ingest (northbound 0.19.4).
        hoplog_snapshot "$run_dir/rest" rest
        # A WiFi scanner's own log over the window (KELT_SCANNER_SSH, read only,
        # messages only): the check compares its scans with the adapter's lines.
        if [ -n "${KELT_SCANNER_SSH:-}" ]; then
          read -r _ _ s e < <(window_args "$run_dir/rest" rest 0)
          ssh -o BatchMode=yes -o ConnectTimeout=10 "$KELT_SCANNER_SSH" \
            "journalctl -u ${KELT_SCANNER_UNIT:-scanner} --since @${s%.*} --until @${e%.*} -o cat --no-pager" \
            >"$run_dir/rest/scanner-journal.txt" 2>/dev/null || log "WARNING: the scanner's journal could not be read"
        fi
        fpwin+=($(window_args "$run_dir/rest" rest 0))
        continue ;;
      hit|fresh) src=wittra ;;
      local|local50) src=synthetic ;;
      *) die "unknown condition $cond (rest|hit|fresh|local|local50)" ;;
    esac
    if [ "$cond" = local50 ]; then
      log "response-time local50: adding $load_n synthetic assets"
      RT_LOAD=1; load_setup "$load_n" "$token" "$gw"
      python3 "$EXP_ROOT/exposure/load_assets.py" bodies "$LOAD_PREFIX" "$load_n" >"$run_dir/$cond/bodies.jsonl"
      cw="$(python3 -c "print(int($load_rate * $load_warm_s))")"
      cn="$(python3 -c "print(int($load_rate * ($load_warm_s + $load_s)))")"; cr="$load_rate"
      log "response-time local50: $cn requests at ${cr}/s over $load_n assets, open loop"
      KELT_CAMARA_TOKEN="$token" python3 "$EXP_ROOT/exposure/response_time_load.py" --gateway "$gw" \
        --path "$(retrieve_path)" --bodies "$run_dir/$cond/bodies.jsonl" --rate "$cr" --count "$cn" \
        --run-dir "$run_dir/$cond" --condition "$cond"
      hoplog_snapshot "$run_dir/$cond" "$cond"
      rt_undo
    else
      asset="$(assets "$token" "$gw" | awk -v s="$src" '$2==s && $1 !~ /^'"$LOAD_PREFIX"'/{print $1; exit}')"
      [ -n "$asset" ] || die "no asset with source $src for condition $cond"
      case "$cond" in
        hit)   body="{\"device\":{\"assetId\":\"$asset\"}}" ;;
        fresh) body="{\"device\":{\"assetId\":\"$asset\"}}"; cn="$fresh_n"; cr="$fresh_rate" ;;
        local) body="{\"device\":{\"assetId\":\"$asset\"},\"maxAge\":0}"
               # Placed only when it has no position, removed only if placed here
               # (also on exit), so a demo placement is left as found.
               read -r status _ < <(retrieve_probe "$token" "$gw" "$asset" "$body")
               if [ "$status" = 422 ] && [ "$(curl -s --max-time 10 -o /dev/null -w '%{http_code}' -X PUT \
                    "$gw/assets/$asset/placement" -H "Authorization: Bearer $token" \
                    -H 'Content-Type: application/json' -d '{"x":2.0,"y":2.0}')" = 200 ]; then
                 RT_PLACED="$asset"
                 sleep 3
               fi ;;
      esac
      read -r status _ < <(retrieve_probe "$token" "$gw" "$asset" "$body")
      [ "$status" = 200 ] || die "$cond: $asset answers $status, not 200"
      # The probe itself fills the gateway's 5 s cache: let it expire before a
      # condition whose first request must not be served from it.
      [ "$cond" != fresh ] || sleep 6
      echo "$body" >"$run_dir/$cond/body.json"
      log "response-time $cond: $cn requests at ${cr}/s on $asset"
      KELT_RUN_DIR="$run_dir/$cond" KELT_CAMARA_TOKEN="$token" KELT_GATEWAY_URL="$gw" \
        KELT_RETRIEVE_BODY="$run_dir/$cond/body.json" \
        "$EXP_ROOT/exposure/response_time_driver.sh" "$cond" "$cn" "$cr"
      [ "$cond" != local ] || rt_undo
    fi
    python3 "$EXP_ROOT/exposure/hop_aggregate.py" "$run_dir/$cond" --warmup "$cw" \
      --json "$run_dir/$cond/stages.json" >"$run_dir/$cond/aggregate.txt"
    python3 "$EXP_ROOT/exposure/response_time_summary.py" "$run_dir/$cond" vendor >/dev/null
    fpwin+=($(window_args "$run_dir/$cond" "$cond" "$(python3 -c "print($cw / $cr)")"))
  done
  footprint_stop "$fp" "$run_dir/raw/footprint/1"
  footprint_summarise "$run_dir" "${fpwin[@]}"
  check_run "$run_dir"
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
