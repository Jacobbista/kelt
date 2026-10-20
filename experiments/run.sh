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
# Every condition but rest is sent open loop (exposure/response_time_load.py):
# KELT_LAT_WARMUP_S (10) s of warm-up, discarded, then at least
# KELT_LAT_MIN_COUNT (1000) requests and at least KELT_LAT_MIN_S (60) s.
#   rest            nothing sent, KELT_RESOURCE_S (300 s): the footprint at rest,
#                   and the WiFi scans the adapter received (its hop lines, one
#                   per ingest; its /devices at the start and the end says
#                   whether a scanner was located); with KELT_SCANNER_SSH, the
#                   scanner's journal of the window too (unit KELT_SCANNER_UNIT,
#                   default scanner)
#   wittra-cached   the vendor asset, no maxAge, KELT_LAT_CACHED_RATE (5/s):
#                   mostly answers from the gateway's cache
#   wittra-cloud    the vendor asset, no maxAge, KELT_LAT_CLOUD_RATE (0.15/s),
#                   every request past both 5 s caches. Each request's hop line
#                   says cache hit or miss; the summary counts them. Under
#                   northbound 0.19 maxAge=0 is refused for a position that is
#                   not current by the source's declaration, so it cannot select
#                   the full path.
#   synthetic-<R>   KELT_LAT_LOAD_ASSETS (50) synthetic assets, maxAge=0, R
#                   requests per second in total, each asset asked in turn
#   stream-<N>      the same assets, no request: N clients on the gateway's
#                   /positions/stream (exposure/stream_client.py) for
#                   KELT_LAT_WARMUP_S of warm-up, then KELT_STREAM_S (300) s
#   examples        the recorded examples (exposure/examples.py) on the vendor
#                   asset; for the one with another organisation's token, a
#                   temporary Keycloak client is created and deleted
#                   (exposure/other_org.sh)
# The load assets are added and placed before the first synthetic or stream
# condition and removed after the last (exposure/load_fixture.sh). Each
# synthetic condition also bounds the offset between the gateway's clock and
# this host's (exposure/clock_offset.py), on which the stream's ages depend;
# stream.json records the engine's broadcast interval (WEBSOCKET_INTERVAL_MS).
# Close the vendor asset's other consumers for the whole campaign (location-app
# map and detail panel, position-stream clients, dashboards): they keep the
# adapter's cache warm, and they load the stack. The footprint samplers run
# from the first condition to the last; each condition's window starts after
# its warm-up. The hop logs are followed for each condition (hoplog_start).
# The summary splits the traces by HTTP status and by where they were served
# (gateway cache, adapter cache, vendor) from their hop lines.
RT_GW=""; RT_LOAD=0; RT_OO=0; RT_TOKEN=""; RT_TOKEN_PID=""
rt_undo() {
  [ "$RT_LOAD" = 0 ] || load_undo "$RT_GW" || log "WARNING: the load assets were not fully removed: run.sh response-time refuses to start until they are"
  RT_LOAD=0
}
rt_exit() {
  rt_undo
  [ "$RT_OO" = 0 ] || oo_undo
  # The renewer's sleep first: it would hold the caller's output open.
  [ -z "$RT_TOKEN_PID" ] || { pkill -P "$RT_TOKEN_PID" sleep; kill "$RT_TOKEN_PID"; } 2>/dev/null || true
  [ -z "$RT_TOKEN" ] || rm -f "$RT_TOKEN" "$RT_TOKEN.other"
}

# The bearer token in a file only this user reads, renewed every 20 min (a
# token lives an hour and wittra-cloud takes about two).
rt_token_start() {
  RT_TOKEN="$(umask 077; mktemp "$FOOTPRINT_TMP/kelt-rt-token.XXXXXX")"
  camara_token >"$RT_TOKEN" || die "no CAMARA token"
  ( while sleep 1200; do
      if t="$(camara_token)"; then printf '%s\n' "$t" >"$RT_TOKEN.new" && mv "$RT_TOKEN.new" "$RT_TOKEN"
      else log "WARNING: the CAMARA token was not renewed"; fi
    done ) &
  RT_TOKEN_PID=$!
}

# rt_plan <condition>: "rate warm-up measured" for an open-loop condition.
rt_plan() {
  local rate
  case "$1" in
    wittra-cached) rate="${KELT_LAT_CACHED_RATE:-5}" ;;
    wittra-cloud)  rate="${KELT_LAT_CLOUD_RATE:-0.15}" ;;
    synthetic-*)   rate="${1#synthetic-}" ;;
    *) die "unknown condition $1 (rest | wittra-cached | wittra-cloud | synthetic-<rate> | stream-<clients>)" ;;
  esac
  python3 -c "import math, sys; r = float(sys.argv[1])
