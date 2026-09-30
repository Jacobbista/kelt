#!/usr/bin/env bash
# Tests for network/ue-run.sh, the measurement job that runs on the UE. The
# tools and systemd are PATH shims; the link counters and /proc/stat are fake
# files. Run: bash experiments/tests/network/ue-run.test.sh
set -o pipefail

JOB_SRC="$(cd "$(dirname "$0")/../../network" && pwd)/ue-run.sh"
fails=0
ok() { echo "ok   $1"; }
bad() { echo "FAIL $1"; fails=$((fails + 1)); }
expect() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }

# A fresh job dir with shims. $1 = schedule text; the rest are job.env lines.
setup() {
  T="$(mktemp -d)"; J="$T/job"; B="$T/bin"; mkdir -p "$J" "$B" "$T/net/eth0/statistics"
  for c in rx_bytes tx_bytes rx_packets tx_packets; do echo 1000 > "$T/net/eth0/statistics/$c"; done
  printf 'cpu  100 0 100 800 0 0 0 0 0 0\n' > "$T/stat"
  cp "$JOB_SRC" "$J/ue-run.sh"
  printf '%s\n' "$1" > "$J/schedule.txt"; shift
  {
    echo "KELT_TARGET=10.45.0.1"; echo "KELT_IF=eth0"; echo "KELT_JOB_ID=t1"
    echo "KELT_START_DELAY_S=0"; echo "KELT_PAUSE_S=0"; echo "KELT_DEADLINE_S=600"
    echo "KELT_STATS_DIR=$T/net"; echo "KELT_PROC_STAT=$T/stat"; echo "KELT_LOAD_LEAD_S=0"
    echo "KELT_PAUSE_UNITS=example-streamer.service"
    printf '%s\n' "$@"
  } > "$J/job.env"
  export SHIM_LOG="$T/shim.log"; : > "$SHIM_LOG"
  cat > "$B/iperf3" <<'EOF'
#!/usr/bin/env bash
echo "iperf3 $*" >> "$SHIM_LOG"
[ "$1" = -c ] && [ -n "${SHIM_IPERF_SLEEP:-}" ] && exec sleep "$SHIM_IPERF_SLEEP"   # one process, like iperf3
echo '{"start":{},"intervals":[],"end":{}}'
EOF
  cat > "$B/ping" <<'EOF'
#!/usr/bin/env bash
echo "ping $* sudo=${SUDO_SHIM:-0}" >> "$SHIM_LOG"
if [ "${SHIM_PING_REFUSE:-}" = 1 ] && [ "${SUDO_SHIM:-0}" != 1 ]; then
  echo "ping: cannot flood; minimal interval allowed for user is 200ms" >&2; exit 2
fi
echo "[1.0] 64 bytes from 10.45.0.1: icmp_seq=1 ttl=64 time=12.0 ms"
echo "1 packets transmitted, 1 received, 0% packet loss, time 0ms"
EOF
  cat > "$B/systemctl" <<'EOF'
#!/usr/bin/env bash
echo "systemctl $*" >> "$SHIM_LOG"
if [ "$1" = is-active ]; then [ "${SHIM_ACTIVE:-1}" = 1 ]; exit; fi
exit 0
EOF
  cat > "$B/systemd-run" <<'EOF'
#!/usr/bin/env bash
echo "systemd-run $*" >> "$SHIM_LOG"
EOF
  cat > "$B/sudo" <<'EOF'
#!/usr/bin/env bash
[ "$1" = -n ] && shift
SUDO_SHIM=1 exec "$@"
EOF
  chmod +x "$B"/* "$J/ue-run.sh"
  export PATH="$B:$PATH"
}
status_is() { grep -q "$1" "$J/status"; }

# ── a full schedule ───────────────────────────────────────────────────────────
setup "1 dl1 iperf3 -t 1 -i 0.1 -J -R
2 idle ping 10 0.1
3 load ping+load 10 0.1 -t 1 -P 4"
( cd "$J" && ./ue-run.sh ) > "$T/out" 2>&1
expect "full: state done" "status_is '^state=done'"
expect "full: one .meta per line" "[ \$(ls $J/runs/*.meta | wc -l) = 3 ]"
expect "full: iperf3 result kept" "[ -s $J/runs/1-dl1.json ]"
expect "full: ping output kept" "[ -s $J/runs/2-idle.ping ]"
expect "full: load result kept" "[ -s $J/runs/3-load.ping ] && [ -s $J/runs/3-load.load.json ]"
expect "full: meta has times, rc and counters" "grep -q '^start=[0-9.]* end=[0-9.]* rc=0 if_before=1000,1000,1000,1000 if_after=1000,1000,1000,1000' $J/runs/1-dl1.meta"
expect "full: iperf3 aimed at the target" "grep -q 'iperf3 -c 10.45.0.1 -t 1 -i 0.1 -J -R' $SHIM_LOG"
expect "full: manifest verifies" "(cd $J && sha256sum -c --quiet MANIFEST.sha256)"
expect "full: ue.txt written" "grep -q '^tcp_cc=' $J/ue.txt"
expect "full: unit paused with a restore timer first" "grep -n 'systemd-run\|systemctl stop example-streamer' $SHIM_LOG | head -1 | grep -q systemd-run"
expect "full: unit started again" "grep -q 'systemctl start example-streamer.service' $SHIM_LOG"
expect "full: restore timer stopped" "grep -q 'systemctl stop kelt-restore-t1-1.timer' $SHIM_LOG"
rm -rf "$T"

# ── a hanging tool is cut by its own timeout, the job goes on ─────────────────
setup "1 dl1 iperf3 -t 1 -J
2 ul1 iperf3 -t 1 -J" "KELT_TOOL_GRACE_S=1"
SHIM_IPERF_SLEEP=30 bash -c "cd $J && ./ue-run.sh" > "$T/out" 2>&1
expect "hang: run 1 rc=124" "grep -q 'rc=124' $J/runs/1-dl1.meta"
expect "hang: run 2 executed" "[ -f $J/runs/2-ul1.meta ]"
expect "hang: state done" "status_is '^state=done'"
rm -rf "$T"

# ── SIGTERM mid-run: aborted, the paused unit back ──────────────────────────────
setup "1 dl1 iperf3 -t 60 -J
2 ul1 iperf3 -t 60 -J"
( cd "$J" && SHIM_IPERF_SLEEP=60 exec ./ue-run.sh ) > "$T/out" 2>&1 &
pid=$!
for _ in $(seq 50); do grep -q 'run=1/' "$J/status" 2>/dev/null && break; sleep 0.1; done
sleep 0.3; kill -TERM "$pid"; wait "$pid" 2>/dev/null
expect "term: state aborted at run 1" "status_is '^state=aborted .*run=1'"
expect "term: unit started again" "grep -q 'systemctl start example-streamer.service' $SHIM_LOG"
expect "term: restore timer stopped" "grep -q 'systemctl stop kelt-restore-t1-1.timer' $SHIM_LOG"
expect "term: no iperf3 left behind" "! pgrep -f 'sleep 60' >/dev/null"
expect "term: run 2 never started" "[ ! -f $J/runs/2-ul1.meta ]"
expect "term: manifest written" "[ -s $J/MANIFEST.sha256 ]"
rm -rf "$T"

# ── two TERMs in a row (pkill hits timeout and the job): the restore still runs
setup "1 dl1 iperf3 -t 60 -J"
( cd "$J" && SHIM_IPERF_SLEEP=60 exec ./ue-run.sh ) > "$T/out" 2>&1 &
pid=$!
for _ in $(seq 50); do grep -q 'run=1/' "$J/status" 2>/dev/null && break; sleep 0.1; done
sleep 0.3; kill -TERM "$pid"; kill -TERM "$pid"; wait "$pid" 2>/dev/null
expect "term twice: unit started again" "grep -q 'systemctl start example-streamer.service' $SHIM_LOG"
expect "term twice: state aborted" "status_is '^state=aborted'"
rm -rf "$T"

# ── run.log is not in the manifest (it may still grow after it)
setup "1 idle ping 5 0.1"
( cd "$J" && ./ue-run.sh > run.log 2>&1 )
expect "manifest: run.log left out" "! grep -q ' run.log' $J/MANIFEST.sha256"
rm -rf "$T"

# ── a unit that is not running is left alone ──────────────────────────────────
setup "1 idle ping 5 0.1"
SHIM_ACTIVE=0 bash -c "cd $J && ./ue-run.sh" > "$T/out" 2>&1
expect "inactive: not stopped" "! grep -q 'systemctl stop example-streamer-stream' $SHIM_LOG"
expect "inactive: not started" "! grep -q 'systemctl start example-streamer-stream' $SHIM_LOG"
expect "inactive: no restore timer" "! grep -q systemd-run $SHIM_LOG"
rm -rf "$T"

# ── no unit named: nothing paused ────────────────────────────────────────────
setup "1 idle ping 5 0.1" "KELT_PAUSE_UNITS="
bash -c "cd $J && ./ue-run.sh" > "$T/out" 2>&1
expect "no units: nothing stopped" "! grep -q 'systemctl stop' $SHIM_LOG"
expect "no units: no restore timer" "! grep -q systemd-run $SHIM_LOG"
rm -rf "$T"

# ── ping that refuses 0.1 s for a user goes through sudo ─────────────────────
setup "1 idle ping 5 0.1"
SHIM_PING_REFUSE=1 bash -c "cd $J && ./ue-run.sh" > "$T/out" 2>&1
expect "ping: measured through sudo" "grep -q 'ping -D .* sudo=1' $SHIM_LOG"
expect "ping: rc 0" "grep -q 'rc=0' $J/runs/1-idle.meta"
rm -rf "$T"

exit $fails
