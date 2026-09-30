"""Reading the worker's captures (classic pcap, plain or gzip): GTP-U on br-ran
and br-n3, plain IPv4 on the N6m veths.

Standard library only. Used by rtt.py (echo pairing, the segments of the core)
and throughput.py (TCP segments and retransmissions seen from the core).
Owner: experiments/README.md.
"""
from __future__ import annotations

import glob
import gzip
import os
import re
import socket
import struct
from typing import Iterator

GTPU_PORT = 2152


def read_pcap(path: str) -> Iterator[tuple[float, bytes, int]]:
    """(time, frame, linktype) for every packet of a classic pcap file."""
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rb") as fh:
        head = fh.read(24)
        if len(head) < 24:
            return
        magic = struct.unpack("<I", head[:4])[0]
        if magic in (0xA1B2C3D4, 0xA1B23C4D):
            e = "<"
        else:
            e = ">"
            magic = struct.unpack(">I", head[:4])[0]
        nano = magic == 0xA1B23C4D
        link = struct.unpack(e + "I", head[20:24])[0]
        while True:
            rec = fh.read(16)
            if len(rec) < 16:
                return
            sec, frac, incl, _ = struct.unpack(e + "IIII", rec)
            data = fh.read(incl)
            yield sec + frac / (1e9 if nano else 1e6), data, link


# The rtt capture points, in the order a request crosses them (the reply
# crosses them backwards): the worker's RAN and N3 bridges (GTP-U, routed by the
# worker), the UPF's N3 and N6m veths, the measurement server's N6m veth.
POINTS = ("br-ran", "br-n3", "upf-n3", "upf-n6m", "server")


def is_capture(path: str) -> bool:
    """A capture file (.pcap, .pcap.gz), not its report (.pcap.log)."""
    return path.endswith((".pcap", ".pcap.gz"))


def captures(run_dir: str) -> list[str]:
    """The full br-ran captures of every attempt of a run (raw/worker/<n>/)."""
    return sorted(p for p in glob.glob(os.path.join(run_dir, "raw", "worker", "*", "br-ran.pcap*")) if is_capture(p))


def point_captures(run_dir: str) -> dict[str, dict[str, str]]:
    """attempt -> point -> path of the ICMP-only captures (raw/worker/<n>/points/)."""
    out: dict[str, dict[str, str]] = {}
    for path in sorted(p for p in glob.glob(os.path.join(run_dir, "raw", "worker", "*", "points", "*.pcap*"))
                       if is_capture(p)):
        attempt = path.split(os.sep)[-3]
        point = os.path.basename(path).split(".pcap")[0]
        out.setdefault(attempt, {})[point] = path
    return out


REPORT_RE = re.compile(r"^(\d+) packets (captured|received by filter|dropped by kernel)$")


def capture_report(path: str) -> dict[str, int] | None:
    """tcpdump's own report when it stopped (<capture>.log): packets captured,
    received by the filter, dropped by the kernel. None when there is none."""
    try:
        with open(path) as fh:
            lines = fh.read().splitlines()
    except OSError:
        return None
    names = {"captured": "captured", "received by filter": "received", "dropped by kernel": "dropped"}
    out = {}
    for line in lines:
        m = REPORT_RE.match(line.strip())
        if m:
            out[names[m.group(2)]] = int(m.group(1))
    return out or None


def attempt_of(path: str) -> str:
    """The attempt a raw/ue/<n>/... or raw/worker/<n>/... path belongs to."""
    parts = path.split(os.sep)
    return parts[parts.index("raw") + 2]


def _ip_payload(frame: bytes, link: int = 1) -> bytes | None:
    if link == 1:           # Ethernet, with an optional VLAN tag
        off, etype = 14, struct.unpack("!H", frame[12:14])[0]
        if etype == 0x8100:
            off, etype = 18, struct.unpack("!H", frame[16:18])[0]
    elif link == 113:       # Linux cooked
        off, etype = 16, struct.unpack("!H", frame[14:16])[0]
    else:
        return None
    return frame[off:] if etype == 0x0800 else None


def gtpu_inner(frame: bytes, link: int = 1) -> bytes | None:
    """The inner IPv4 packet of a GTP-U G-PDU, or None."""
    ip = _ip_payload(frame, link)
    if not ip or len(ip) < 20 or ip[9] != 17:
        return None
    udp = ip[(ip[0] & 0x0F) * 4:]
    if len(udp) < 16:
        return None
    sport, dport = struct.unpack("!HH", udp[:4])
    if GTPU_PORT not in (sport, dport):
        return None
    g = udp[8:]
    flags, mtype = g[0], g[1]
    if mtype != 0xFF:
        return None
    off = 8
    if flags & 0x07:
        nxt = g[11]
        off = 12
        while nxt:
            ln = g[off] * 4
            if ln == 0:
                return None
            nxt = g[off + ln - 1]
            off += ln
    inner = g[off:]
    return inner if inner and inner[0] >> 4 == 4 else None


def ip_packet(frame: bytes, link: int = 1) -> bytes | None:
    """The IPv4 packet a frame carries: the inner one for GTP-U, else the plain one."""
    inner = gtpu_inner(frame, link)
    if inner:
        return inner
    ip = _ip_payload(frame, link)
    return ip if ip and len(ip) >= 20 and ip[0] >> 4 == 4 else None


def addr(b: bytes) -> str:
    return socket.inet_ntoa(b)
