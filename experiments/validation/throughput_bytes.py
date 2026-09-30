#!/usr/bin/env python3
"""Validation T1: the throughput bytes against the br-ran capture.

  throughput_bytes.py <throughput-run-dir>

Per run, the TCP sequence range each flow covers on br-ran (exact unique
bytes, whatever the retransmissions) against the bytes iperf3's receiver
counted, the basis of the goodput reported. Every byte the receiver counted
crossed br-ran first, in either direction, so:

- received <= br-ran, always (a violation means the tool counts bytes the
  network did not carry);
- received >= 97% of br-ran: the rest is data in flight or in buffers when
  iperf3's timer ended, described, not modelled.

Owner: experiments/README.md.
"""
from __future__ import annotations

import glob
import json
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from network.pcap import addr, captures, gtpu_inner, read_pcap  # noqa: E402
from network.throughput import read_meta  # noqa: E402


def sequence_ranges(run_dir: str, target: str, windows: dict[str, tuple]) -> dict[str, int]:
    """run -> bytes of sequence space covered by its flows in its own direction."""
    flows: dict[tuple, list] = {}
    for path in captures(run_dir):
        for t, frame, link in read_pcap(path):
            ip = gtpu_inner(frame, link)
            if not ip or ip[9] != 6:
                continue
            run = next((k for k, (a, b) in windows.items() if a <= t <= b), None)
            if not run:
                continue
            hl = (ip[0] & 0x0F) * 4
            src = addr(ip[12:16])
            sport, dport, seq, _, off = struct.unpack("!HHIIB", ip[hl:hl + 13])
            payload = struct.unpack("!H", ip[2:4])[0] - hl - (off >> 4) * 4
            direction = "dl" if src == target else "ul"
            if payload <= 0 or not run.split("-", 1)[1].startswith(direction):
                continue
            key = (run, sport, dport)
            first = flows.setdefault(key, [seq, 0, 0])[0]
            rel = (seq - first) % 2 ** 32
            rel = rel - 2 ** 32 if rel >= 2 ** 31 else rel
            f = flows[key]
            f[1], f[2] = min(f[1], rel), max(f[2], rel + payload)
    out: dict[str, int] = {}
    for (run, _, _), (_, low, top) in flows.items():
        out[run] = out.get(run, 0) + top - low
    return out


def main() -> int:
    run_dir = sys.argv[1]
    with open(os.path.join(run_dir, "ue.json")) as fh:
        target = json.load(fh)["target"]
    windows, counts = {}, {}
    for meta in glob.glob(os.path.join(run_dir, "raw", "ue", "*", "runs", "*.meta")):
        run = os.path.basename(meta)[:-5]
        m = read_meta(meta)
        windows[run] = (m["start"], m["end"])
        with open(meta[:-5] + ".json") as fh:
            end = json.load(fh).get("end", {})
        counts[run] = (end.get("sum_sent", {}).get("bytes"), end.get("sum_received", {}).get("bytes"))
    ranges = sequence_ranges(run_dir, target, windows)
    fails = 0
    for run in sorted(ranges, key=lambda r: int(r.split("-")[0])):
        _, received = counts[run]
        seen = ranges[run]
        share = received / seen * 100
        ok = received <= seen and share >= 97
        fails += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {run}: received {received} B = {share:.2f}% of the {seen} B seen on br-ran")
    return 1 if fails or not ranges else 0


if __name__ == "__main__":
    raise SystemExit(main())
