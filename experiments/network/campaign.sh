# The network campaigns (thesis 4.10.1), sourced by run.sh after lib/common.sh.
#
# The runner never measures over SSH: it copies a job (ue-run.sh, job.env,
# schedule.txt) to /tmp/kelt-run-<slug>-<stamp>/ on the UE, starts it
# detached and disconnects. While the job runs, the worker captures GTP-U on
# br-ran and samples the footprint. After the
# expected end the runner fetches the job's files, checks them against their
# manifest, and only then removes the job directory from the UE.
#
#   KELT_UE_SSH       ssh target of the UE (required): <user>@<host>
#   KELT_NET_REPEATS  runs per throughput combination (5)
#   KELT_NET_RUN_S    seconds per throughput run (60)
#   KELT_RTT_REPEATS  runs per rtt condition (3)
#   KELT_RTT_S        seconds of ping per rtt run (300)
#   KELT_UE_PAUSE_UNITS  systemd units stopped on the UE for the job (none by default)
#
# Owner: experiments/README.md.

NET_START_DELAY_S=10
NET_PAUSE_S=10

# ── pure parts (tests/network/campaign.test.sh) ───────────────────────────────

# The pod whose attachments hold <ip>, from `kubectl get pods -o json` on stdin
# (Multus records every interface's addresses in the network-status annotation).
pod_with_address() {
  python3 -c '
import json, sys
ip = sys.argv[1]
for p in json.load(sys.stdin)["items"]:
    st = p["metadata"].get("annotations", {}).get("k8s.v1.cni.cncf.io/network-status")
    if st and any(ip in n.get("ips", []) for n in json.loads(st)):
        print(p["metadata"]["name"])
        break
' "$1"
}

# yes when a run of <slug> captures the echoes at the rtt points.
rtt_points_wanted() { [ "$1" = rtt ] && [ "${KELT_CAPTURE:-1}" != 0 ] && echo yes || echo no; }

# The rtt capture points, in the order a request crosses them (network/pcap.py
# POINTS), and the kernel filter of each: the echoes only. GTP-U carries a
# 4-byte PDU session container both ways, so the inner protocol is udp[33].
RTT_POINTS="br-ran br-n3 upf-n3 upf-n6m server"
point_filter() {
  case "$1" in
    br-ran|br-n3|upf-n3) echo 'udp port 2152 and udp[33] = 1' ;;
    *) echo icmp ;;
  esac
}

# The interface name for an ifindex, from `ip -o link` on stdin (a pod's
# /sys/class/net/<if>/iflink is the ifindex of its veth's host side).
veth_name() { awk -v i="$1" -F': ' '$1 == i { sub(/@.*/, "", $2); print $2; exit }'; }

# Round robin over the four combinations, so a slow disturbance spreads
# over all of them instead of hitting five runs of one.
throughput_schedule() {
  local repeats="$1" secs="$2" r i=0 c extra
  for r in $(seq "$repeats"); do
    # Uplink goodput is what the server receives: the client only knows what it wrote.
    for c in "dl1:-R" "dl4:-R -P 4" "ul1:--get-server-output" "ul4:-P 4 --get-server-output"; do
      i=$((i + 1))
      extra="${c#*:}"
      echo "$i ${c%%:*} iperf3 -t $secs -i 0.1 -J${extra:+ $extra}"
    done
  done
}

# Idle runs first (the capture covers only them), then the runs under uplink
# load: iperf3 4 streams for the ping plus 10 s before and after.
rtt_schedule() {
  local repeats="$1" secs="$2" i=0 count
  count=$((secs * 10))
  for _ in $(seq "$repeats"); do i=$((i + 1)); echo "$i idle ping $count 0.1"; done
  for _ in $(seq "$repeats"); do i=$((i + 1)); echo "$i load ping+load $count 0.1 -t $((secs + 20)) -P 4"; done
}

# Seconds a schedule (stdin) takes, with <pause> seconds between runs.
schedule_seconds() {
  awk -v pause="$1" '
    { n++; d = 0
      for (i = 4; i <= NF; i++) if ($i == "-t") d = $(i + 1)
      if ($3 == "ping") d = $4 * $5
      total += d }
    END { printf "%d\n", total + (n > 1 ? (n - 1) * pause : 0) }'
}

