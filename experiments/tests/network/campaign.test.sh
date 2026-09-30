#!/usr/bin/env bash
# Tests for the pure parts of network/campaign.sh: the schedules the UE job
# runs and how long they take. Run: bash experiments/tests/network/campaign.test.sh
set -o pipefail

# shellcheck source=/dev/null
source "$(cd "$(dirname "$0")/../../network" && pwd)/campaign.sh"
fails=0
ok() { echo "ok   $1"; }
bad() { echo "FAIL $1"; fails=$((fails + 1)); }
expect() { if eval "$2"; then ok "$1"; else bad "$1: $3"; fi; }

s="$(throughput_schedule 2 60)"
expect "throughput: 4 combinations x 2 repeats" "[ \$(wc -l <<< \"\$s\") = 8 ]" "$(wc -l <<< "$s")"
expect "throughput: round robin, not five in a row" \
  "[ \"\$(awk '{print \$2}' <<< \"\$s\" | paste -sd' ')\" = 'dl1 dl4 ul1 ul4 dl1 dl4 ul1 ul4' ]" "$(awk '{print $2}' <<< "$s" | paste -sd' ')"
expect "throughput: indexes 1..n" "[ \"\$(awk '{print \$1}' <<< \"\$s\" | paste -sd' ')\" = '1 2 3 4 5 6 7 8' ]" ""
expect "throughput: dl1 args" "grep -qx '1 dl1 iperf3 -t 60 -i 0.1 -J -R' <<< \"\$s\"" "$(sed -n 1p <<< "$s")"
expect "throughput: dl4 args" "grep -qx '2 dl4 iperf3 -t 60 -i 0.1 -J -R -P 4' <<< \"\$s\"" "$(sed -n 2p <<< "$s")"
expect "throughput: ul1 args" "grep -qx '3 ul1 iperf3 -t 60 -i 0.1 -J --get-server-output' <<< \"\$s\"" "$(sed -n 3p <<< "$s")"
expect "throughput: ul4 args" "grep -qx '4 ul4 iperf3 -t 60 -i 0.1 -J -P 4 --get-server-output' <<< \"\$s\"" "$(sed -n 4p <<< "$s")"

r="$(rtt_schedule 3 300)"
expect "rtt: 3 idle then 3 load" \
  "[ \"\$(awk '{print \$2}' <<< \"\$r\" | paste -sd' ')\" = 'idle idle idle load load load' ]" "$(awk '{print $2}' <<< "$r" | paste -sd' ')"
expect "rtt: idle is 3000 pings at 0.1 s" "grep -qx '1 idle ping 3000 0.1' <<< \"\$r\"" "$(sed -n 1p <<< "$r")"
expect "rtt: load covers the ping plus 10 s each side" "grep -qx '4 load ping+load 3000 0.1 -t 320 -P 4' <<< \"\$r\"" "$(sed -n 4p <<< "$r")"

expect "seconds: throughput 8x60 + 7 pauses of 10" "[ \"\$(schedule_seconds 10 <<< \"\$s\")\" = 550 ]" "$(schedule_seconds 10 <<< "$s")"
expect "seconds: rtt 3x300 + 3x320 + 5x10" "[ \"\$(schedule_seconds 10 <<< \"\$r\")\" = 1910 ]" "$(schedule_seconds 10 <<< "$r")"
expect "seconds: idle part only" "[ \"\$(grep ' idle ' <<< \"\$r\" | schedule_seconds 10)\" = 920 ]" "$(grep ' idle ' <<< "$r" | schedule_seconds 10)"

expect "mode: address in the UE pool is direct" "[ \"\$(ue_mode 10.45.3.4/32 10.45.0.0/16)\" = direct ]" "$(ue_mode 10.45.3.4/32 10.45.0.0/16)"
expect "mode: a LAN address is nat" "[ \"\$(ue_mode 192.168.100.20/24 10.45.0.0/16)\" = nat ]" "$(ue_mode 192.168.100.20/24 10.45.0.0/16)"

expect "job name carries slug, stamp and attempt" "[ \"\$(job_name throughput 20260928T100000Z 2)\" = kelt-run-throughput-20260928T100000Z-2 ]" "$(job_name throughput 20260928T100000Z 2)"
expect "job name read back to its run" "[ \"\$(job_run_dir /runs kelt-run-rtt-20260928T100000Z-2)\" = /runs/rtt/20260928T100000Z ]" "$(job_run_dir /runs kelt-run-rtt-20260928T100000Z-2)"
expect "job name read back to its attempt" "[ \"\$(job_attempt kelt-run-rtt-20260928T100000Z-2)\" = 2 ]" "$(job_attempt kelt-run-rtt-20260928T100000Z-2)"

