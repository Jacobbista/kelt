#!/usr/bin/env bash
# The measurement job that runs on the UE (the Pi), started detached by the
# campaign runner (network/campaign.sh) so no SSH crosses the modem while it
# measures. It reads job.env and schedule.txt from its own directory, runs
# every line, and leaves in the same directory:
#
#   status               state=running run=<i>/<n> | state=done | state=aborted reason=<r> run=<i>
#   runs/<i>-<name>.*    .json (iperf3 -J), .ping (ping -D), .load.json, .meta
#   cpu.csv              epoch,busy_permille every second
#   ue.txt               what the UE is: model, link speed, TCP congestion control, route
#   MANIFEST.sha256      checksums of everything above, checked after the fetch
#
# schedule.txt, one run per line:
#   <i> <name> iperf3 <iperf3 args...>
#   <i> <name> ping <count> <interval>
#   <i> <name> ping+load <count> <interval> <iperf3 args...>   (iperf3 starts first)
#
# It changes nothing on the UE's networking. The units in KELT_PAUSE_UNITS
# (an application on the UE that sends over the link) are stopped for the job
# and started again at the end, on a signal, or, if this script dies outright,
# by a systemd timer set before they are stopped. Owner: experiments/README.md.
set -u
cd "$(dirname "$0")" || exit 1
# shellcheck source=/dev/null
source ./job.env
: "${KELT_TARGET:?}" "${KELT_IF:?}" "${KELT_JOB_ID:?}" "${KELT_DEADLINE_S:?}"
PAUSE_UNITS="${KELT_PAUSE_UNITS:-}"
STATS="${KELT_STATS_DIR:-/sys/class/net}/$KELT_IF/statistics"
PROC_STAT="${KELT_PROC_STAT:-/proc/stat}"
GRACE="${KELT_TOOL_GRACE_S:-20}"
mkdir -p runs

N="$(grep -c . schedule.txt)"
CURRENT=0
PAUSED=()
SAMPLER=""
TOOL_PIDS=()
PING=(ping)

status() { printf '%s\n' "$*" > status.tmp && mv -f status.tmp status; }
now() { date +%s.%N; }
counters() { echo "$(cat "$STATS/rx_bytes"),$(cat "$STATS/tx_bytes"),$(cat "$STATS/rx_packets"),$(cat "$STATS/tx_packets")"; }

# A tool in the background, waited for: a signal then reaches the trap at once
# instead of after the tool ends.
run_bg() { "$@" & TOOL_PIDS+=("$!"); }
wait_last() { local rc=0; wait "${TOOL_PIDS[-1]}" || rc=$?; unset 'TOOL_PIDS[-1]'; return "$rc"; }

pause_units() {
  local u i=0
  for u in $PAUSE_UNITS; do
    systemctl is-active --quiet "$u" || continue
    i=$((i + 1))
    sudo -n systemd-run --quiet --on-active="$((KELT_DEADLINE_S + 60))s" \
      --unit="kelt-restore-$KELT_JOB_ID-$i" systemctl start "$u" || continue
    sudo -n systemctl stop "$u" && PAUSED+=("$u")
  done
}

restore_units() {
  local u i=0
  for u in "${PAUSED[@]}"; do
    i=$((i + 1))
    sudo -n systemctl start "$u"
    sudo -n systemctl stop "kelt-restore-$KELT_JOB_ID-$i.timer" 2>/dev/null
  done
  PAUSED=()
}

cpu_sampler() {
  local a b idle_a idle_b tot_a tot_b
  read -r -a a < <(head -1 "$PROC_STAT")
  while sleep "${KELT_CPU_EVERY_S:-1}"; do
    read -r -a b < <(head -1 "$PROC_STAT")
    idle_a=$((a[4] + a[5])); idle_b=$((b[4] + b[5]))
    tot_a=0; tot_b=0
    for v in "${a[@]:1}"; do tot_a=$((tot_a + v)); done
    for v in "${b[@]:1}"; do tot_b=$((tot_b + v)); done
    if [ $((tot_b - tot_a)) -gt 0 ]; then
      echo "$(now),$(( 1000 * ((tot_b - tot_a) - (idle_b - idle_a)) / (tot_b - tot_a) ))"
    fi
    a=("${b[@]}")
  done >> cpu.csv
}

