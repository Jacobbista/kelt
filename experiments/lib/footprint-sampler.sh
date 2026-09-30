#!/usr/bin/env bash
# Samples CPU and memory once a second, for the footprint of a campaign.
#
#   footprint-sampler.sh <out-file> <seconds>
#
# Runs on each VM (every pod's cgroup and the VM itself) and on the host (the
# machine only, FOOTPRINT_NO_PODS=1). Lines, appended to <out-file>:
#
#   N <epoch> <cpus> <cpu jiffies total> <cpu jiffies idle+iowait> <mem total kB> <mem available kB>
#   P <epoch> <pod uid> <cpu usage usec> <memory working set bytes>
#   V <epoch> <vm name> <cpu ticks, utime + stime> <clock ticks per s> <rss kB>
#
# V lines (FOOTPRINT_VBOX=1, on the host): each VirtualBox VM process
# (VBoxHeadless --comment <name>), what the testbed takes from the host apart
# from everything else running on it.
#
# Working set = memory.current - inactive_file, as the kubelet and cAdvisor
# count a pod's memory. Rates are computed afterwards (resource-use/footprint.py).
# FOOTPRINT_ROOT prefixes /proc and /sys (tests). Owner: experiments/README.md.
set -u
out="$1" secs="$2"
root="${FOOTPRINT_ROOT:-}"
cpus="${FOOTPRINT_NPROC:-$(nproc)}"
kube="$root/sys/fs/cgroup/kubepods.slice"
clk="${FOOTPRINT_CLK_TCK:-$(getconf CLK_TCK)}"

# The VM processes, found once (they live as long as the VMs).
vm_pids=() vm_names=()
if [ -n "${FOOTPRINT_VBOX:-}" ]; then
  for d in "$root"/proc/[0-9]*; do
    args=()
    mapfile -d '' -t args < "$d/cmdline" 2>/dev/null || continue
    [ "${#args[@]}" -gt 0 ] && [ "${args[0]##*/}" = VBoxHeadless ] || continue
    for i in "${!args[@]}"; do
      [ "${args[$i]}" = --comment ] && { vm_pids+=("${d##*/}"); vm_names+=("${args[$((i + 1))]}"); }
    done
  done
fi

# value <var> <file> <key>: sets <var> to the value after <key> in a "key
# value" file. Builtins only, no subshell: this runs every second for every
# pod, and a process per read would add the load it measures.
value() {
  local k v
  printf -v "$1" ''
  while read -r k v _; do
    [ "$k" = "$3" ] && { printf -v "$1" '%s' "$v"; return; }
  done < "$2"
}

sample() {
  local now="$EPOCHREALTIME" label rest total idle mt ma slice uid usage cur inact ws
  read -r label rest < "$root/proc/stat"
  set -- $rest
  total=$(( $1 + $2 + $3 + $4 + $5 + $6 + $7 + $8 )); idle=$(( $4 + $5 ))
  value mt "$root/proc/meminfo" MemTotal:; value ma "$root/proc/meminfo" MemAvailable:
  echo "N $now $cpus $total $idle $mt $ma"
  local i st rss
  for i in "${!vm_pids[@]}"; do
    read -r st < "$root/proc/${vm_pids[$i]}/stat" 2>/dev/null || continue
    set -- ${st##*) }
    value rss "$root/proc/${vm_pids[$i]}/status" VmRSS:
    echo "V $now ${vm_names[$i]} $(( ${12} + ${13} )) $clk $rss"
  done
  [ -n "${FOOTPRINT_NO_PODS:-}" ] && return
  for slice in "$kube"/*-pod*.slice "$kube"/*/*-pod*.slice; do
    [ -d "$slice" ] || continue
    uid="${slice##*-pod}"; uid="${uid%.slice}"; uid="${uid//_/-}"
    value usage "$slice/cpu.stat" usage_usec
    read -r cur < "$slice/memory.current"
    value inact "$slice/memory.stat" inactive_file
    ws=$(( cur - ${inact:-0} )); [ "$ws" -lt 0 ] && ws=0
    echo "P $now $uid $usage $ws"
  done
}

end=$(( SECONDS + secs ))
while [ "$SECONDS" -lt "$end" ]; do
  sample >> "$out"
  sleep 1
done
