"""The load an open-loop condition actually sent (exposure/response_time_load.py):
its load.json and requests_<condition>.csv. One reading for the check and the
summary.

Owner: experiments/exposure/README.md.
"""
from __future__ import annotations

import csv
import json
import os

from lib.stats import percentile


def requests(cond_dir: str) -> list[dict]:
    path = next((os.path.join(cond_dir, f) for f in sorted(os.listdir(cond_dir)) if f.startswith("requests_")), None)
    if not path:
        return []
    with open(path) as fh:
        return list(csv.DictReader(fh))


def load_stats(cond_dir: str) -> dict | None:
    """Planned and reached rate, p99 lateness and counts of an open-loop
    condition; None when it was not sent open loop. The rate reached is over
    every request sent, warm-up included; the warm-up count is in load.json."""
    path = os.path.join(cond_dir, "load.json")
    if not os.path.exists(path):
        return None
    with open(path) as fh:
        load = json.load(fh)
    reqs = requests(cond_dir)
    starts = sorted(float(r["start_epoch"]) for r in reqs if r.get("start_epoch"))
    late = [float(r["late_s"]) for r in reqs if r.get("late_s")]
    span = starts[-1] - starts[0] if len(starts) > 1 else 0.0
    return {"planned_per_s": float(load["rate_per_s"]), "warmup": int(load.get("warmup", 0)),
            "sent": len(starts), "sent_per_s": round((len(starts) - 1) / span, 3) if span > 0 else None,
            "late_p99_ms": round(percentile(late, 0.99) * 1000, 3) if late else None}


MIN_RATE_SHARE = 0.98


def load_problems(cond: str, ls: dict) -> list[str]:
    """What keeps an open-loop condition from counting as sent at its rate: below
    98% of the nominal rate, or the 99th percentile of lateness over one period."""
    if ls["sent"] < 2 or ls["sent_per_s"] is None:
        return [f"{cond}: fewer than two requests sent"]
    out, nominal = [], ls["planned_per_s"]
    if ls["sent_per_s"] < MIN_RATE_SHARE * nominal:
        out.append(f"{cond}: sent {ls['sent_per_s']:.1f}/s of {nominal:g}/s")
    if ls["late_p99_ms"] > 1000 / nominal:
        out.append(f"{cond}: 99% of requests within {ls['late_p99_ms']:.0f} ms of schedule, over one period")
    return out