# remaining runs: the schedule lines without a .meta in any attempt
R="$(mktemp -d)"
printf '1 dl1 iperf3 -t 60\n2 dl4 iperf3 -t 60 -P 4\n3 ul1 iperf3 -t 60\n4 ul4 iperf3 -t 60 -P 4\n' > "$R/schedule.txt"
mkdir -p "$R/raw/ue/1/runs" "$R/raw/ue/2/runs"
touch "$R/raw/ue/1/runs/1-dl1.meta" "$R/raw/ue/1/runs/2-dl4.json" "$R/raw/ue/2/runs/3-ul1.meta"
expect "remaining: cut and never started, not the finished" \
  "[ \"\$(remaining_schedule $R | awk '{print \$1}' | paste -sd' ')\" = '2 4' ]" "$(remaining_schedule "$R" | awk '{print $1}' | paste -sd' ')"
expect "remaining: lines kept whole" "grep -qx '4 ul4 iperf3 -t 60 -P 4' <<< \"\$(remaining_schedule $R)\"" ""
expect "next attempt after 1 and 2 is 3" "[ \"\$(next_attempt $R)\" = 3 ]" "$(next_attempt "$R")"
expect "first attempt of a new run is 1" "[ \"\$(next_attempt $(mktemp -d))\" = 1 ]" ""
rm -rf "$R"

# waiting for the job: an SSH that hangs or fails is "not reachable yet", never
# the end of the runner. Each case runs in its own bash -e, as run.sh does
# (set -e is ignored inside an if condition, so a subshell here would prove nothing).
CAMPAIGN="$(cd "$(dirname "$0")/../../network" && pwd)/campaign.sh"
runner() { bash -ec "source '$CAMPAIGN'; log() { :; }; sleep() { :; }; NET_START_DELAY_S=0; $1" 2>/dev/null; }
POLLS="$(mktemp)"
expect "status: a hung SSH reads as empty, not a failure" \
  "runner 'ue_ssh() { return 124; }; st=\"\$(ue_status job)\"; [ -z \"\$st\" ]'" ""
expect "wait: unreachable past the deadline, then done, returns 0" \
  "runner 'ue_status() { echo x >> $POLLS; [ \$(wc -l < $POLLS) -gt 3 ] && echo \"state=done ok\"; true; }; ue_wait job 10 10'" ""
expect "wait: a hung SSH while waiting is retried" \
  ": > $POLLS; runner 'ue_ssh() { echo x >> $POLLS; [ \$(wc -l < $POLLS) -gt 3 ] || return 124; echo \"state=done ok\"; }; ue_wait job 10 10'" ""
expect "wait: reachable and still running after the deadline gives up" \
  "! runner 'ue_status() { echo state=running; }; ue_wait job 10 10'" ""
expect "wait: unreachable for longer than the grace gives up" \
  "! runner 'ue_status() { :; }; UE_REACH_GRACE_S=300; ue_wait job 10 10'" ""
