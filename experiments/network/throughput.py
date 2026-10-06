#!/usr/bin/env python3
"""Summary of one throughput run (thesis 4.10.1.1).

  throughput.py <run-dir>

Reads what the UE job left in <run-dir>/raw/ue/<attempt>/ (iperf3 -J results
and their .meta lines), the worker's br-ran capture (raw/worker/<attempt>/)
and ue.json, and writes summary.json, summary.md and
series-<combination>.csv (the 0.1 s series of the run closest to the median,
for a figure). Median and p90 are over 1 s windows (see windows()). Owner: experiments/README.md.
"""
from __future__ import annotations

import bisect
import glob
import json
import os
import re
import statistics
import struct
import sys

EXP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, EXP)
from lib.stats import summary  # noqa: E402
from network.pcap import addr, captures, gtpu_inner, read_pcap  # noqa: E402

RUN_RE = re.compile(r"^(\d+)-(.+)\.json$")


SERVER_LINE_RE = re.compile(r"^\[\s*(SUM|\d+)\]\s+([\d.]+)-([\d.]+)\s+sec\s+\S+\s+\S+\s+([\d.]+)\s+([KMG]?)bits/sec\s*$")
UNIT = {"": 1e-6, "K": 1e-3, "M": 1.0, "G": 1e3}


def server_text_intervals(text: str) -> list[tuple[float, float, float]]:
    """(start s, end s, Mbit/s) per interval of the server's text report (the
    UPF's iperf3 server runs without -J): the [SUM] lines with several streams,
    the stream's own lines with one; the final totals (receiver/sender) left out.
    Each interval starts where the last ended; the text rounds both to 0.01 s,
    so a start one hundredth after the last end is that end."""
    rows = {"SUM": [], "one": []}
    for line in text.splitlines():
        m = SERVER_LINE_RE.match(line.rstrip())
        if m:
            rows["SUM" if m.group(1) == "SUM" else "one"].append(
                (float(m.group(2)), float(m.group(3)), float(m.group(4)) * UNIT[m.group(5)]))
    out = rows["SUM"] or rows["one"]
    for i in range(1, len(out)):
        start, end, v = out[i]
        if 0 < start - out[i - 1][1] <= 0.011:
            out[i] = (out[i - 1][1], end, v)
    return out


def intervals(iperf_json: dict) -> list[tuple[float, float, float]]:
    """(start s, end s, Mbit/s) per interval at the receiver. Uplink: the
    server's intervals (--get-server-output); the client's are only what it wrote."""
    reverse = iperf_json.get("start", {}).get("test_start", {}).get("reverse")
    if not reverse and "server_output_json" in iperf_json:
        iperf_json = iperf_json["server_output_json"]
    elif not reverse and "server_output_text" in iperf_json:
        return server_text_intervals(iperf_json["server_output_text"])
    return [(iv["sum"]["start"], iv["sum"]["end"], iv["sum"]["bits_per_second"] / 1e6)
            for iv in iperf_json.get("intervals", [])]


def samples(iperf_json: dict, discard_s: float = 5.0) -> list[float]:
    """Mbit/s per recorded interval (0.1 s), without the intervals that end
    within the first discard_s seconds (the start of the run). For the time series."""
    return [round(v, 6) for _, end, v in intervals(iperf_json) if end > discard_s + 1e-9]


def windows(iperf_json: dict, width: float = 1.0, discard_s: float = 5.0) -> list[float]:
    """Mbit/s over consecutive windows of `width` seconds after the discard,
    each the time-weighted mean of the intervals it covers; a window not fully
    covered because the run ended is left out; inside the run, the sub-ms gaps
    iperf3 leaves when its interval timer fires late are not missing data,
    so a window is the mean over the time its intervals cover. The statistics use these: a TCP
    flow delivers in bursts and stalls longer than 0.1 s (a retransmission
    timeout is at least 200 ms on Linux), so a 0.1 s sample says how bursty the
    flow is, a 1 s window what rate the application got."""
    ivs = [(max(a, discard_s), b, v) for a, b, v in intervals(iperf_json) if b > discard_s + 1e-9]
    last = max((b for _, b, _ in ivs), default=0.0)
    out, k = [], 0
    while True:
        lo, hi = discard_s + k * width, discard_s + (k + 1) * width
        if hi > last + 1e-6:
            return out
        parts = [(min(b, hi) - max(a, lo), v) for a, b, v in ivs if b > lo + 1e-9 and a < hi - 1e-9]
        covered = sum(t for t, _ in parts)
        out.append(round(sum(t * v for t, v in parts) / covered, 6))
        k += 1


