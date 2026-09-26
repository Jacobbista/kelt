#!/usr/bin/env bash
# Export the plane filter counters (nftables table inet kelt_planes, see
# ovs-setup.sh) for the node-exporter textfile collector: one series per filter
# rule, so Prometheus sees every crossing between 5G planes on the worker,
# allowed or not.
# See docs/architecture/plane-isolation.md
set -uo pipefail

: "${PLANE_METRICS_DIR:?PLANE_METRICS_DIR must be set by the ds-net-setup DaemonSet}"
: "${PLANE_METRICS_INTERVAL:?PLANE_METRICS_INTERVAL must be set by the ds-net-setup DaemonSet}"
: "${PLANE_FILTER_MODE:?PLANE_FILTER_MODE must be set by the ds-net-setup DaemonSet}"

OUT="${PLANE_METRICS_DIR}/kelt_planes.prom"
mkdir -p "$PLANE_METRICS_DIR"

write_once() {
  local tmp="${OUT}.$$"
  {
    echo "# HELP kelt_plane_crossing_packets_total Packets crossing a 5G plane boundary on the worker, by filter rule."
    echo "# TYPE kelt_plane_crossing_packets_total counter"
    echo "# HELP kelt_plane_crossing_bytes_total Bytes crossing a 5G plane boundary on the worker, by filter rule."
    echo "# TYPE kelt_plane_crossing_bytes_total counter"
    echo "# HELP kelt_plane_filter_enforced 1 when not-allowed crossings are dropped, 0 when only counted."
    echo "# TYPE kelt_plane_filter_enforced gauge"
    echo "kelt_plane_filter_enforced $([[ "$PLANE_FILTER_MODE" == "enforce" ]] && echo 1 || echo 0)"
    # Counted rules carry a comment "allowed: ..." or "not allowed: ...".
    nft list chain inet kelt_planes forward 2>/dev/null | awk '
      /counter packets/ && match($0, /comment "(allowed|not allowed): [^"]*"/) {
        c = substr($0, RSTART + 9, RLENGTH - 10)
        split(c, parts, ": ")
        verdict = (parts[1] == "allowed") ? "allowed" : "not_allowed"
        for (i = 1; i < NF; i++) {
          if ($i == "packets") pkts = $(i + 1)
          if ($i == "bytes") bytes = $(i + 1)
        }
        printf "kelt_plane_crossing_packets_total{verdict=\"%s\",rule=\"%s\"} %s\n", verdict, parts[2], pkts
        printf "kelt_plane_crossing_bytes_total{verdict=\"%s\",rule=\"%s\"} %s\n", verdict, parts[2], bytes
      }'
  } > "$tmp" && mv "$tmp" "$OUT"
}

while true; do
  write_once
  sleep "$PLANE_METRICS_INTERVAL"
done
