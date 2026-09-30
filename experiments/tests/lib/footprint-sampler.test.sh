#!/usr/bin/env bash
# Tests for lib/footprint-sampler.sh against a fake /proc and /sys.
# Run: bash experiments/tests/lib/footprint-sampler.test.sh
set -o pipefail
SAMPLER="$(cd "$(dirname "$0")/../../lib" && pwd)/footprint-sampler.sh"
fails=0
ok() { echo "ok   $1"; }
bad() { echo "FAIL $1"; fails=$((fails + 1)); }
expect() { if eval "$2"; then ok "$1"; else bad "$1: $3"; fi; }

R="$(mktemp -d)"
mkdir -p "$R/proc" "$R/sys/fs/cgroup/kubepods.slice/kubepods-burstable.slice/kubepods-burstable-pod1a2b_3c.slice" \
  "$R/sys/fs/cgroup/kubepods.slice/kubepods-podff_ee.slice"
echo "cpu  100 5 50 800 40 0 5 0 0 0" > "$R/proc/stat"
echo "cpu0 50 2 25 400 20 0 3 0 0 0" >> "$R/proc/stat"
printf 'MemTotal:       2048000 kB\nMemFree:  1 kB\nMemAvailable:   1024000 kB\n' > "$R/proc/meminfo"
B="$R/sys/fs/cgroup/kubepods.slice/kubepods-burstable.slice/kubepods-burstable-pod1a2b_3c.slice"
printf 'usage_usec 5000000\nuser_usec 1\n' > "$B/cpu.stat"; echo 300 > "$B/memory.current"; printf 'anon 1\ninactive_file 100\n' > "$B/memory.stat"
G="$R/sys/fs/cgroup/kubepods.slice/kubepods-podff_ee.slice"
printf 'usage_usec 7\n' > "$G/cpu.stat"; echo 50 > "$G/memory.current"; printf 'inactive_file 0\n' > "$G/memory.stat"

out="$(FOOTPRINT_ROOT="$R" FOOTPRINT_NPROC=4 bash "$SAMPLER" /dev/stdout 1 2>&1)"
node="$(grep '^N ' <<< "$out" | head -1)"
expect "node line: cpus, total and idle+iowait jiffies, memory total and available (kB)" \
  "[ \"\$(cut -d' ' -f3- <<< \"\$node\")\" = '4 1000 840 2048000 1024000' ]" "$node"
expect "pod line per pod slice, uid with dashes, usage usec and working set" \
  "grep -qE '^P [0-9.]+ 1a2b-3c 5000000 200$' <<< \"\$out\"" "$out"
expect "a guaranteed pod (directly under kubepods.slice) too" "grep -qE '^P [0-9.]+ ff-ee 7 50$' <<< \"\$out\"" ""
out2="$(FOOTPRINT_ROOT="$R" FOOTPRINT_NPROC=4 FOOTPRINT_NO_PODS=1 bash "$SAMPLER" /dev/stdout 1 2>&1)"
expect "the host (no pods) writes node lines only" "! grep -q '^P ' <<< \"\$out2\" && grep -q '^N ' <<< \"\$out2\"" "$out2"

# the host: each VirtualBox VM process (VBoxHeadless --comment <name>), its CPU
# time in clock ticks (utime + stime) and resident memory
mkdir -p "$R/proc/4242" "$R/proc/77"
printf '/usr/lib/virtualbox/VBoxHeadless\0--comment\0worker-5g-k8s-testbed\0--startvm\0x\0' > "$R/proc/4242/cmdline"
echo "4242 (VBoxHeadless) S 1 2 3 4 5 6 7 8 9 10 700 300 0 0 20 0 30 0 99 1000 2000" > "$R/proc/4242/stat"
printf 'Name:\tVBoxHeadless\nVmRSS:\t  9520420 kB\n' > "$R/proc/4242/status"
printf 'bash\0' > "$R/proc/77/cmdline"
mkdir -p "$R/proc/2"; : > "$R/proc/2/cmdline"      # a kernel thread: empty cmdline
out3="$(FOOTPRINT_ROOT="$R" FOOTPRINT_NPROC=4 FOOTPRINT_NO_PODS=1 FOOTPRINT_VBOX=1 FOOTPRINT_CLK_TCK=100 bash "$SAMPLER" /dev/stdout 1 2>&1)"
expect "a VM process: name, CPU ticks (utime + stime), clock ticks per second, RSS kB" \
  "grep -qE '^V [0-9.]+ worker-5g-k8s-testbed 1000 100 9520420$' <<< \"\$out3\"" "$out3"
expect "other processes are not VMs" "[ \"\$(grep -c '^V ' <<< \"\$out3\")\" = 1 ]" ""
rm -rf "$R"
exit $fails