def read_meta(path: str) -> dict:
    with open(path) as fh:
        fields = dict(kv.split("=", 1) for kv in fh.read().split())
    out = {"start": float(fields["start"]), "end": float(fields["end"]), "rc": int(fields["rc"])}
    for k in ("if_before", "if_after"):
        out[k] = [int(x) for x in fields[k].split(",")]
    return out


def counter_delta(before: int, after: int, bits: int = 32) -> int:
    """after - before for a counter that wraps at 2**bits (the Pi 3B+'s lan78xx
    exposes 32-bit byte counters). Valid while a run moves less than 4 GiB,
    572 Mbit/s for 60 s, far above this link."""
    return (after - before) % (2 ** bits)


def _load(path: str) -> dict:
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"error": "no readable result"}


def br_ran(run_dir: str, target: str, windows_: list[tuple[int, float, float]]) -> dict[int, dict]:
    """TCP segments between the UE and the target seen on br-ran, per run and
    direction: segments, payload bytes, and segments carrying data already seen
    on that flow (retransmitted). Downlink: a retransmission seen here means the
    first copy was lost after br-ran (radio side); uplink: after br-ran (inside
    the testbed). A loss before br-ran does not show here as a retransmission."""
    runs = sorted(windows_, key=lambda w: w[1])
    starts = [w[1] for w in runs]
    out = {idx: {d: {"segments": 0, "payload_bytes": 0, "retransmitted": 0} for d in ("dl", "ul")}
           for idx, _, _ in runs}
    top: dict[tuple, int] = {}
    for path in captures(run_dir):
        for t, frame, link in read_pcap(path):
            k = bisect.bisect_right(starts, t) - 1
            if k < 0 or t > runs[k][2]:
                continue
            inner = gtpu_inner(frame, link)
            if not inner or inner[9] != 6:
                continue
            hl = (inner[0] & 0x0F) * 4
            src, dst = addr(inner[12:16]), addr(inner[16:20])
            if target not in (src, dst) or len(inner) < hl + 13:
                continue
            total = struct.unpack("!H", inner[2:4])[0]
            sport, dport, seq = struct.unpack("!HHI", inner[hl:hl + 8])
            payload = total - hl - (inner[hl + 12] >> 4) * 4
            c = out[runs[k][0]]["dl" if src == target else "ul"]
            c["segments"] += 1
            c["payload_bytes"] += max(payload, 0)
            if payload > 0:
                flow = (src, sport, dst, dport)
                end = (seq + payload) % 2 ** 32
                # compared as TCP does (RFC 1982): the 32-bit sequence wraps,
                # about once per 4 GB from a random start
                ahead = (end - top[flow]) % 2 ** 32 if flow in top else 1
                if ahead == 0 or ahead >= 2 ** 31:
                    c["retransmitted"] += 1
                else:
                    top[flow] = end
    return out


# Left out at the start of every run: the uplink climbs to its level over 8-10 s
# in every run of the 2026-09-28 campaign (the gNB's scheduling, not TCP slow
# start, which takes well under a second at this RTT).
DISCARD_S = 10.0


def failure(js: dict, rc: int, discard_s: float) -> str | None:
    """Why a run has no usable result, or None. A downlink run (-R) whose every
    window is there counts even with an iperf3 error: the UE is the receiver and
    reports each interval itself, and the error comes after, when the two ends
    exchange results (the error is kept with the run). Uplink: the receiver's
    report is the server's, carried by that exchange, so an error there leaves
    no data."""
    error = js.get("error")
    if rc != 0:
        return f"exit code {rc}" + (f": {error}" if error else "")
    if not error:
        return None
    test = js.get("start", {}).get("test_start", {})
    whole = test.get("duration", 0) - discard_s - 1
    if test.get("reverse") and len(windows(js, 1.0, discard_s)) >= whole > 0:
        return None
    return error


def run_files(run_dir: str) -> dict[int, str]:
    """The iperf3 result of each run, by index. Every attempt of the run
    (raw/ue/<attempt>/runs) is searched; a run cut before it wrote its .meta
    did not finish and is left out (redone by a later attempt when the run
    was resumed)."""
    found: dict[int, str] = {}
    for path in sorted(glob.glob(os.path.join(run_dir, "raw", "ue", "*", "runs", "*.json"))):
        m = RUN_RE.match(os.path.basename(path))
        if m and not path.endswith(".load.json") and os.path.exists(path[:-len(".json")] + ".meta"):
            found[int(m.group(1))] = path
    return found


