#!/usr/bin/env python3
"""Summary of one rtt run (thesis 4.10.1.2), with the core/access split.

  rtt.py <run-dir>

Reads the UE job's ping outputs (raw/ue/<attempt>/runs/<i>-idle.ping,
<i>-load.ping) and the worker's captures: the full br-ran capture of the idle
runs (raw/worker/<attempt>/br-ran.pcap[.gz], for the foreign traffic) and the
ICMP-only captures at the five points of the core for every run
(raw/worker/<attempt>/points/<point>.pcap.gz). For every echo request seen on
br-ran on its way to the server and its reply on the way back, core = reply
time − request time, both on the worker's clock; access = the UE's ping RTT −
core for the same sequence number. The points split core into its parts, all
on the worker's clock, so no clock sync is needed.
Writes summary.json, summary.md and rtt-samples.csv (every sample, for a CDF).
Owner: experiments/README.md.
"""
from __future__ import annotations

import collections
import glob
import json
import os
import re
import struct
import sys

EXP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, EXP)
from lib.stats import summary  # noqa: E402
from network.pcap import (POINTS, addr as _addr, attempt_of, captures, gtpu_inner, ip_packet,  # noqa: E402,F401
                          point_captures, read_pcap)

REPLY_RE = re.compile(r"icmp_seq=(\d+) .*time=([\d.]+) ms")
SENT_RE = re.compile(r"^(\d+) packets transmitted")
RUN_RE = re.compile(r"^(\d+)-(.+)\.ping$")


def parse_ping(text: str) -> tuple[dict[int, float], int]:
    """seq -> RTT ms for every reply, and the number of requests sent."""
    rtts, sent = {}, 0
    for line in text.splitlines():
        m = REPLY_RE.search(line)
        if m:
            rtts.setdefault(int(m.group(1)), float(m.group(2)))
        m = SENT_RE.match(line.strip())
        if m:
            sent = int(m.group(1))
    return rtts, sent


def split(pcap_path: str, target: str) -> dict:
    """Echo requests to `target` and their replies seen on br-ran, per ICMP id.
    The UE address is the most frequent source of those requests."""
    reqs, reps, sources, other = {}, {}, collections.Counter(), []
    for t, frame, link in read_pcap(pcap_path):
        inner = gtpu_inner(frame, link)
        if not inner:
            continue
        hl = (inner[0] & 0x0F) * 4
        src, dst, proto = _addr(inner[12:16]), _addr(inner[16:20]), inner[9]
        if proto == 1 and len(inner) >= hl + 8:
            kind = inner[hl]
            ident, seq = struct.unpack("!HH", inner[hl + 4:hl + 8])
            if kind == 8 and dst == target:
                reqs.setdefault((src, ident, seq), t)
                sources[src] += 1
                continue
            if kind == 0 and src == target:
                reps.setdefault((dst, ident, seq), t)
                continue
        other.append((t, src, dst, struct.unpack("!H", inner[2:4])[0]))
    if not sources:
        return {"ue": None, "flows": {}, "foreign_packets": 0, "foreign": []}
    ue = sources.most_common(1)[0][0]
    flows: dict[int, dict] = {}
    for (src, ident, seq), t in sorted(reqs.items(), key=lambda kv: kv[1]):
        if src != ue:
            continue
        f = flows.setdefault(ident, {"first": t, "core_ms": {}, "unpaired": 0, "requests": 0})
        f["requests"] += 1
        r = reps.get((src, ident, seq))
        if r is None:
            f["unpaired"] += 1
        else:
            f["core_ms"][seq] = (r - t) * 1000
    # Foreign: to or from the UE, and not the experiment's own traffic with the
    # target (the echo flow above, the iperf3 load of the loaded runs).
    foreign = [(t, n) for t, s, d, n in other if ue in (s, d) and target not in (s, d)]
    return {"ue": ue, "flows": flows, "foreign_packets": len(foreign), "foreign": foreign}


def _merge(caps: list[dict]) -> dict | None:
    """One view of the captures of every attempt: flows side by side (a list,
    ICMP ids may repeat across attempts), foreign packets (time, IP length) together."""
    caps = [c for c in caps if c["ue"]]
    if not caps:
        return None
    return {"ue": caps[0]["ue"], "flows": {i: f for i, f in enumerate(f for c in caps for f in c["flows"].values())},
            "foreign_packets": sum(c["foreign_packets"] for c in caps),
            "foreign": [x for c in caps for x in c["foreign"]]}


# The parts of the core, between consecutive capture points: the request's
# time from one point to the next plus the reply's time back; the server's own
# turnaround at the last point.
SEGMENTS = (("worker", "br-ran", "br-n3"), ("ovs_n3", "br-n3", "upf-n3"), ("upf", "upf-n3", "upf-n6m"),
            ("ovs_n6m", "upf-n6m", "server"))