print(sys.argv[1], math.ceil(r * float(sys.argv[2])), max(int(sys.argv[3]), math.ceil(r * float(sys.argv[4]))))" \
    "$rate" "${KELT_LAT_WARMUP_S:-10}" "${KELT_LAT_MIN_COUNT:-1000}" "${KELT_LAT_MIN_S:-60}"
}

# rt_send <run dir> <condition> <bodies file>: one open-loop condition, its hop
# logs, aggregation and summary; appends its footprint window to RT_FPWIN.
rt_send() {
  local run_dir="$1" cond="$2" bodies="$3" rate warm n tag
  read -r rate warm n < <(rt_plan "$cond")
  tag="$(basename "$run_dir")-$cond"
  log "response-time $cond: $warm warm-up + $n requests at ${rate}/s, open loop"
  hoplog_start "$tag" "$(python3 -c "print(int(($warm + $n) / $rate) + 300)")"
  python3 "$EXP_ROOT/exposure/response_time_load.py" --gateway "$RT_GW" --path "$(retrieve_path)" \
    --bodies "$bodies" --rate "$rate" --count "$((warm + n))" --warmup "$warm" --token-file "$RT_TOKEN" \
    --run-dir "$run_dir/$cond" --condition "$cond"
  hoplog_stop "$tag" "$run_dir/$cond" "$cond"
  python3 "$EXP_ROOT/exposure/hop_aggregate.py" "$run_dir/$cond" --warmup "$warm" \
    --json "$run_dir/$cond/stages.json" >"$run_dir/$cond/aggregate.txt"
  python3 "$EXP_ROOT/exposure/response_time_summary.py" "$run_dir/$cond" vendor >/dev/null
  case "$cond" in synthetic-*) python3 "$EXP_ROOT/exposure/clock_offset.py" "$run_dir/$cond" >/dev/null ;; esac
  RT_FPWIN+=($(window_args "$run_dir/$cond" "$cond" "$(python3 -c "print($warm / $rate)")"))
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