def summarize(run_dir: str, discard_s: float = DISCARD_S) -> dict:
    with open(os.path.join(run_dir, "ue.json")) as fh:
        ue = json.load(fh)
    found = run_files(run_dir)
    metas = {idx: read_meta(p[:-len(".json")] + ".meta") for idx, p in found.items()}
    seen = br_ran(run_dir, ue.get("target", ""), [(i, m["start"], m["end"]) for i, m in metas.items()]) \
        if captures(run_dir) else {}
    combos: dict[str, dict] = {}
    for idx in sorted(found):
        path, meta = found[idx], metas[idx]
        name = RUN_RE.match(os.path.basename(path)).group(2)
        js = _load(path)
        c = combos.setdefault(name, {"runs": [], "failed": [], "windows": []})
        reason = failure(js, meta["rc"], discard_s)
        if reason:
            c["failed"].append({"index": idx, "reason": reason})
            continue
        ws = windows(js, 1.0, discard_s)
        before, after = meta["if_before"], meta["if_after"]
        c["windows"] += ws
        c["runs"].append({
            "index": idx,
            "mean": round(statistics.fmean(ws), 3) if ws else None,
            "retransmits": js.get("end", {}).get("sum_sent", {}).get("retransmits"),
            "tool_bytes": js.get("end", {}).get("sum_received", {}).get("bytes"),
            "link_bytes": counter_delta(before[0], after[0]) + counter_delta(before[1], after[1]),
            "br_ran": seen.get(idx),
            "tool_error": js.get("error"),
            "series": samples(js, discard_s),
        })
    out = {"discard_s": discard_s, "window_s": 1.0, "combinations": {}}
    for name, c in combos.items():
        means = [r["mean"] for r in c["runs"] if r["mean"] is not None]
        med = statistics.median(means) if means else None
        rep = min((r for r in c["runs"] if r["mean"] is not None),
                  key=lambda r: abs(r["mean"] - med))["index"] if means else None
        out["combinations"][name] = {
            "n_runs": len(c["runs"]),
            "failed": c["failed"],
            "samples": summary(c["windows"]),
            "run_means": means,
            "spread": [min(means), max(means)] if means else None,
            "representative": rep,
            "sender_tcp_cc": ue.get("server_tcp_cc") if name.startswith("dl") else ue.get("ue_tcp_cc"),
            "runs": c["runs"],
            "windows": c["windows"],
        }
    return out


def write(run_dir: str, s: dict) -> None:
    slim = {**s, "combinations": {k: {**v, "runs": [{kk: vv for kk, vv in r.items() if kk != "series"} for r in v["runs"]]}
                                  for k, v in s["combinations"].items()}}
    with open(os.path.join(run_dir, "summary.json"), "w") as fh:
        json.dump(slim, fh, indent=1)
    md = [f"# throughput (first {s['discard_s']:g} s of each run discarded; statistics over {s['window_s']:g} s windows)", "",
          "| combination | runs | failed | median Mbit/s | p90 | run means (min–max) | sender TCP CC |",
          "|---|---|---|---|---|---|---|"]
    for name, c in s["combinations"].items():
        sp = f"{c['spread'][0]:.1f}–{c['spread'][1]:.1f}" if c["spread"] else "—"
        md.append(f"| {name} | {c['n_runs']} | {len(c['failed'])} | {c['samples']['median']} | {c['samples']['p90']} "
                  f"| {sp} | {c['sender_tcp_cc']} |")
        rep = next((r for r in c["runs"] if r["index"] == c["representative"]), None)
        if rep:
            with open(os.path.join(run_dir, f"series-{name}.csv"), "w") as fh:
                fh.write("interval,mbit_s\n" + "".join(f"{i},{v:.3f}\n" for i, v in enumerate(rep["series"])))
    notes = [f"- {name} run {f['index']} failed: {f['reason']}" for name, c in s["combinations"].items() for f in c["failed"]]
    notes += [f"- {name} run {r['index']} counted, iperf3 reported after measuring: {r['tool_error']}"
              for name, c in s["combinations"].items() for r in c["runs"] if r.get("tool_error")]
    if notes:
        md += [""] + notes
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