SEGMENT_NAMES = tuple(name for name, _, _ in SEGMENTS) + ("server",)


def point_times(pcap_path: str, target: str) -> dict[tuple, list]:
    """(UE, ICMP id, seq) -> [request time, reply time] of the echoes with
    `target` at one capture point, GTP-U or plain."""
    out: dict[tuple, list] = {}
    for t, frame, link in read_pcap(pcap_path):
        ip = ip_packet(frame, link)
        if not ip or ip[9] != 1:
            continue
        hl = (ip[0] & 0x0F) * 4
        if len(ip) < hl + 8:
            continue
        kind = ip[hl]
        ident, seq = struct.unpack("!HH", ip[hl + 4:hl + 8])
        src, dst = _addr(ip[12:16]), _addr(ip[16:20])
        if kind == 8 and dst == target:
            e = out.setdefault((src, ident, seq), [None, None])
            e[0] = e[0] or t
        elif kind == 0 and src == target:
            e = out.setdefault((dst, ident, seq), [None, None])
            e[1] = e[1] or t
    return out


def segments(at: dict[str, list]) -> dict[str, float] | None:
    """ms per part of the core for one echo, from point -> [request, reply];
    None when a point missed the request or the reply."""
    if any(not at.get(p) or None in at[p] for p in POINTS):
        return None
    # 4 decimals of a ms (0.1 us): the captures are in ns, and an epoch in a
    # float resolves about 0.24 us; OVS steps are a few us.
    out = {name: round(((at[b][0] - at[a][0]) + (at[a][1] - at[b][1])) * 1000, 4) for name, a, b in SEGMENTS}
    out["server"] = round((at[POINTS[-1]][1] - at[POINTS[-1]][0]) * 1000, 4)
    return out


def summarize(run_dir: str) -> dict:
    with open(os.path.join(run_dir, "ue.json")) as fh:
        ue = json.load(fh)
    target = ue["target"]
    # Foreign traffic from the full br-ran captures (the idle runs).
    full = {attempt_of(p): split(p, target) for p in captures(run_dir)}
    cap = _merge(list(full.values()))
    # Echo flows per attempt, from the ICMP-only br-ran capture when there is
    # one (every run), else from the full one (idle runs only); the other points
    # per attempt, for the parts of the core.
    points = point_captures(run_dir)
    flows: dict[str, dict] = {}
    times: dict[str, dict[str, dict]] = {}
    for attempt in set(full) | set(points):
        src = points.get(attempt, {}).get("br-ran")
        f = split(src, target) if src else full.get(attempt)
        flows[attempt] = f["flows"] if f and f["ue"] else {}
        times[attempt] = {p: point_times(path, target) for p, path in points.get(attempt, {}).items()}
    ue_addr = cap["ue"] if cap else next((split(pp["br-ran"], target)["ue"] for pp in points.values() if "br-ran" in pp), None)
    runs = {}
    for path in sorted(glob.glob(os.path.join(run_dir, "raw", "ue", "*", "runs", "*.ping"))):
        m = RUN_RE.match(os.path.basename(path))
        # A run cut before it wrote its .meta did not finish (discarded by the
        # runner; redone by a later attempt when the run was resumed).
        if m and os.path.exists(path[:-len(".ping")] + ".meta"):
            runs[int(m.group(1))] = (int(m.group(1)), m.group(2), path)
    runs = [runs[k] for k in sorted(runs)]
    # A run's echo flow is the one (ICMP id) whose first request falls in the
    # run's window (UE clock, NTP; worker clock: 2 s of slack). The job's own
    # one-packet ping check and the other runs' flows fall outside it.
    def window(meta_path: str) -> tuple[float, float]:
        with open(meta_path) as fh:
            m = dict(kv.split("=", 1) for kv in fh.read().split())
        return float(m["start"]), float(m["end"])

    def flow_of(attempt: str, start: float, end: float):
        inside = [(i, f) for i, f in flows.get(attempt, {}).items() if start - 2 <= f["first"] <= end]
        return max(inside, key=lambda x: x[1]["requests"]) if inside else None

    out: dict = {"ue_address": ue_addr,
                 "foreign_packets": cap["foreign_packets"] if cap else None,
                 "capture_matches_runs": None, "conditions": {}, "samples": []}
    matched_all, any_capture = True, bool(flows and any(flows.values()))
    acc = collections.defaultdict(lambda: {"runs": [], "total": [], "core": [], "access": [],
                                           "segments": collections.defaultdict(list), "paired": 0, "unpaired": 0})
    for idx, cond, path in runs:
        attempt = attempt_of(path)
        with open(path) as fh:
            rtts, sent = parse_ping(fh.read())
        c = acc[cond]
        c["runs"].append({"index": idx, "transmitted": sent, "received": len(rtts),
                          "loss": round(1 - len(rtts) / sent, 4) if sent else None})
        start, end = window(path[:-len(".ping")] + ".meta")
        if cond == "idle" and cap:
            # Foreign traffic inside the run's own window (the capture also
            # covers the launch, before the job starts). Its bytes are what can
            # queue ahead of a ping.
            inside = [n for t, n in cap["foreign"] if start <= t <= end]
            c["runs"][-1].update(foreign_packets=len(inside), foreign_bytes=sum(inside),
                                 foreign_bytes_per_s=round(sum(inside) / (end - start), 1) if end > start else None)
        core, parts = {}, {}
        if flows.get(attempt):
            hit = flow_of(attempt, start, end)
            if hit is None:
                matched_all = False
            else:
                ident, f = hit
                core = f["core_ms"]
                c["paired"] += len(core)
                c["unpaired"] += f["unpaired"]
                for seq in core:
                    at = {p: times[attempt].get(p, {}).get((ue_addr, ident, seq)) for p in POINTS}
                    sg = segments(at)
                    if sg:
                        parts[seq] = sg
        for seq, total in sorted(rtts.items()):
            c["total"].append(total)
            row = {"condition": cond, "run": idx, "seq": seq, "total_ms": total, "core_ms": None, "access_ms": None}
            row.update({name: None for name in SEGMENT_NAMES})
            if seq in core:
                c["core"].append(round(core[seq], 4))
                c["access"].append(round(total - core[seq], 3))
                row.update(core_ms=round(core[seq], 4), access_ms=round(total - core[seq], 3))
            if seq in parts:
                for name, v in parts[seq].items():
                    c["segments"][name].append(v)
                row.update(parts[seq])
            out["samples"].append(row)
    if any_capture:
        out["capture_matches_runs"] = matched_all
    for cond, c in acc.items():
        d = {"runs": c["runs"], "total": summary(c["total"])}
        if c["core"]:
            d["core"] = summary(c["core"], 4)
            d["access"] = summary(c["access"])
            d["pairing"] = {"paired": c["paired"], "unpaired": c["unpaired"]}
        if c["segments"]:
            d["segments"] = {name: summary(c["segments"][name], 4) for name in SEGMENT_NAMES if c["segments"][name]}
        out["conditions"][cond] = d
    return out


