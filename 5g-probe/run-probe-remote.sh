#!/usr/bin/env bash
# run-probe-remote.sh — generate 5G-Probe-compatible result bundles on a remote UE.
#
# Produces a folder under ./results/ containing metadata.json + data.csv
# (+ iperf3_output.txt for throughput tests). Copy the folder to the probe host
# under <probe>/5g-probe/results/standalone/<YYYY-MM-DD>/ and it appears in the
# Test Results UI automatically.
#
# Dependencies: bash, iperf3, ping, awk, python3 (stdlib only, for JSON parsing).
#
# Usage:
#   ./run-probe-remote.sh throughput <target_ip> <ul|dl> <tcp|udp> [duration] [bandwidth]
#   ./run-probe-remote.sh latency    <target_ip> [count] [interval_s] [size_bytes]
#   ./run-probe-remote.sh batch      <file>     # one test per line, # = comment
#
# Examples:
#   ./run-probe-remote.sh throughput 10.45.0.1 ul tcp 30
#   ./run-probe-remote.sh throughput 10.45.0.1 dl udp 30 100M
#   ./run-probe-remote.sh latency    10.45.0.1 60 0.5
#   ./run-probe-remote.sh batch      tests.txt

set -euo pipefail

usage() {
    sed -n '2,18p' "$0" >&2
    exit 2
}

[ "$#" -ge 2 ] || usage

TEST_TYPE="$1"
TARGET_IP="$2"
shift 2

HOST_TAG="$(hostname -s)"
TS_ISO="$(date -u +%Y-%m-%dT%H:%M:%S)"
TS_FNAME="$(date +%H%M%S)"
DATE_DIR="$(date +%Y-%m-%d)"

OUT_BASE="${OUT_BASE:-./results/standalone/${DATE_DIR}}"
mkdir -p "${OUT_BASE}"

# ---------- THROUGHPUT ----------
run_throughput() {
    local direction="$1"   # ul|dl
    local proto="$2"       # tcp|udp
    local duration="${3:-30}"
    local bw="${4:-}"      # only meaningful for UDP

    case "$direction" in ul|dl) ;; *) echo "direction must be ul|dl" >&2; exit 2 ;; esac
    case "$proto" in tcp|udp) ;; *) echo "protocol must be tcp|udp" >&2; exit 2 ;; esac

    local tag="${HOST_TAG}_${TS_FNAME}_throughput_${direction}_${proto}"
    [ -n "$bw" ] && tag="${tag}_${bw}"
    local out_dir="${OUT_BASE}/${tag}"
    mkdir -p "${out_dir}"

    local -a iperf_args=(-c "${TARGET_IP}" -t "${duration}" -i 1 -J)
    [ "$direction" = "dl" ] && iperf_args+=(-R)
    if [ "$proto" = "udp" ]; then
        iperf_args+=(-u -b "${bw:-100M}")
    fi

    echo "[*] iperf3 ${iperf_args[*]}"
    local raw="${out_dir}/iperf3_output.json"
    iperf3 "${iperf_args[@]}" > "${raw}" || true

    # Parse JSON → CSV + summary via inline python
    CSV="${out_dir}/data.csv" \
    META="${out_dir}/metadata.json" \
    RAW="${raw}" \
    TS="${TS_ISO}" \
    TEST_TYPE="throughput" DIR="${direction}" PROTO="${proto}" \
    BW="${bw}" TARGET="${TARGET_IP}" DUR="${duration}" HOST="${HOST_TAG}" \
    python3 - <<'PYEOF'
import csv, json, os, sys
raw = os.environ["RAW"]
direction = os.environ["DIR"]
proto = os.environ["PROTO"]
with open(raw) as f:
    data = json.load(f)

intervals = data.get("intervals", [])
end = data.get("end", {})