RT_FPWIN=()
run_response_time() {
  local gw run_dir cond asset left fp budget status rate warm n s e interval load=0
  local conds="${KELT_LAT_CONDITIONS:-rest wittra-cached wittra-cloud synthetic-5 synthetic-10 synthetic-25 synthetic-50 synthetic-100 synthetic-200 stream-1 stream-5 examples}"
  local rest_s="${KELT_RESOURCE_S:-300}" load_n="${KELT_LAT_LOAD_ASSETS:-50}" stream_s="${KELT_STREAM_S:-300}"
  # shellcheck source=exposure/load_fixture.sh
  source "$EXP_ROOT/exposure/load_fixture.sh"
  # shellcheck source=exposure/other_org.sh
  source "$EXP_ROOT/exposure/other_org.sh"
  gw="$(gateway_url)"
  curl -s --max-time 10 -o /dev/null -w '%{http_code}' "$gw/health" | grep -q '^200$' || die "gateway $gw/health is not 200"
  # The samplers stop on their own after the longest the campaign can take.
  budget=$((rest_s + 1800))
  for cond in $conds; do
    case "$cond" in
      rest|examples) ;;
      stream-*) load=1; budget=$((budget + ${KELT_LAT_WARMUP_S:-10} + stream_s + 120)) ;;
      *) [ "${cond#synthetic-}" = "$cond" ] || load=1
         read -r rate warm n < <(rt_plan "$cond")
         budget="$(python3 -c "print(int($budget + ($warm + $n) / $rate + 120))")" ;;
    esac
  done
  if [ "$load" = 1 ]; then
    left="$(load_leftovers "$gw")"
    [ -z "$left" ] || die "leftover from an earlier run, remove it first: $(echo $left)"
  fi
  if [[ " $conds " == *" examples "* ]]; then
    left="$(oo_leftover)"
    [ -z "$left" ] || die "leftover from an earlier run, remove it first: $left"
  fi
  run_dir="$(begin_run response-time "$conds")"
  RT_GW="$gw"; trap rt_exit EXIT
  rt_token_start
  fp="rt-$(basename "$run_dir")"
  footprint_start "$fp" "$budget" "$run_dir/raw/footprint/1"
  for cond in $conds; do
    mkdir -p "$run_dir/$cond"
    case "$cond" in
      synthetic-*|stream-*)
        if [ "$RT_LOAD" = 0 ]; then
          log "response-time: adding $load_n synthetic assets"
          RT_LOAD=1; load_setup "$load_n" "$(cat "$RT_TOKEN")" "$gw"
        fi ;;
    esac
    case "$cond" in
      rest)
        log "response-time rest: nothing sent for $rest_s s"
        hoplog_start "$(basename "$run_dir")-rest" "$((rest_s + 300))"
        wifi_seen >"$run_dir/rest/wifi-devices-start.json"
        window open "$run_dir/rest" rest; sleep "$rest_s"; window close "$run_dir/rest" rest
        wifi_seen >"$run_dir/rest/wifi-devices-end.json"
        # The adapter's hop lines: one per scan ingest (northbound 0.19.4).
        hoplog_stop "$(basename "$run_dir")-rest" "$run_dir/rest" rest
        # A WiFi scanner's own log over the window (KELT_SCANNER_SSH, read only,
        # messages only): the check compares its scans with the adapter's lines.
        if [ -n "${KELT_SCANNER_SSH:-}" ]; then
          read -r _ _ s e < <(window_args "$run_dir/rest" rest 0)
          ssh -o BatchMode=yes -o ConnectTimeout=10 "$KELT_SCANNER_SSH" \
            "journalctl -u ${KELT_SCANNER_UNIT:-scanner} --since @${s%.*} --until @${e%.*} -o cat --no-pager" \
            >"$run_dir/rest/scanner-journal.txt" 2>/dev/null || log "WARNING: the scanner's journal could not be read"
        fi
        RT_FPWIN+=($(window_args "$run_dir/rest" rest 0))
        ;;
      wittra-*)
        asset="$(assets "$(cat "$RT_TOKEN")" "$gw" | awk -v s=wittra '$2==s && $1 !~ /^'"$LOAD_PREFIX"'/{print $1; exit}')"
        [ -n "$asset" ] || die "no asset with source wittra for condition $cond"
        echo "{\"device\":{\"assetId\":\"$asset\"}}" >"$run_dir/$cond/bodies.jsonl"
        read -r status _ < <(retrieve_probe "$(cat "$RT_TOKEN")" "$gw" "$asset")
        [ "$status" = 200 ] || die "$cond: $asset answers $status, not 200"
        # The probe itself fills the gateway's 5 s cache: let it expire before a
        # condition whose first request must not be served from it.
        [ "$cond" != wittra-cloud ] || sleep 6
        rt_send "$run_dir" "$cond" "$run_dir/$cond/bodies.jsonl"
        ;;
      synthetic-*)
        python3 "$EXP_ROOT/exposure/load_assets.py" bodies "$LOAD_PREFIX" "$load_n" >"$run_dir/$cond/bodies.jsonl"
        rt_send "$run_dir" "$cond" "$run_dir/$cond/bodies.jsonl"
        ;;
      stream-*)
        n="${cond#stream-}"
        log "response-time $cond: $n client(s) on the position stream, ${KELT_LAT_WARMUP_S:-10} s warm-up + $stream_s s"
        python3 "$EXP_ROOT/exposure/stream_client.py" --gateway "$gw" --token-file "$RT_TOKEN" --clients "$n" \
          --warmup "${KELT_LAT_WARMUP_S:-10}" --duration "$stream_s" --run-dir "$run_dir/$cond" --condition "$cond"
        interval="$(kubectl get cm -n "$LOAD_POS_NS" "${KELT_ENGINE_CONFIG:-positioning-engine-config}" \
          -o jsonpath='{.data.WEBSOCKET_INTERVAL_MS}' | tr -d '\r')"
        python3 - "$run_dir/$cond/stream.json" "$interval" <<'PY'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
d["engine_interval_ms"] = int(sys.argv[2]) if sys.argv[2].isdigit() else None
json.dump(d, open(p, "w"), indent=2)
PY
        RT_FPWIN+=($(window_args "$run_dir/$cond" "$cond" 0))
        ;;
      examples)
        asset="$(assets "$(cat "$RT_TOKEN")" "$gw" | awk -v s=wittra '$2==s && $1 !~ /^'"$LOAD_PREFIX"'/{print $1; exit}')"
        [ -n "$asset" ] || die "no asset with source wittra for the examples"
        log "response-time examples: a temporary client of organisation $OO_ORG, then the exchanges"
        RT_OO=1; oo_setup "$RT_TOKEN.other"
        python3 "$EXP_ROOT/exposure/examples.py" --gateway "$gw" --token-file "$RT_TOKEN" \
          --other-token-file "$RT_TOKEN.other" --asset "$asset" --out "$run_dir/examples" \
          || log "WARNING: the examples were not all recorded"
        rm -f "$RT_TOKEN.other"; oo_undo; RT_OO=0
        ;;
      *) die "unknown condition $cond (rest | wittra-cached | wittra-cloud | synthetic-<rate> | stream-<clients> | examples)" ;;
    esac
  done
  rt_undo
  footprint_stop "$fp" "$run_dir/raw/footprint/1"
  footprint_summarise "$run_dir" "${RT_FPWIN[@]}"
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
