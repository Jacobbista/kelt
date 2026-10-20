#!/usr/bin/env bash
# The host power test: synthetic-5 under four host settings, one after the
# other, to locate the two values of the end-to-end time at low rates.
#
#   sudo experiments/exposure/host_power.sh
#
#   idle-base    the host as deployed
#   idle-off     deep idle states blocked: a root process holds
#                /dev/cpu_dma_latency at 0 (Linux PM QoS), as in the test of
#                2026-10-06; only POLL is left, so C1 is blocked too
#   freq-max     scaling_min_freq set to KELT_FREQ_MAX_KHZ (default
#                cpuinfo_max_freq) on every cpufreq policy; driver, governor and
#                idle states unchanged
#   idle-base-2  the host as deployed, after the restores
#
# Each run is one `run.sh response-time` with the condition synthetic-5, started
# as the user who called sudo. Around it the script records, into
# <run dir>/host/: turbostat once a second for the whole run, and the
# settings and idle-state counters read back before the setting, once applied,
# in the middle of the run and after the restore. Every setting is restored
# after its run, and by an exit trap on failure or Ctrl-C. KELT_POWER_RUNS
# selects and orders the runs. Owner: experiments/exposure/README.md.
set -uo pipefail

[ "$(id -u)" = 0 ] && [ -n "${SUDO_USER:-}" ] || { echo "run with sudo, as the user who runs the campaigns" >&2; exit 2; }
EXP="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNS="${KELT_POWER_RUNS:-idle-base idle-off freq-max idle-base-2}"
CPUFREQ=/sys/devices/system/cpu/cpufreq
MAX_KHZ="${KELT_FREQ_MAX_KHZ:-$(cat "$CPUFREQ/policy0/cpuinfo_max_freq")}"
TMP="$(mktemp -d)"
QOS_PID=""; TS_PID=""
declare -A SAVED_MIN

log() { printf '[%s] %s\n' "$(date -u +%H:%M:%SZ)" "$*" >&2; }

for p in "$CPUFREQ"/policy*; do SAVED_MIN[$p]="$(cat "$p/scaling_min_freq")"; done

# Every setting this script touches, and the idle-state counters, as JSON.
readback() {
  python3 - <<'PY'
import glob, json, os, struct
pol = {}
for p in sorted(glob.glob("/sys/devices/system/cpu/cpufreq/policy*")):
    r = lambda f: open(os.path.join(p, f)).read().strip()
    pol[os.path.basename(p)] = {"governor": r("scaling_governor"), "min_khz": int(r("scaling_min_freq")),
                                "max_khz": int(r("scaling_max_freq")), "cur_khz": int(r("scaling_cur_freq")),
                                "epp": r("energy_performance_preference")}
with open("/dev/cpu_dma_latency", "rb") as fh:
    qos = struct.unpack("i", fh.read(4))[0]
idle = {}
for c in sorted(glob.glob("/sys/devices/system/cpu/cpu[0-9]*"), key=lambda x: int(x.rsplit("cpu", 1)[1])):
    idle[os.path.basename(c)] = {open(s + "/name").read().strip(): {"usage": int(open(s + "/usage").read()),
                                                                    "time_us": int(open(s + "/time").read()),
                                                                    "disable": int(open(s + "/disable").read())}
                                 for s in sorted(glob.glob(c + "/cpuidle/state*"))}
import datetime
print(json.dumps({"utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                  "cpufreq_driver": open("/sys/devices/system/cpu/cpufreq/policy0/scaling_driver").read().strip(),
                  "idle_driver": open("/sys/devices/system/cpu/cpuidle/current_driver").read().strip(),
                  "pm_qos_cpu_dma_latency_us": qos, "policies": pol, "idle_states": idle}, indent=1))
PY
}

apply() {
  case "$1" in
    idle-off)
      python3 -c 'import os, signal, struct