rows = []
phase = direction
for idx, iv in enumerate(intervals, 1):
    streams = iv.get("streams", [])
    if not streams:
        continue
    s = streams[0]  # single-stream assumption
    rows.append({
        "#": idx,
        "protocol": proto,
        "phase": phase,
        "time_s": round(s.get("end", 0), 2),
        "interval_start_s": round(s.get("start", 0), 3),
        "interval_end_s": round(s.get("end", 0), 3),
        "pkt_bytes": s.get("bytes", ""),
        "client_Mbps": round(s.get("bits_per_second", 0) / 1e6, 3),
        "server_Mbps": "",
        "loss_pct": round(s.get("lost_percent", 0), 3) if proto == "udp" else "",
        "jitter_ms": round(s.get("jitter_ms", 0), 3) if proto == "udp" else "",
        "retransmits": s.get("retransmits", "") if proto == "tcp" else "",
        "cwnd_kB": round(s.get("snd_cwnd", 0) / 1024, 1) if proto == "tcp" and s.get("snd_cwnd") else "",
    })

fields = ["#","protocol","phase","time_s","interval_start_s","interval_end_s",
          "pkt_bytes","client_Mbps","server_Mbps","loss_pct","jitter_ms",
          "retransmits","cwnd_kB"]
with open(os.environ["CSV"], "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=fields)
    w.writeheader()
    w.writerows(rows)

# Summary
summary = {}
sum_sender = end.get("sum_sent") or end.get("sum", {})
sum_recv = end.get("sum_received") or end.get("sum", {})
client_mbps = round(sum_sender.get("bits_per_second", 0) / 1e6, 2)
server_mbps = round(sum_recv.get("bits_per_second", 0) / 1e6, 2)
pfx = direction
summary[f"{pfx}_client_mbps"] = client_mbps
summary[f"{pfx}_server_mbps"] = server_mbps
if proto == "tcp":
    summary[f"{pfx}_total_retr"] = sum_sender.get("retransmits", 0)
else:
    summary[f"{pfx}_loss_pct"] = round(sum_recv.get("lost_percent", 0), 2)
    summary[f"{pfx}_jitter_ms"] = round(sum_recv.get("jitter_ms", 0), 3)

meta = {
    "test_type": "throughput",
    "direction": direction,
    "protocol": proto,
    "bandwidth": os.environ.get("BW") or None,
    "namespace": f"remote:{os.environ['HOST']}",
    "target_ip": os.environ["TARGET"],
    "duration_s": int(os.environ["DUR"]),
    "timestamp": os.environ["TS"],
    "chart": None,
    "csv": "data.csv",
    "raw_output": "iperf3_output.json",
    "summary": summary,
    "source": "remote",
    "remote_host": os.environ["HOST"],
}
with open(os.environ["META"], "w") as f:
    json.dump(meta, f, indent=2)
print(f"[*] Wrote {os.environ['CSV']}")
print(f"[*] Wrote {os.environ['META']}")
PYEOF

    echo "[✓] Bundle ready: ${out_dir}"
}

