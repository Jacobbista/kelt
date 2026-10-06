#!/usr/bin/env python3
"""The checks every run gets: what the summary may have lost without saying.

  check_run.py <run-dir>

One line per check, "ok" or "FAIL" with what is wrong; exit 1 when any fails.
A failed check flags the run, it does not remove it. Checks by campaign:

- throughput: every run of the schedule has a result or a failure reason;
  every counted run has its 1 s windows from the discard to the end; iperf3's
  bytes against the capture (throughput_bytes.py).
- rtt: every run of the schedule has a result; every echo paired on br-ran has
  all its parts; access is never negative.
- throughput and rtt: the other UEs' traffic on the cell inside each run, from
  the full br-ran capture (rtt: the idle runs), reported (part of the scenario).
- every campaign with a footprint: one sample per second from each machine and
  each pod inside the measured windows (no gap over 2 s); every pod named.

Owner: experiments/README.md.
"""
from __future__ import annotations

import collections
import csv
import glob
import json
import os
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "resource-use"))
import footprint  # noqa: E402
from network import throughput  # noqa: E402
from network.pcap import addr, captures, gtpu_inner, read_pcap  # noqa: E402

MAX_GAP_S = 2.0


def planned(run_dir: str) -> dict[int, str]:
    with open(os.path.join(run_dir, "schedule.txt")) as fh:
        return {int(f[0]): f[1] for f in (ln.split() for ln in fh) if f}


def check_schedule(run_dir: str) -> tuple[bool, str]:
    done = {int(os.path.basename(p).split("-")[0])
            for p in glob.glob(os.path.join(run_dir, "raw", "ue", "*", "runs", "*.meta"))}
    missing = sorted(set(planned(run_dir)) - done)
    return not missing, f"runs without a result: {missing}" if missing else "every planned run has a result"


def check_windows(run_dir: str) -> tuple[bool, str]:
    s = throughput.summarize(run_dir)
    short = []
    for name, c in s["combinations"].items():
        for r in c["runs"]:
            js = throughput._load(glob.glob(os.path.join(run_dir, "raw", "ue", "*", "runs", f"{r['index']}-{name}.json"))[-1])
            want = js.get("start", {}).get("test_start", {}).get("duration", 0) - s["discard_s"]
            have = len(throughput.windows(js, 1.0, s["discard_s"]))
            if have < want - 1:
                short.append(f"{name} run {r['index']}: {have} of {want:g}")
    failed = [f"{n} run {f['index']} ({f['reason']})" for n, c in s["combinations"].items() for f in c["failed"]]
    note = f"; failed with a reason: {', '.join(failed)}" if failed else ""
    return not short, (f"runs short of windows: {', '.join(short)}" if short else "every counted run has all its windows") + note


def check_bytes(run_dir: str) -> tuple[bool, str]:
    if not glob.glob(os.path.join(run_dir, "raw", "worker", "*", "br-ran.pcap*")):
        return True, "no capture (KELT_CAPTURE=0): bytes not checked"
    p = subprocess.run([sys.executable, os.path.join(HERE, "throughput_bytes.py"), run_dir], capture_output=True, text=True)
    bad = [ln for ln in p.stdout.splitlines() if ln.startswith("FAIL")]
    return p.returncode == 0, "; ".join(bad) if bad else "received <= seen on br-ran and >= 97% for every run"


def check_echoes(run_dir: str) -> tuple[bool, str]:
    with open(os.path.join(run_dir, "ue.json")) as fh:
        if str(json.load(fh).get("capture", "1")) == "0":
            return True, "no capture (KELT_CAPTURE=0): echoes not checked"
    with open(os.path.join(run_dir, "rtt-samples.csv")) as fh:
        rows = list(csv.DictReader(fh))
    if rows and "worker" not in rows[0]:
        return False, "no parts of the core: a run from before the five capture points"
    paired = [r for r in rows if r["core_ms"]]
    partial = [r for r in paired if not all(r[k] for k in ("worker", "ovs_n3", "upf", "ovs_n6m", "server"))]
    negative = [r for r in paired if float(r["access_ms"]) < 0]
    ok = not partial and not negative and paired
    return bool(ok), (f"{len(paired)} echoes paired, {len(partial)} without all parts, "
                      f"{len(negative)} with negative access")


# Other UEs on the cell: their traffic shares the radio with the measurement.
# Other devices stay attached, as in a real cell: their traffic is part of the
# scenario, so it is reported per run, not judged.


