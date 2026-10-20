#!/usr/bin/env python3
"""Bounds on the offset between the gateway's clock and this host's, from one
open-loop condition.

  clock_offset.py <condition dir>

A request sent at a (host clock) and answered at b reached the gateway at
t_receive and left it at t_emit (its hop line, the gateway's clock). The
offset (gateway minus host) lies in [t_emit - b, t_receive - a] for every
request, so in the intersection over all of them. Writes clock.json with the
bounds in ms and the number of requests used. The stream's position ages are
taken on this host's clock against timestamps set in the cluster, so they are
exact to within these bounds. Owner: experiments/exposure/README.md.
"""
from __future__ import annotations

import glob
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from lib.openloop import requests  # noqa: E402

GATEWAY = "camara-gateway"


def bounds(cond_dir: str) -> dict:
    sent = {r["x_correlator"]: r for r in requests(cond_dir) if r.get("x_correlator") and r.get("start_epoch")}
    lo, hi, n = float("-inf"), float("inf"), 0
    for path in glob.glob(os.path.join(cond_dir, "hoplog_*.txt")):
        with open(path) as fh:
            for ln in fh:
                i = ln.find("{")
                if i < 0 or GATEWAY not in ln:
                    continue
                try:
                    h = json.loads(ln[i:])
                except ValueError:
                    continue
                r = sent.get(h.get("correlator"))
                if h.get("service") != GATEWAY or r is None or not r.get("time_total_s"):
                    continue
                a = float(r["start_epoch"])
                b = a + float(r["time_total_s"])
                lo, hi, n = max(lo, h["t_emit"] - b), min(hi, h["t_receive"] - a), n + 1
    if not n:
        return {"requests": 0, "lower_ms": None, "upper_ms": None}
    return {"requests": n, "lower_ms": round(lo * 1000, 3), "upper_ms": round(hi * 1000, 3)}


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    b = bounds(sys.argv[1])
    with open(os.path.join(sys.argv[1], "clock.json"), "w") as fh:
        json.dump(b, fh, indent=2)
    print(json.dumps(b))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