describe_ue() {
  local route
  route="$(ip -4 route show default 2>/dev/null | head -1)"
  {
    echo "model=$({ tr -d '\0' < /proc/device-tree/model; } 2>/dev/null || echo unknown)"
    echo "link=$KELT_IF"
    echo "link_speed_mbit=$(cat "${KELT_STATS_DIR:-/sys/class/net}/$KELT_IF/speed" 2>/dev/null || echo unknown)"
    echo "tcp_cc=$(cat /proc/sys/net/ipv4/tcp_congestion_control 2>/dev/null || echo unknown)"
    echo "default_route=$route"
    echo "address=$(ip -4 -o addr show "$KELT_IF" 2>/dev/null | awk '{print $4}' | head -1)"
    echo "iperf3=$(iperf3 --version 2>/dev/null | head -1)"
  } > ue.txt
}

manifest() {
  find . -type f ! -name MANIFEST.sha256 ! -name status.tmp ! -name job.env ! -name ue-run.sh ! -name run.log \
    -printf '%P\n' | sort | xargs -r sha256sum > MANIFEST.sha256
}

finish() {
  local state="$1" reason="${2:-}"
  # Ignore further signals: `pkill -f ue-run.sh` hits both timeout (which
  # forwards its TERM) and this script, and the restore must not be cut short.
  trap '' TERM INT
  trap - EXIT
  [ ${#TOOL_PIDS[@]} -gt 0 ] && kill -TERM "${TOOL_PIDS[@]}" 2>/dev/null
  pkill -TERM -P $$ 2>/dev/null
  [ -n "$SAMPLER" ] && kill "$SAMPLER" 2>/dev/null
  restore_units
  if [ "$state" = done ]; then status "state=done"
  else status "state=aborted reason=$reason run=$CURRENT"; fi
  manifest
  exit 0
}
trap 'finish aborted signal' TERM INT
trap 'finish aborted exit' EXIT

# ping at 0.1 s needs root on older iputils: try as the user, else through sudo.
choose_ping() {
  if ! ping -c 1 -i 0.1 -W 2 "$KELT_TARGET" >/dev/null 2>ping-check.err; then
    grep -qi 'interval\|flood' ping-check.err && PING=(sudo -n ping)
  fi
  rm -f ping-check.err
}

# `-t N` of an iperf3 argument list, for its timeout.
iperf_duration() { local p=""; for a in "$@"; do [ "$p" = -t ] && { echo "$a"; return; }; p="$a"; done; echo 10; }

run_line() {
  local idx="$1" name="$2" kind="$3"; shift 3
  local base="runs/$idx-$name" rc=0 before after start end count interval dur
  before="$(counters)"; start="$(now)"
  case "$kind" in
    iperf3)
      dur="$(iperf_duration "$@")"
      run_bg timeout "$((dur + GRACE))" iperf3 -c "$KELT_TARGET" "$@" > "$base.json"
      wait_last || rc=$? ;;
    ping)
      count="$1"; interval="$2"
      run_bg timeout "$(awk -v c="$count" -v i="$interval" -v g="$GRACE" 'BEGIN{printf "%d", c*i+g+5}')" \
        "${PING[@]}" -D -i "$interval" -c "$count" "$KELT_TARGET" > "$base.ping" 2>&1
      wait_last || rc=$? ;;
    ping+load)
      count="$1"; interval="$2"; shift 2
      dur="$(iperf_duration "$@")"
      run_bg timeout "$((dur + GRACE))" iperf3 -c "$KELT_TARGET" "$@" -J > "$base.load.json"
      sleep "${KELT_LOAD_LEAD_S:-10}"
      run_bg timeout "$(awk -v c="$count" -v i="$interval" -v g="$GRACE" 'BEGIN{printf "%d", c*i+g+5}')" \
        "${PING[@]}" -D -i "$interval" -c "$count" "$KELT_TARGET" > "$base.ping" 2>&1
      wait_last || rc=$?
      local lrc=0; wait_last || lrc=$?
      [ "$rc" = 0 ] && rc="$lrc" ;;
    *) rc=64 ;;
  esac
  end="$(now)"; after="$(counters)"
  echo "start=$start end=$end rc=$rc if_before=$before if_after=$after" > "$base.meta"
}

status "state=starting"
describe_ue
sleep "${KELT_START_DELAY_S:-10}"
pause_units
cpu_sampler & SAMPLER=$!
choose_ping
while read -r idx name kind args; do
  [ -n "${idx:-}" ] || continue
  [ "$CURRENT" -gt 0 ] && sleep "${KELT_PAUSE_S:-10}"
  CURRENT="$idx"
  status "state=running run=$idx/$N"
  # shellcheck disable=SC2086
  run_line "$idx" "$name" "$kind" $args
done < schedule.txt
finish done