# ---------- LATENCY ----------
run_latency() {
    local count="${1:-60}"
    local interval="${2:-0.5}"
    local size="${3:-56}"

    local tag="${HOST_TAG}_${TS_FNAME}_latency_ul"
    local out_dir="${OUT_BASE}/${tag}"
    mkdir -p "${out_dir}"

    local raw="${out_dir}/ping_output.txt"
    echo "[*] ping -c ${count} -i ${interval} -s ${size} ${TARGET_IP}"
    ping -c "${count}" -i "${interval}" -s "${size}" "${TARGET_IP}" > "${raw}" || true

    CSV="${out_dir}/data.csv" \
    META="${out_dir}/metadata.json" \
    RAW="${raw}" \
    TS="${TS_ISO}" \
    TARGET="${TARGET_IP}" COUNT="${count}" INTERVAL="${interval}" \
    SIZE="${size}" HOST="${HOST_TAG}" \
    python3 - <<'PYEOF'
import csv, json, os, re
raw_path = os.environ["RAW"]
interval = float(os.environ["INTERVAL"])
size = int(os.environ["SIZE"])
rows = []
prev_rtt = None
with open(raw_path) as f:
    for line in f:
        m_rtt = re.search(r"time[=<]([\d.]+)\s*ms", line)
        m_seq = re.search(r"icmp_seq=(\d+)", line)
        m_ttl = re.search(r"ttl=(\d+)", line)
        if not m_rtt:
            continue
        seq = int(m_seq.group(1)) if m_seq else len(rows) + 1
        rtt = float(m_rtt.group(1))
        jitter = round(abs(rtt - prev_rtt), 3) if prev_rtt is not None else 0.0
        prev_rtt = rtt
        rows.append({
            "#": seq,
            "time_s": round((seq - 1) * interval, 3),
            "rtt_ms": rtt,
            "owd_ms": round(rtt / 2, 3),
            "jitter_ms": jitter,
            "ttl": int(m_ttl.group(1)) if m_ttl else "",
            "pkt_bytes": size,
        })

fields = ["#","time_s","rtt_ms","owd_ms","jitter_ms","ttl","pkt_bytes"]
with open(os.environ["CSV"], "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=fields)
    w.writeheader()
    w.writerows(rows)

rtts = [r["rtt_ms"] for r in rows]
summary = {}
if rtts:
    summary["avg_rtt_ms"] = round(sum(rtts) / len(rtts), 2)
    summary["min_rtt_ms"] = round(min(rtts), 2)
    summary["max_rtt_ms"] = round(max(rtts), 2)
    summary["packets"] = len(rtts)

meta = {
    "test_type": "latency",
    "direction": "ul",
    "protocol": None,
    "bandwidth": None,
    "namespace": f"remote:{os.environ['HOST']}",
    "target_ip": os.environ["TARGET"],
    "duration_s": int(float(os.environ["COUNT"]) * interval),
    "timestamp": os.environ["TS"],
    "chart": None,
    "csv": "data.csv",
    "raw_output": "ping_output.txt",
    "summary": summary,
    "source": "remote",
    "remote_host": os.environ["HOST"],
    "ping_count": int(os.environ["COUNT"]),
    "ping_interval": interval,
    "ping_packet_size": size,
}
with open(os.environ["META"], "w") as f:
    json.dump(meta, f, indent=2)
print(f"[*] Wrote {os.environ['CSV']}")
print(f"[*] Wrote {os.environ['META']}")
PYEOF

    echo "[✓] Bundle ready: ${out_dir}"
}

run_batch() {
    local file="$1"
    [ -f "$file" ] || { echo "batch file not found: $file" >&2; exit 2; }
    echo "[*] Batch mode: $file"
    local n=0 ok=0 fail=0
    while IFS= read -r line || [ -n "$line" ]; do
        # Strip leading/trailing whitespace and skip comments/blank lines
        line="$(echo "$line" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
        [ -z "$line" ] && continue
        [[ "$line" == \#* ]] && continue
        # Directive: `pause N` → sleep N seconds, no test executed
        if [[ "$line" =~ ^pause[[:space:]]+([0-9]+)$ ]]; then
            local p="${BASH_REMATCH[1]}"
            echo
            echo "===== pause ${p}s ====="
            sleep "$p"
            continue
        fi
        n=$((n+1))
        echo
        echo "===== [$n] $line ====="
        # shellcheck disable=SC2086
        if "$0" $line; then ok=$((ok+1)); else fail=$((fail+1)); echo "[!] line failed: $line" >&2; fi
    done < "$file"
    echo
    echo "[✓] Batch complete: $ok ok, $fail failed (of $n)"
}

case "${TEST_TYPE}" in
    throughput) run_throughput "$@" ;;
    latency)    run_latency "$@" ;;
    batch)      run_batch "$@" ;;
    *)          usage ;;
esac

echo
echo "Next: copy the bundle(s) to the probe host. From the probe machine:"
echo "  scp -r ${HOST_TAG}:$(pwd)/results/standalone/${DATE_DIR}/ \\"
echo "      <probe-path>/5g-probe/results/standalone/"
echo
echo "Then refresh Test Results in the probe UI."