unreachable_take_over() {
  bash -ec "set -o pipefail; source '$CAMPAIGN'
    log() { echo \"\$*\" >&2; }; die() { log \"ERROR: \$*\"; exit 1; }
    sleep() { :; }; ue_ssh() { return 124; }; take_over resume" 2>&1
}
expect "take over: an unreachable UE is said, not read as no job" "grep -q 'cannot reach' <<< \"\$(unreachable_take_over)\"" "$(unreachable_take_over)"
rm -f "$POLLS"

# the measurement server: the pod whose attachments (Multus network-status)
# hold the target address, found by address rather than by name
PODS="$(python3 -c '
import json
ns = "k8s.v1.cni.cncf.io/network-status"
pod = lambda name, nets: {"metadata": {"name": name, "annotations": {ns: json.dumps(nets)}}}
print(json.dumps({"items": [
    pod("face-recognition-1", [{"interface": "eth0", "ips": ["10.42.1.134"]}, {"interface": "n6m", "ips": ["10.208.0.200"]}]),
    {"metadata": {"name": "no-annotations"}},
    pod("measurement-server-1", [{"interface": "n6m", "ips": ["10.208.0.202"]}])]}))')"
expect "server pod: found by its n6m address" "[ \"\$(pod_with_address 10.208.0.202 <<< \"\$PODS\")\" = measurement-server-1 ]" "$(pod_with_address 10.208.0.202 <<< "$PODS")"
expect "server pod: none holds the address" "[ -z \"\$(pod_with_address 10.208.0.99 <<< \"\$PODS\")\" ]" ""

# capture points: the kernel keeps only the echoes (under uplink load the rest
# would be most of the traffic); GTP-U carries a 4-byte extension both ways
expect "filter: GTP-U points keep ICMP inside GTP-U" "[ \"\$(point_filter upf-n3)\" = 'udp port 2152 and udp[33] = 1' ]" "$(point_filter upf-n3)"
expect "filter: N6m points keep plain ICMP" "[ \"\$(point_filter server)\" = icmp ]" "$(point_filter server)"
LINKS='1: lo: <LOOPBACK,UP> mtu 65536
17: veth167d0740@if3: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1450 master ovs-system
9: br-n3: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1450'
expect "veth: the host side of a pod interface, by ifindex" "[ \"\$(veth_name 17 <<< \"\$LINKS\")\" = veth167d0740 ]" "$(veth_name 17 <<< "$LINKS")"
expect "veth: an unknown ifindex gives nothing" "[ -z \"\$(veth_name 99 <<< \"\$LINKS\")\" ]" ""
UPFS="$(python3 -c '
import json
ns = "k8s.v1.cni.cncf.io/network-status"
pod = lambda name, ifs: {"metadata": {"name": name, "annotations": {ns: json.dumps([{"interface": i, "ips": [a]} for i, a in ifs])}}}
print(json.dumps({"items": [pod("netshoot-1", [("n3", "10.203.0.103"), ("n6m", "10.208.0.100")]),
                            pod("upf-cloud-1", [("n3", "10.203.0.101"), ("n6m", "10.208.0.101")])]}))')"
expect "upf: the pod holding the UPF's N3 address, not a probe with the same interfaces" \
  "[ \"\$(pod_with_address 10.203.0.101 <<< \"\$UPFS\")\" = upf-cloud-1 ]" "$(pod_with_address 10.203.0.101 <<< "$UPFS")"

# vagrant ssh reads its stdin: a loop fed on stdin that calls it must still
# start every point (the real worker_sh, a fake vagrant that swallows stdin)
F="$(mktemp -d)"
printf '#!/bin/sh\ncat > /dev/null\necho "$@" >> %s/calls\n' "$F" > "$F/vagrant"; chmod +x "$F/vagrant"
PATH="$F:$PATH" points_start tag 30 <<< "$(printf 'br-ran br-ran\nbr-n3 br-n3\nupf-n3 veth1\nupf-n6m veth2\nserver veth3\n')"
expect "points: one capture started per point" "[ \"\$(grep -c tcpdump $F/calls 2>/dev/null)\" = 5 ]" "$(cat "$F/calls" 2>&1)"
expect "points: nanosecond timestamps (the OVS steps are a few microseconds)" "grep -q 'tcpdump --time-stamp-precision=nano' $F/calls" "$(head -1 "$F/calls")"
expect "points: tcpdump's own report kept next to the capture" "grep -q '2> /tmp/kelt-pt-tag-br-ran.pcap.log' $F/calls" "$(head -1 "$F/calls")"
rm -rf "$F"

# captures can be left out (the A/B check of whether capturing changes the
# measurement): no br-ran capture, no rtt points
expect "capture: throughput captures its whole job" "[ \"\$(capture_seconds throughput \"\$s\")\" -gt 0 ]" ""
expect "capture: KELT_CAPTURE=0 leaves it out" "[ \"\$(KELT_CAPTURE=0 capture_seconds throughput \"\$s\")\" = 0 ]" "$(KELT_CAPTURE=0 capture_seconds throughput "$s")"
expect "capture: rtt points only when capturing" "[ \"\$(KELT_CAPTURE=0 rtt_points_wanted rtt)\" = no ] && [ \"\$(rtt_points_wanted rtt)\" = yes ] && [ \"\$(rtt_points_wanted throughput)\" = no ]" ""

# worker_fetch on a file without a capture report (the NIC counters) must not
# fail: under set -e a failed remote test would end the runner. The fake
# vagrant runs the remote command here; sudo just runs its command.
F="$(mktemp -d)"
printf '#!/bin/bash\nshift 3; exec bash -c "$1"\n' > "$F/vagrant"
printf '#!/bin/bash\nexec "$@"\n' > "$F/sudo"; chmod +x "$F/vagrant" "$F/sudo"
echo "epoch,rx_bytes" > "$F/ran.csv"
out="$(PATH="$F:$PATH" bash -c "set -euo pipefail; REPO_ROOT=$F; source '$CAMPAIGN'; log() { echo \"\$*\"; }
  worker_fetch $F/ran.csv $F/got/ran.csv; echo fetched" 2>&1)"
expect "fetch: a file without a capture report is fetched, the runner goes on" \
  "grep -q '^fetched$' <<< \"\$out\" && [ -s $F/got/ran.csv ] && [ ! -e $F/got/ran.csv.log ]" "$out"
rm -rf "$F"

exit $fails
