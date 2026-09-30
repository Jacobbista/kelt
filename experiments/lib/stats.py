"""One percentile method for every table (nearest rank, the one hop_aggregate used).

Owner: experiments/README.md.
"""
from __future__ import annotations


def percentile(xs: list[float], q: float) -> float:
    if not xs:
        return float("nan")
    s = sorted(xs)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


def summary(xs: list[float], digits: int = 3) -> dict:
    """n, median, p90, p99 and max, rounded to `digits` (4 for the parts of the
    core, a few microseconds in ms)."""
    if not xs:
        return {"n": 0, "median": None, "p90": None, "p99": None, "max": None}

    def r(v: float) -> float:
        return round(float(v), digits)

    return {"n": len(xs), "median": r(percentile(xs, 0.5)), "p90": r(percentile(xs, 0.9)),
            "p99": r(percentile(xs, 0.99)), "max": r(max(xs))}
