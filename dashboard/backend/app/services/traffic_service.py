"""OVS bridge traffic counter service.

Polls `ovs-ofctl dump-ports` on each 5G bridge via SSH to the worker node,
computes per-second delta rates (PPS / Bps), and exposes them keyed by
interface label (N2, N3, N4, N6-Cloud, N6-Edge).
"""

import logging
import re
import subprocess
import time
from typing import Any

from app.config import settings

log = logging.getLogger(__name__)

BRIDGES: dict[str, str] = {
    "br-n2": "N2",
    "br-n3": "N3",
    "br-n4": "N4",
    "br-n6c": "N6",
}

_RE_LOCAL = re.compile(
    r"LOCAL:\s*rx\s+pkts=(\d+),\s*bytes=(\d+).*?tx\s+pkts=(\d+),\s*bytes=(\d+)",
    re.DOTALL,
)

_RE_PORT = re.compile(
    r"port\s+\d+:\s*rx\s+pkts=(\d+),\s*bytes=(\d+).*?tx\s+pkts=(\d+),\s*bytes=(\d+)",
    re.DOTALL,
)


def _ssh(command: str, timeout: int | None = None) -> str:
    wrapped = [
        "ssh",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "LogLevel=ERROR",
        "-o", "BatchMode=yes",
        settings.worker_ssh_host,
        command,
    ]
    proc = subprocess.run(
        wrapped, capture_output=True, text=True,
        timeout=timeout or settings.shell_timeout_seconds, check=False,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"SSH failed ({proc.returncode}): {err}")
    return proc.stdout or ""


def _parse_dump_ports(raw: str) -> dict[str, int]:
    """Sum rx/tx packets and bytes across all ports of a bridge."""
    total = {"rx_packets": 0, "tx_packets": 0, "rx_bytes": 0, "tx_bytes": 0}
    for m in _RE_PORT.finditer(raw):
        total["rx_packets"] += int(m.group(1))
        total["rx_bytes"] += int(m.group(2))
        total["tx_packets"] += int(m.group(3))
        total["tx_bytes"] += int(m.group(4))
    m = _RE_LOCAL.search(raw)
    if m:
        total["rx_packets"] += int(m.group(1))
        total["rx_bytes"] += int(m.group(2))
        total["tx_packets"] += int(m.group(3))
        total["tx_bytes"] += int(m.group(4))
    return total


class TrafficService:
    def __init__(self) -> None:
        self._prev: dict[str, dict[str, int]] = {}
        self._prev_ts: float = 0.0

    def get_bridge_counters(self) -> dict[str, dict[str, int]]:
        """Fetch raw cumulative counters for every tracked bridge."""
        cmd_parts = []
        for br in BRIDGES:
            cmd_parts.append(f"echo '@@{br}@@' && sudo ovs-ofctl dump-ports {br} 2>/dev/null || true")
        cmd = " && ".join(cmd_parts)

        try:
            raw = _ssh(cmd, timeout=8)
        except Exception as exc:
            log.warning("Failed to fetch OVS counters: %s", exc)
            return {}

        counters: dict[str, dict[str, int]] = {}
        sections = raw.split("@@")
        for i in range(1, len(sections), 2):
            br_name = sections[i].strip()
            body = sections[i + 1] if i + 1 < len(sections) else ""
            if br_name in BRIDGES:
                counters[br_name] = _parse_dump_ports(body)
        return counters

    def get_counter_deltas(self) -> dict[str, dict[str, float]]:
        """Compute per-second rates since last call."""
        now = time.monotonic()
        current = self.get_bridge_counters()

        elapsed = now - self._prev_ts if self._prev_ts else 0.0
        result: dict[str, dict[str, float]] = {}

        for br, label in BRIDGES.items():
            cur = current.get(br)
            prev = self._prev.get(br)
            if cur is None:
                result[label] = {"pps": 0, "bps": 0, "rx_packets": 0, "tx_packets": 0}
                continue
            if prev is None or elapsed <= 0:
                result[label] = {"pps": 0, "bps": 0, "rx_packets": 0, "tx_packets": 0}
                continue
            dpkt = (cur["rx_packets"] + cur["tx_packets"]) - (prev["rx_packets"] + prev["tx_packets"])
            dbytes = (cur["rx_bytes"] + cur["tx_bytes"]) - (prev["rx_bytes"] + prev["tx_bytes"])
            result[label] = {
                "pps": round(max(dpkt, 0) / elapsed, 1),
                "bps": round(max(dbytes, 0) / elapsed, 1),
                "rx_packets": cur["rx_packets"],
                "tx_packets": cur["tx_packets"],
            }

        self._prev = current
        self._prev_ts = now
        return result


_instance: TrafficService | None = None


def get_traffic_service() -> TrafficService:
    global _instance
    if _instance is None:
        _instance = TrafficService()
    return _instance