def check_cell(run_dir: str) -> tuple[bool, str]:
    caps = captures(run_dir)
    if not caps:
        return True, "no full br-ran capture: other UEs not counted"
    with open(os.path.join(run_dir, "ue.json")) as fh:
        target = json.load(fh)["target"]
    wins = []
    for meta in glob.glob(os.path.join(run_dir, "raw", "ue", "*", "runs", "*.meta")):
        m = throughput.read_meta(meta)
        wins.append((os.path.basename(meta)[:-5], m["start"], m["end"]))
    peers, other, ours = collections.Counter(), [], []
    for path in caps:
        for t, frame, link in read_pcap(path):
            ip = gtpu_inner(frame, link)
            if not ip:
                continue
            src, dst, n = addr(ip[12:16]), addr(ip[16:20]), struct.unpack("!H", ip[2:4])[0]
            if target in (src, dst):
                peers[dst if src == target else src] += n
                ours.append(t)
            else:
                other.append((t, src, dst, n))
    ue = peers.most_common(1)[0][0] if peers else None
    rates = []
    for run, a, b in sorted(wins, key=lambda w: w[1]):
        # a run the full capture did not cover (rtt: the load runs) has none
        # of the measurement's own packets in it
        if not any(a <= t <= b for t in ours):
            continue
        inside = sum(n for t, src, dst, n in other if a <= t <= b and ue not in (src, dst))
        rates.append((run, round(inside / (b - a), 1) if b > a else 0.0))
    if not rates:
        return True, "no run inside the full capture"
    values = [v for _, v in rates]
    return True, (f"other UEs {min(values)}-{max(values)} B/s per run "
                  f"(mean {sum(values) / len(values):.0f}), {len(rates)} run(s) covered")


def check_footprint(run_dir: str) -> tuple[bool, str]:
    path = os.path.join(run_dir, "footprint.json")
    if not os.path.exists(path):
        return False, "no footprint.json"
    with open(path) as fh:
        windows = [tuple(w) for c in json.load(fh).values() for w in c["windows"]]
    nodes, names, vms = footprint._load(run_dir)
    gaps, seen = [], set()

    def gap(label: str, times: list[float]) -> None:
        for a, b in windows:
            t = sorted(x for x in times if a - MAX_GAP_S <= x <= b + MAX_GAP_S)
            edges = [a] + t + [b] if t else [a, b]
            worst = max(y - x for x, y in zip(edges, edges[1:]))
            if worst > MAX_GAP_S:
                gaps.append(f"{label} {worst:.1f} s")
                return

    for node, (ns, pods) in nodes.items():
        gap(node, [s[0] for s in ns])
        for uid, s in pods.items():
            inside = [x[0] for x in s if any(a <= x[0] <= b for a, b in windows)]
            if inside:
                seen.add(uid)
                gap(f"{node} pod {names.get(uid, (None, uid))[1]}", [x[0] for x in s])
    for vm, s in vms.items():
        gap(f"vm-process {vm}", [x[0] for x in s])
    unnamed = sorted(uid for uid in seen if uid not in names)
    ok = not gaps and not unnamed
    msg = "one sample per second everywhere, every pod named" if ok else \
        "; ".join(filter(None, [f"gaps: {', '.join(gaps[:5])}" if gaps else "",
                                f"pods without a name: {len(unnamed)}" if unnamed else ""]))
    return ok, msg


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    run_dir = sys.argv[1].rstrip("/")
    campaign = os.path.basename(os.path.dirname(run_dir))
    checks = []
    if campaign == "throughput":
        checks = [("schedule", lambda: check_schedule(run_dir)), ("windows", lambda: check_windows(run_dir)),
                  ("bytes", lambda: check_bytes(run_dir)), ("cell", lambda: check_cell(run_dir))]
    elif campaign == "rtt":
        checks = [("schedule", lambda: check_schedule(run_dir)), ("echoes", lambda: check_echoes(run_dir)),
                  ("cell", lambda: check_cell(run_dir))]
    if glob.glob(os.path.join(run_dir, "raw", "footprint", "*")):
        checks.append(("footprint", lambda: check_footprint(run_dir)))
    if not checks:
        print(f"no checks for {campaign}")
        return 0
    fails = 0
    for name, fn in checks:
        ok, msg = fn()
        fails += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {name}: {msg}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