# nat when the UE's address is outside the UE pool (a router in between),
# direct when it is a pool address (the modem is in the UE).
ue_mode() {
  python3 -c 'import ipaddress, sys
a = ipaddress.ip_interface(sys.argv[1]).ip
print("direct" if a in ipaddress.ip_network(sys.argv[2], strict=False) else "nat")' "$1" "$2"
}

# A job is one attempt at a run: kelt-run-<campaign>-<stamp>-<attempt>.
job_name() { echo "kelt-run-$1-$2-$3"; }
job_attempt() { echo "${1##*-}"; }
job_run_dir() { local n="${2#kelt-run-}"; n="${n%-*}"; echo "$1/${n%-*}/${n##*-}"; }

# The schedule lines of a run that no attempt finished (no .meta anywhere):
# what a resumed run still has to do. A run cut halfway is redone whole.
remaining_schedule() {
  local run_dir="$1" idx name rest
  while read -r idx name rest; do
    [ -n "$idx" ] || continue
    compgen -G "$run_dir/raw/ue/*/runs/$idx-$name.meta" > /dev/null || echo "$idx $name $rest"
  done < "$run_dir/schedule.txt"
}

next_attempt() {
  local n=0 d
  for d in "$1"/raw/ue/*/; do
    d="$(basename "$d")"
    [[ "$d" =~ ^[0-9]+$ ]] && [ "$d" -gt "$n" ] && n="$d"
  done
  echo $((n + 1))
}

# ── the UE ────────────────────────────────────────────────────────────────────

# Every call has its own time limit: ConnectTimeout covers only the TCP
# connect, and an SSH session over the cellular link has hung for minutes.
ue_ssh() {
  timeout "${UE_SSH_TIMEOUT:-60}" ssh -o BatchMode=yes -o ConnectTimeout=15 \
    -o ServerAliveInterval=15 -o ServerAliveCountMax=3 "$KELT_UE_SSH" "$@"
}

# Refuses to start next to an earlier job, checks the tools and the path to the
# target, and prints the UE's link (the default route's device).
ue_preflight() {
  local left
  [ -n "${KELT_UE_SSH:-}" ] || die "set KELT_UE_SSH=<user>@<host>: the UE to measure from"
  ue_ssh true || die "cannot reach $KELT_UE_SSH over SSH"
  left="$(ue_ssh 'ls -d /tmp/kelt-run-* 2>/dev/null; pgrep -f "[u]e-run.sh" >/dev/null && echo "a job is running"; true')"
  ue_ssh 'sudo -n true' || die "the UE user needs passwordless sudo (systemd-run, pausing units)"
  [ -z "$left" ] || die "earlier job on the UE: $(echo $left). Continue it with run.sh resume, or end it with run.sh stop"
  ue_ssh 'command -v iperf3 >/dev/null && command -v ping >/dev/null' || die "iperf3 or ping missing on the UE"
  ue_ssh "ping -c 3 -W 2 -q $NET_TARGET >/dev/null" || die "the UE cannot reach $NET_TARGET"
  ue_ssh "timeout 5 bash -c '</dev/tcp/$NET_TARGET/5201'" || die "no iperf3 server on $NET_TARGET:5201"
  ue_ssh "ip -4 route show default | awk '{for (i = 1; i < NF; i++) if (\$i == \"dev\") { print \$(i + 1); exit }}'"
}

# ── the core side ─────────────────────────────────────────────────────────────

# vagrant ssh reads stdin: never let it take the caller's (a loop fed on stdin).
worker_sh() { (cd "$REPO_ROOT" && SSH_AUTH_SOCK= vagrant ssh worker -c "$1" 2>/dev/null < /dev/null) | sed '/^\[Testbed\]/d'; }

# The measurement server's pod, found by the target address.
server_pod() { kubectl get pods -n "$APPS_NS" -o json | pod_with_address "$NET_TARGET"; }

prom_value() {
  local prom="http://$(node_ip):$(svc_nodeport "$MONITORING_NS" "${KELT_PROM_SVC:-prometheus}")"
  curl -s --max-time 10 "$prom/api/v1/query" --data-urlencode "query=$1" \
    | python3 -c 'import json,sys
r = json.load(sys.stdin)["data"]["result"]
print(r[0]["value"][1] if r else "")' 2>/dev/null
}

# ue.json: what was measured from where. Called at the start (with the UE's
# facts) and at the end (sessions and local time again).
ue_json() {
  local run_dir="$1" phase="$2"; shift 2
  python3 - "$run_dir/ue.json" "$phase" "$@" <<'PY'
import json, os, sys, time
path, phase, *kv = sys.argv[1:]
doc = json.load(open(path)) if os.path.exists(path) else {}
for item in kv:
    k, v = item.split("=", 1)
    doc[k] = v
doc[f"local_{phase}"] = time.strftime("%Y-%m-%d %H:%M:%S %Z")
json.dump(doc, open(path, "w"), indent=1)
PY
}

ran_nic() { worker_sh "sudo ovs-vsctl list-ports br-ran | grep -v '^patch-\|^veth' | head -1" | tr -d '\r'; }

# Worker files of one attempt: /tmp/kelt-<what>-<stamp>-<attempt>.<ext>, each
# with the PID of its writer next to it, for worker_fetch to stop it.
# Headers only (128 bytes: outer Ethernet/IP/UDP/GTP-U, inner IP and TCP/ICMP).
worker_capture_start() {
  local out="$1" secs="$2" iface="${3:-br-ran}" filter="${4:-udp port 2152}"
  worker_sh "sudo setsid nohup timeout $secs tcpdump --time-stamp-precision=nano -i $iface -s 128 -U -w $out '$filter' \
    > /dev/null 2> $out.log < /dev/null & echo \$! > $out.pid"
}

# points_start <tag> <seconds>, "<point> <interface>" lines on stdin: one
# filtered capture per point, /tmp/kelt-pt-<tag>-<point>.pcap on the worker.
points_start() {
  local pt iface
  while read -r pt iface; do
    [ -n "$pt" ] && worker_capture_start "/tmp/kelt-pt-$1-$pt.pcap" "$2" "$iface" "$(point_filter "$pt")"
  done
  return 0
}

# points_fetch <tag> <dest dir>, "<point> [...]" lines on stdin.
points_fetch() {
  local pt _
  while read -r pt _; do
    [ -n "$pt" ] && worker_fetch "/tmp/kelt-pt-$1-$pt.pcap" "$2/$pt.pcap.gz"
  done
  return 0
}

# "<point> <interface>" for every rtt capture point: the worker's RAN and N3
# bridges, and the host side of the UPF's N3 and N6m veths and of the server's
# N6m veth (found from each pod's iflink, which does not age out like the
# switch's forwarding table).
capture_points() {
  local server="$1" upf links
  # The UPF the sessions use: the pod holding its N3 address (a probe pod may
  # have N3 and N6m interfaces too).
  upf="$(kubectl get pods -n "$CORE_NS" -o json | pod_with_address "$(plan_value upf_cloud_n3_ip)")"
  [ -n "$upf" ] || die "no pod in $CORE_NS holds the UPF's N3 address $(plan_value upf_cloud_n3_ip)"
  links="$(worker_sh 'ip -o link')"
  echo "br-ran br-ran"
  echo "br-n3 br-n3"
  pod_veth upf-n3 "$CORE_NS" "$upf" n3 "$links"
  pod_veth upf-n6m "$CORE_NS" "$upf" n6m "$links"
  pod_veth server "$APPS_NS" "$server" n6m "$links"
}

# "<point> <veth>" for one pod interface: <point> <namespace> <pod> <interface> <ip -o link>.
pod_veth() {
  local idx name
  idx="$(kubectl exec -n "$2" "$3" -- cat "/sys/class/net/$4/iflink" | tr -d '\r')"
  name="$(veth_name "$idx" <<< "$5")"
  [ -n "$name" ] || die "no veth on the worker for $3 $4 (iflink ${idx:-none})"
  echo "$1 $name"
}

# Stops the writer (by the PID its start recorded: a pkill -f on the path would
# also match this very command line), copies the file into the run (base64:
# vagrant ssh is not binary safe; gzip on the worker first when dst ends .gz)
# and removes it from the worker.
worker_fetch() {
  local src="$1" dst="$2" read="$1"
  mkdir -p "$(dirname "$dst")"
  [[ "$dst" == *.gz ]] && read="$src.gz"
  worker_sh "sudo kill \$(cat $src.pid 2>/dev/null) 2>/dev/null; sleep 1; [ -f $src ] || exit 1
    $([[ "$dst" == *.gz ]] && echo "sudo gzip -1 -f $src;") sudo base64 -w0 $read && sudo rm -f $src $read $src.pid" \
    | base64 -d > "$dst" && [ -s "$dst" ] || { rm -f "$dst"; log "could not fetch $src from the worker"; }
  # A capture's own report, written when it stopped (captured, received,
  # dropped by kernel): whether the capture is complete.
  worker_sh "if [ -f $src.log ]; then sudo cat $src.log; sudo rm -f $src.log; fi" > "${dst%.gz}.log"
  [ -s "${dst%.gz}.log" ] || rm -f "${dst%.gz}.log"
}

# ── launch, wait, fetch ───────────────────────────────────────────────────────

# ue_launch <run_dir> <job name> <ue link> <deadline s> (schedule on stdin)
ue_launch() {
  local run_dir="$1" job="$2" link="$3" deadline="$4" stage
  stage="$run_dir/job/$(job_attempt "$job")"; mkdir -p "$stage"
  cat > "$stage/schedule.txt"
  cp "$EXP_ROOT/network/ue-run.sh" "$stage/"
  {
    echo "KELT_TARGET=$NET_TARGET"; echo "KELT_IF=$link"; echo "KELT_JOB_ID=$job"
    echo "KELT_DEADLINE_S=$deadline"; echo "KELT_START_DELAY_S=$NET_START_DELAY_S"; echo "KELT_PAUSE_S=$NET_PAUSE_S"
    echo "KELT_PAUSE_UNITS=\"${KELT_UE_PAUSE_UNITS:-}\""
  } > "$stage/job.env"
  tar -C "$stage" -czf - . | UE_SSH_TIMEOUT=300 ue_ssh "mkdir -p /tmp/$job && tar -C /tmp/$job -xzf -" \
    || die "could not copy the job to the UE"
  # A transient systemd unit, as the UE's user: fully apart from this SSH
  # session, which then closes at once (a `setsid nohup ... &` kept it open for
  # the first minute of the job). Collected when it ends, whatever its result.
  ue_ssh "sudo -n systemd-run --quiet --unit=$job --uid=\$(id -u) --gid=\$(id -g) \
    --working-directory=/tmp/$job --property=CollectMode=inactive-or-failed \
    /usr/bin/timeout -s TERM $deadline /bin/bash -c 'exec ./ue-run.sh > run.log 2>&1 < /dev/null'" \
    || die "could not start the job on the UE"
  log "job $job started on $KELT_UE_SSH; no SSH until it ends (~$(( (deadline + 59) / 60 )) min at most)"
}

# Empty when the UE cannot be reached (an SSH that fails or hangs), which the
# callers read as "not yet": under set -e a failed call would end the runner.
ue_status() { ue_ssh "cat /tmp/$1/status 2>/dev/null" 2>/dev/null || true; }

# Fetches an attempt into <run_dir>/raw/ue/<attempt>, checks the manifest, then
# cleans the UE.
ue_fetch() {
  local run_dir="$1" job="$2" dst
  dst="$run_dir/raw/ue/$(job_attempt "$job")"
  mkdir -p "$dst"
  UE_SSH_TIMEOUT=600 ue_ssh "tar -C /tmp/$job -czf - --exclude=ue-run.sh --exclude=job.env ." | tar -C "$dst" -xzf - \
    || { log "fetch failed; the job stays on the UE"; return 1; }
  (cd "$dst" && sha256sum -c --quiet MANIFEST.sha256) || { log "manifest check failed; the job stays on the UE"; return 1; }
  ue_ssh "rm -rf /tmp/$job"
  log "fetched $(find "$dst" -type f | wc -l) files, job removed from the UE"
}

# Sleeps through the job (no SSH meanwhile), then waits for its end. A UE that
# answers but has not ended by the deadline has failed; one that cannot be
# reached is waited for UE_REACH_GRACE_S more (SSH to the UE can cross the link
# just measured, and has stayed silent for minutes after a job).
ue_wait() {
  local job="$1" expected="$2" deadline="$3" grace="${UE_REACH_GRACE_S:-1800}" waited st
  sleep "$((expected + NET_START_DELAY_S + 15))"
  waited=$((expected + NET_START_DELAY_S + 15))
  while :; do
    st="$(ue_status "$job")"
    case "$st" in state=done*|state=aborted*) log "UE: $st"; return 0 ;; esac
    if [ -n "$st" ]; then
      [ "$waited" -ge "$((deadline + 120))" ] && { log "UE: no end after the deadline ($st)"; return 1; }
    else
      [ "$waited" -ge "$((deadline + grace))" ] && { log "UE: unreachable ${grace}s after the deadline"; return 1; }
      log "UE: not reachable, trying again in 60 s"
    fi
    sleep 60; waited=$((waited + 60))
  done
}

# Records what an attempt left unfinished: every run cut or never started
# (run.sh resume redoes them), and the attempt itself when it did not end done.
mark_unfinished() {
  local run_dir="$1" attempt="$2" idx name st
  while read -r idx name _; do
    (cd "$EXP_ROOT" && python3 -m lib.runmeta discard "$run_dir" "run $idx $name, attempt $attempt" "not finished")
  done < <(remaining_schedule "$run_dir")
  st="$(cat "$run_dir/raw/ue/$attempt/status" 2>/dev/null || echo 'no status')"
  grep -q '^state=done' <<< "$st" || (cd "$EXP_ROOT" && python3 -m lib.runmeta discard "$run_dir" "attempt $attempt" "$st")
}

# ── campaigns ─────────────────────────────────────────────────────────────────

# One attempt at a run: the given schedule lines, with the footprint samplers
# and, when capture_s > 0, the worker's br-ran capture for that long.
net_attempt() {
  local run_dir="$1" sched="$2" capture_s="$3" link="$4" slug stamp attempt job expected deadline server nic
  local points="" pt iface
  slug="$(basename "$(dirname "$run_dir")")"; stamp="$(basename "$run_dir")"
  attempt="$(next_attempt "$run_dir")"; job="$(job_name "$slug" "$stamp" "$attempt")"
  expected="$(schedule_seconds "$NET_PAUSE_S" <<< "$sched")"
  deadline=$((expected + NET_START_DELAY_S + 300))
  server="$(server_pod)"
  [ -n "$server" ] || die "no pod in $APPS_NS holds $NET_TARGET (apps_measurement_server_enabled?)"
  nic="$(ran_nic)"; [ -n "$nic" ] || die "no RAN NIC on br-ran (is the RAN attached?)"
  # rtt: the echoes at every point of the core, for the whole job (idle and load).
  if [ "$(rtt_points_wanted "$slug")" = yes ]; then points="$(capture_points "$server")" || exit 1; fi
  ue_json "$run_dir" "attempt_${attempt}_start" \
    "ue_ssh=$KELT_UE_SSH" "target=$NET_TARGET" "ue_link=$link" \
    "ue_address=$(ue_ssh "ip -4 -o addr show $link | awk '{print \$4}' | head -1")" \
    "mode=$(ue_mode "$(ue_ssh "ip -4 -o addr show $link | awk '{print \$4}' | head -1")" "$(plan_value ue_internet_subnet)")" \
    "ue_tcp_cc=$(ue_ssh 'cat /proc/sys/net/ipv4/tcp_congestion_control')" \
    "server_pod=$server" "server_tcp_cc=$(kubectl exec -n "$APPS_NS" "$server" -- cat /proc/sys/net/ipv4/tcp_congestion_control | tr -d '\r')" \
    "ran_nic=$nic" "attempts=$attempt" "capture=${KELT_CAPTURE:-1}" "capture_points=$(paste -sd, <<< "$points")" \
    "attempt_${attempt}_sessions_start=$(prom_value 'sum(fivegs_smffunction_sm_sessionnbr)')" \
    "attempt_${attempt}_ran_ue_start=$(prom_value 'sum(ran_ue)')"
  footprint_start "$stamp-$attempt" "$((deadline + 120))" "$run_dir/raw/footprint/$attempt"
  [ "$capture_s" -gt 0 ] && worker_capture_start "/tmp/kelt-br-ran-$stamp-$attempt.pcap" "$capture_s"
  points_start "$stamp-$attempt" "$((deadline + 120))" <<< "$points"
  (cd "$EXP_ROOT" && python3 -m lib.runmeta open "$run_dir" "attempt-$attempt")
  ue_launch "$run_dir" "$job" "$link" "$deadline" <<< "$sched"
  ue_wait "$job" "$expected" "$deadline"
  (cd "$EXP_ROOT" && python3 -m lib.runmeta close "$run_dir" "attempt-$attempt")
  ue_fetch "$run_dir" "$job" || die "the job's files stay on the UE in /tmp/$job; run.sh resume or run.sh stop fetches them"
  footprint_stop "$stamp-$attempt" "$run_dir/raw/footprint/$attempt"
  [ "$capture_s" -gt 0 ] && worker_fetch "/tmp/kelt-br-ran-$stamp-$attempt.pcap" "$run_dir/raw/worker/$attempt/br-ran.pcap.gz"
  points_fetch "$stamp-$attempt" "$run_dir/raw/worker/$attempt/points" <<< "$points"
  ue_json "$run_dir" "attempt_${attempt}_end" \
    "attempt_${attempt}_sessions_end=$(prom_value 'sum(fivegs_smffunction_sm_sessionnbr)')" \
    "attempt_${attempt}_ran_ue_end=$(prom_value 'sum(ran_ue)')"
  mark_unfinished "$run_dir" "$attempt"
}

# How long the worker captures for an attempt's schedule: the whole job for
# throughput; for rtt only its idle runs, which come first.
# KELT_CAPTURE=0 leaves every capture out (the check of whether capturing
# changes the measurement).
capture_seconds() {
  local slug="$1" sched="$2" part
  [ "${KELT_CAPTURE:-1}" = 0 ] && { echo 0; return; }
  case "$slug" in
    throughput) part="$sched" ;;
    rtt) part="$(grep ' idle ' <<< "$sched" || true)" ;;
  esac
  [ -n "$part" ] || { echo 0; return; }
  echo $((NET_START_DELAY_S + $(schedule_seconds "$NET_PAUSE_S" <<< "$part") + 30))
}

net_campaign() {
  local slug="$1" sched="$2" run_dir link
  link="$(ue_preflight)" || exit 1
  run_dir="$(begin_run "$slug" "measurement server $NET_TARGET from $KELT_UE_SSH")"
  printf '%s\n' "$sched" > "$run_dir/schedule.txt"
  net_attempt "$run_dir" "$sched" "$(capture_seconds "$slug" "$sched")" "$link"
  net_summarise "$run_dir"
}

net_summarise() {
  local run_dir="$1" slug left
  slug="$(basename "$(dirname "$run_dir")")"
  python3 "$EXP_ROOT/network/$slug.py" "$run_dir"
  # The footprint per run condition; throughput leaves out the same start of
  # every run as its own statistics.
  footprint_summarise "$run_dir" --discard "$( [ "$slug" = throughput ] \
    && (cd "$EXP_ROOT/network" && python3 -c 'from throughput import DISCARD_S; print(DISCARD_S)') || echo 0)"
  check_run "$run_dir"
  left="$(remaining_schedule "$run_dir" | wc -l)"
  [ "$left" = 0 ] && log "$slug -> $run_dir" || log "$slug -> $run_dir ($left run(s) not finished: run.sh resume)"
}

run_throughput() {
  net_campaign throughput "$(throughput_schedule "${KELT_NET_REPEATS:-5}" "${KELT_NET_RUN_S:-60}")"
}

run_rtt() {
  net_campaign rtt "$(rtt_schedule "${KELT_RTT_REPEATS:-3}" "${KELT_RTT_S:-300}")"
}

# Fetches a job left on the UE into its run (stopping it first with `stop`),
# with the worker's files of the same attempt, and cleans the UE. Prints the
# run directory.
take_over() {
  local mode="$1" job st run_dir stamp attempt slug pt
  ue_ssh true || die "cannot reach $KELT_UE_SSH over SSH; try again later"
  job="$(ue_ssh 'ls -d /tmp/kelt-run-* 2>/dev/null | head -1' | xargs -r basename)"
  [ -n "$job" ] || return 0
  st="$(ue_status "$job")"
  if ! grep -q '^state=done\|^state=aborted' <<< "$st"; then
    [ "$mode" = stop ] || die "$job is still running on the UE: wait for it, or end it with run.sh stop"
    ue_ssh "sudo -n systemctl stop $job 2>/dev/null || pkill -TERM -f '[u]e-run.sh'" || true
    for _ in $(seq 30); do ue_status "$job" | grep -q '^state=done\|^state=aborted' && break; sleep 2; done
  fi
  run_dir="$(job_run_dir "$RUNS_DIR" "$job")"; attempt="$(job_attempt "$job")"
  slug="$(basename "$(dirname "$run_dir")")"; stamp="$(basename "$run_dir")"
  [ -f "$run_dir/schedule.txt" ] || die "$run_dir has no schedule.txt: not a run started by this runner"
  ue_fetch "$run_dir" "$job" >&2 || die "fetch failed; nothing removed from the UE"
  footprint_stop "$stamp-$attempt" "$run_dir/raw/footprint/$attempt" >&2
  worker_fetch "/tmp/kelt-br-ran-$stamp-$attempt.pcap" "$run_dir/raw/worker/$attempt/br-ran.pcap.gz" >&2
  if [ "$slug" = rtt ]; then
    tr ' ' '\n' <<< "$RTT_POINTS" | points_fetch "$stamp-$attempt" "$run_dir/raw/worker/$attempt/points" >&2
  fi
  [ "$mode" = stop ] && ! grep -q '^state=done' <<< "$st" && \
    (cd "$EXP_ROOT" && python3 -m lib.runmeta discard "$run_dir" "attempt $attempt" "stopped with run.sh stop")
  mark_unfinished "$run_dir" "$attempt"
  echo "$run_dir"
}

# Ends a job left on the UE and keeps what it measured.
run_stop() {
  local run_dir
  [ -n "${KELT_UE_SSH:-}" ] || die "set KELT_UE_SSH=<user>@<host>"
  run_dir="$(take_over stop)"
  [ -n "$run_dir" ] || { log "no job on $KELT_UE_SSH"; return 0; }
  net_summarise "$run_dir"
}

# Continues a run that did not finish: takes over its job if it is still on
# the UE, then runs only what no attempt finished, as a new attempt of the same
# run (a run cut halfway is redone whole). Without a job on the UE the run is
# named: run.sh resume <run-dir>.
run_resume() {
  local run_dir="${1:-}" sched link
  [ -n "${KELT_UE_SSH:-}" ] || die "set KELT_UE_SSH=<user>@<host>"
  local taken; taken="$(take_over resume)"
  run_dir="${taken:-$run_dir}"
  [ -n "$run_dir" ] || die "no job on the UE: name the run, run.sh resume <run-dir>"
  [ -f "$run_dir/schedule.txt" ] || die "$run_dir has no schedule.txt"
  run_dir="$(cd "$run_dir" && pwd)"   # the run's helpers run from $EXP_ROOT
  sched="$(remaining_schedule "$run_dir")"
  if [ -z "$sched" ]; then log "nothing left to run in $run_dir"; net_summarise "$run_dir"; return 0; fi
  link="$(ue_preflight)" || exit 1
  log "resuming $(basename "$(dirname "$run_dir")")/$(basename "$run_dir"): $(wc -l <<< "$sched") run(s)"
  net_attempt "$run_dir" "$sched" "$(capture_seconds "$(basename "$(dirname "$run_dir")")" "$sched")" "$link"
  net_summarise "$run_dir"
}
