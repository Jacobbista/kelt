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
- throughput and rtt: the QoS of the UE's session (network/ue_qos.py) recorded at
  the start and the end of every attempt, every value known, unchanged within
  the attempt; not applicable when the UE is behind a router (mode nat).
- response-time: every request with an HTTP answer has its trace (hop lines
  joined by x-correlator); every request answered; no trace with a missing hop
  (served by "unknown"); for maxAge=0 on the synthetic source (local,
  local50) every request answered 200, by the adapter. Open-loop conditions (load.json):
  sent at no less than 98% of the nominal rate, 99% of the requests sent
  within one period of their schedule. The rest condition: whether the WiFi
  adapter received scans inside its window (reported, not judged).
- every campaign with a footprint: one sample per second from each machine and
  each pod inside the measured windows it exists in (no gap over 2 s); every
  pod named.

Owner: experiments/README.md.
"""
from __future__ import annotations

import collections
import csv
import datetime
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
from lib.stats import percentile  # noqa: E402
from lib.runmeta import windows as run_windows  # noqa: E402

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


def covered(wins: list[tuple], first: float, last: float) -> list[tuple]:
    """The runs (name, start, end) the full capture spans from start to end,
    by start. rtt captures everything only around the runs at rest; a load run
    it touches for a moment is not covered (a rate over the whole run would
    count a few seconds of traffic)."""
    return sorted((w for w in wins if first <= w[1] and w[2] <= last), key=lambda w: w[1])


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
    peers, other, stamps = collections.Counter(), [], []
    for path in caps:
        for t, frame, link in read_pcap(path):
            stamps.append(t)
            ip = gtpu_inner(frame, link)
            if not ip:
                continue
            src, dst, n = addr(ip[12:16]), addr(ip[16:20]), struct.unpack("!H", ip[2:4])[0]
            if target in (src, dst):
                peers[dst if src == target else src] += n
            else:
                other.append((t, src, dst, n))
    ue = peers.most_common(1)[0][0] if peers else None
    rates = []
    for run, a, b in covered(wins, min(stamps, default=0.0), max(stamps, default=0.0)):
        inside = sum(n for t, src, dst, n in other if a <= t <= b and ue not in (src, dst))
        rates.append((run, round(inside / (b - a), 1) if b > a else 0.0))
    if not rates:
        return True, "no run inside the full capture"
    values = [v for _, v in rates]
    return True, (f"other UEs {min(values)}-{max(values)} B/s per run "
                  f"(mean {sum(values) / len(values):.0f}), {len(rates)} run(s) covered")


def check_qos(run_dir: str) -> tuple[bool, str]:
    try:
        with open(os.path.join(run_dir, "ue.json")) as fh:
            ue = json.load(fh)
    except (OSError, ValueError):
        return False, "no ue.json"
    if ue.get("mode") == "nat":
        return True, "not applicable: the UE is behind a router (mode nat)"
    problems, seen = [], []
    for n in range(1, int(ue.get("attempts", 1)) + 1):
        start, end = ue.get(f"attempt_{n}_qos_start"), ue.get(f"attempt_{n}_qos_end")
        if not start or not end:
            problems.append(f"attempt {n}: not recorded at {'start' if not start else 'end'}")
            continue
        unknown = sorted(k for k, v in start.items() if v == "unknown")
        if unknown:
            problems.append(f"attempt {n}: unknown {', '.join(unknown)}")
        if start != end:
            problems.append(f"attempt {n}: changed within the attempt")
        seen.append(start)
    if problems:
        return False, "; ".join(problems)
    q = seen[-1]
    return True, (f"{q['supi']} 5QI {q['five_qi']}, UE-AMBR {q['ue_ambr_dl_mbit']}/{q['ue_ambr_ul_mbit']}, "
                  f"Session-AMBR {q['session_ambr_dl_mbit']}/{q['session_ambr_ul_mbit']} Mbit/s (record)")


MIN_RATE_SHARE = 0.98
SYNTHETIC_CONDITIONS = ("local", "local50")


def _rt_conditions(run_dir: str) -> list[str]:
    return sorted(c for c in os.listdir(run_dir) if os.path.exists(os.path.join(run_dir, c, "summary.json")))


def _requests(cond_dir: str) -> list[dict]:
    path = next((os.path.join(cond_dir, f) for f in sorted(os.listdir(cond_dir)) if f.startswith("requests_")), None)
    if not path:
        return []
    with open(path) as fh:
        return list(csv.DictReader(fh))


def check_traces(run_dir: str) -> tuple[bool, str]:
    problems, parts = [], []
    for cond in _rt_conditions(run_dir):
        d = os.path.join(run_dir, cond)
        with open(os.path.join(d, "stages.json")) as fh:
            st = json.load(fh)
        with open(os.path.join(d, "summary.json")) as fh:
            sm = json.load(fh)
        reqs = _requests(d)
        unanswered = sum(1 for r in reqs if str(r.get("http_status", "0")) in ("0", "000", ""))
        missing = st.get("without_trace")
        served = {k: v for b in sm["by_status"].values() for k, v in b.get("served_by", {}).items()}
        if missing is None:
            problems.append(f"{cond}: traces not counted (aggregated before the count was kept)")
        elif missing:
            problems.append(f"{cond}: {missing} request(s) without a trace")
        if unanswered:
            problems.append(f"{cond}: {unanswered} request(s) without an HTTP answer")
        if served.get("unknown"):
            problems.append(f"{cond}: {served['unknown']} trace(s) with a missing hop")
        if cond in SYNTHETIC_CONDITIONS:
            not200 = sum(1 for r in reqs if str(r.get("http_status", "")) not in ("200", "0", "000", ""))
            if not200:
                problems.append(f"{cond}: {not200} request(s) answered other than 200")
            ok200 = sm["by_status"].get("200", {}).get("served_by", {})
            other = {k: v for k, v in ok200.items() if k != "adapter"}
            if other:
                problems.append(f"{cond}: 200s not answered by the adapter: {other}")
        parts.append(f"{cond} {st['traces']}")
    return not problems, "; ".join(problems) if problems else \
        "every request answered and traced, every hop present (traces after warm-up: " + ", ".join(parts) + ")"


def check_load(run_dir: str) -> tuple[bool, str]:
    problems, parts = [], []
    for cond in _rt_conditions(run_dir):
        d = os.path.join(run_dir, cond)
        if not os.path.exists(os.path.join(d, "load.json")):
            continue
        with open(os.path.join(d, "load.json")) as fh:
            nominal = float(json.load(fh)["rate_per_s"])
        starts = sorted(float(r["start_epoch"]) for r in _requests(d) if r.get("start_epoch"))
        late = sorted(float(r["late_s"]) for r in _requests(d) if r.get("late_s"))
        if len(starts) < 2:
            problems.append(f"{cond}: fewer than two requests sent")
            continue
        sent = (len(starts) - 1) / (starts[-1] - starts[0])
        p99 = percentile(late, 0.99)
        if sent < MIN_RATE_SHARE * nominal:
            problems.append(f"{cond}: sent {sent:.1f}/s of {nominal:g}/s")
        if p99 > 1 / nominal:
            problems.append(f"{cond}: 99% of requests within {p99 * 1000:.0f} ms of schedule, over one period")
        parts.append(f"{cond} {sent:.2f}/s of {nominal:g}/s, p99 lateness {p99 * 1000:.1f} ms")
    if not parts and not problems:
        return True, "no open-loop condition"
    return not problems, "; ".join(problems) if problems else "; ".join(parts)


def check_wifi(run_dir: str) -> tuple[bool, str]:
    d = os.path.join(run_dir, "rest")
    try:
        with open(os.path.join(d, "wifi-devices-end.json")) as fh:
            end = json.load(fh)
        w = next(x for x in run_windows(d) if x["label"] == "rest")
    except (OSError, ValueError, StopIteration):
        return True, "no rest condition"
    if end is None:
        return True, "WiFi adapter not reachable: scans not known"
    t = lambda k: datetime.datetime.strptime(w[k], "%Y-%m-%dT%H:%M:%SZ").replace(  # noqa: E731
        tzinfo=datetime.timezone.utc).timestamp()
    seen = [x["id"] for x in end if x.get("lastSeen") and t("start_utc") <= float(x["lastSeen"]) <= t("end_utc")]
    return True, (f"WiFi scans received at rest from {len(seen)} device(s)" if seen
                  else "no WiFi scan received at rest")


def check_footprint(run_dir: str) -> tuple[bool, str]:
    path = os.path.join(run_dir, "footprint.json")
    if not os.path.exists(path):
        return False, "no footprint.json"
    with open(path) as fh:
        windows = [tuple(w) for c in json.load(fh).values() for w in c["windows"]]
    nodes, names, vms = footprint._load(run_dir)
    gaps, seen = [], set()

    def gap(label: str, times: list[float], only_present: bool = False) -> None:
        # A pod is checked in the windows where it has samples: one replaced
        # between conditions did not exist in the others.
        for a, b in windows:
            if only_present and not any(a <= x <= b for x in times):
                continue
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
                gap(f"{node} pod {names.get(uid, (None, uid))[1]}", [x[0] for x in s], only_present=True)
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
                  ("bytes", lambda: check_bytes(run_dir)), ("cell", lambda: check_cell(run_dir)),
                  ("qos", lambda: check_qos(run_dir))]
    elif campaign == "rtt":
        checks = [("schedule", lambda: check_schedule(run_dir)), ("echoes", lambda: check_echoes(run_dir)),
                  ("cell", lambda: check_cell(run_dir)), ("qos", lambda: check_qos(run_dir))]
    elif campaign == "response-time":
        checks = [("traces", lambda: check_traces(run_dir)), ("load", lambda: check_load(run_dir)),
                  ("wifi", lambda: check_wifi(run_dir))]
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