fd = os.open("/dev/cpu_dma_latency", os.O_WRONLY); os.write(fd, struct.pack("i", 0)); signal.pause()' &
      QOS_PID=$!; sleep 1 ;;
    freq-max)
      for p in "$CPUFREQ"/policy*; do echo "$MAX_KHZ" >"$p/scaling_min_freq"; done ;;
  esac
}

restore() {
  [ -z "$QOS_PID" ] || { kill "$QOS_PID" 2>/dev/null; wait "$QOS_PID" 2>/dev/null; QOS_PID=""; }
  for p in "${!SAVED_MIN[@]}"; do echo "${SAVED_MIN[$p]}" >"$p/scaling_min_freq"; done
}

# check <run> <json> applied|restored: the setting read back is the one intended.
check() {
  python3 - "$1" "$2" "$3" "$MAX_KHZ" "${SAVED_MIN[$CPUFREQ/policy0]}" <<'PY'
import json, sys
run, path, phase, mx, mn = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5])
d = json.load(open(path))
mins = {p["min_khz"] for p in d["policies"].values()}
qos = d["pm_qos_cpu_dma_latency_us"]
want_min = mx if (run == "freq-max" and phase == "applied") else mn
want_qos0 = run == "idle-off" and phase == "applied"
ok = mins == {want_min} and ((qos == 0) == want_qos0)
print(f"{run} {phase}: min {sorted(mins)} kHz, PM QoS {qos} us -> {'ok' if ok else 'NOT as intended'}")
sys.exit(0 if ok else 1)
PY
}

cleanup() {
  [ -z "$TS_PID" ] || kill "$TS_PID" 2>/dev/null
  restore
  rm -rf "$TMP"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

command -v turbostat >/dev/null || { log "turbostat is missing"; exit 2; }
for run in $RUNS; do
  case "$run" in idle-base|idle-base-2|idle-off|freq-max) ;; *) log "unknown run $run"; exit 2 ;; esac
  log "$run: start"
  readback >"$TMP/before.json"
  apply "$run"
  readback >"$TMP/applied.json"
  check "$run" "$TMP/applied.json" applied >&2 || { log "$run: the setting did not take; stopping"; exit 1; }
  turbostat --quiet --interval 1 --out "$TMP/turbostat.txt" >/dev/null 2>&1 &
  TS_PID=$!
  ( sleep 120; readback >"$TMP/middle.json" ) &
  mid=$!
  sudo -u "$SUDO_USER" -H env KELT_LAT_CONDITIONS=synthetic-5 "$EXP/run.sh" response-time >"$TMP/campaign.log" 2>&1
  status=$?
  wait "$mid" 2>/dev/null
  kill "$TS_PID" 2>/dev/null; wait "$TS_PID" 2>/dev/null; TS_PID=""
  readback >"$TMP/during-end.json"
  restore
  readback >"$TMP/after.json"
  check "$run" "$TMP/after.json" restored >&2 || log "$run: WARNING: not restored as intended"
  dir="$(sed -n 's/.*response-time -> //p' "$TMP/campaign.log" | tail -1)"
  if [ -z "$dir" ] || [ ! -d "$dir" ]; then
    log "$run: the campaign ended without a run dir (exit $status); log kept in $EXP/runs/host-power-$run.log"
    cp "$TMP/campaign.log" "$EXP/runs/host-power-$run.log"; chown "$SUDO_USER": "$EXP/runs/host-power-$run.log"
    exit 1
  fi
  mkdir -p "$dir/host"
  echo "$run" >"$dir/host/setting.txt"
  for f in before applied middle during-end after; do [ -f "$TMP/$f.json" ] && mv "$TMP/$f.json" "$dir/host/settings-$f.json"; done
  mv "$TMP/turbostat.txt" "$TMP/campaign.log" "$dir/host/"
  chown -R "$SUDO_USER": "$dir/host"
  grep -E "^(ok  |FAIL)" "$dir/host/campaign.log" >&2
  log "$run -> $dir"
  [ "$status" = 0 ] || { log "$run: the campaign exited $status; stopping"; exit 1; }
done
log "done: $RUNS"