def write(run_dir: str, s: dict) -> None:
    with open(os.path.join(run_dir, "summary.json"), "w") as fh:
        json.dump({k: v for k, v in s.items() if k != "samples"}, fh, indent=1)
    with open(os.path.join(run_dir, "rtt-samples.csv"), "w") as fh:
        cols = ("condition", "run", "seq", "total_ms", "core_ms", "access_ms") + SEGMENT_NAMES
        fh.write(",".join(cols) + "\n")
        for r in s["samples"]:
            fh.write(",".join("" if r.get(k) is None else str(r[k]) for k in cols) + "\n")
    in_runs = [f"{r.get('foreign_packets')} ({r.get('foreign_bytes_per_s')} B/s)"
               for r in s["conditions"].get("idle", {}).get("runs", [])]
    md = ["# rtt", "", f"UE address in the capture: {s['ue_address'] or '—'}; foreign packets to or from it: "
          f"{s['foreign_packets'] if s['foreign_packets'] is not None else '—'} in the whole capture, "
          f"{', '.join(str(x) for x in in_runs) or '—'} inside the idle runs", "",
          "| condition | part | n | min ms | mean | median | p90 | p99 | max | loss per run |", "|---|---|---|---|---|---|---|---|---|---|"]
    for cond, c in s["conditions"].items():
        loss = ", ".join(f"{r['loss']:.2%}" if r["loss"] is not None else "—" for r in c["runs"])
        for part in ("total", "core", "access"):
            if part in c:
                x = c[part]
                md.append(f"| {cond} | {part} | {x['n']} | {x['min']} | {x['mean']} | {x['median']} | {x['p90']} | {x['p99']} | {x['max']} "
                          f"| {loss if part == 'total' else ''} |")
    parts = [(cond, name, x) for cond, c in s["conditions"].items() for name, x in c.get("segments", {}).items()]
    if parts:
        md += ["", "Parts of the core (worker's clock):", "",
               "| condition | part | n | min ms | mean | median | p90 | p99 | max |", "|---|---|---|---|---|---|---|---|---|"]
        md += [f"| {cond} | {name} | {x['n']} | {x['min']} | {x['mean']} | {x['median']} | {x['p90']} | {x['p99']} | {x['max']} |"
               for cond, name, x in parts]
    with open(os.path.join(run_dir, "summary.md"), "w") as fh:
        fh.write("\n".join(md) + "\n")


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    s = summarize(sys.argv[1])
    write(sys.argv[1], s)
    print(open(os.path.join(sys.argv[1], "summary.md")).read())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
