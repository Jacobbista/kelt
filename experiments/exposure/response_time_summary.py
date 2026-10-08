#!/usr/bin/env python3
"""Per-condition summary of a response-time run from hop_aggregate's stages.csv.

There is no hop line for the adapter's own call to the vendor service: the
adapter span contains it. The adapter also keeps the vendor's answer for
`cacheTtl` s, and maxAge=0 does not bypass that cache (checked 2026-09-26), so a
request reached the vendor only when its adapter span is at least MISS_MS (the
spans are bimodal: ~0.3 ms from the cache, ~150 ms with the cloud call).
Stack share = e2e - adapter span, per request, on those requests only; the
count is always printed next to it. Owner: experiments/README.md.
"""
from __future__ import annotations

import csv
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from lib.stats import summary  # noqa: E402

MISS_MS = 10.0


def summarise(rows: list[dict], vendor_col: str | None) -> dict:
    e2e = [float(r["e2e_ms"]) for r in rows]
    reached = [r for r in rows if vendor_col and float(r.get(vendor_col) or 0) >= MISS_MS]
    comps = sorted({k[:-len("_self_ms")] for r in rows for k in r if k.endswith("_self_ms")})
    return {
        "traces": len(rows),
        "with_vendor_span": len(reached),
        "miss_threshold_ms": MISS_MS,
        "e2e_ms": summary(e2e),
        "vendor_ms": summary([float(r[vendor_col]) for r in reached]) if vendor_col else summary([]),
        "stack_ms": summary([float(r["e2e_ms"]) - float(r[vendor_col]) for r in reached]),
        "per_component": {c: summary([float(r[f"{c}_self_ms"]) for r in rows if r.get(f"{c}_self_ms")])
                          for c in comps},
    }


def served_by(row: dict, vendor_col: str | None) -> str:
    """Where a request was answered, from its hop lines (northbound 0.19): a gateway
    cache hit leaves only the gateway's line (no engine span); an adapter cache hit
    leaves the vendor-adapter's line without the vendor time: its `cache` field
    says `hit` (0.19.2+), or, on an older line, its span is below MISS_MS. A
    source with no vendor behind it is answered by its adapter."""
    engine = next((float(v or 0) for k, v in row.items() if "engine" in k and k.endswith("_span_ms")), 0.0)
    if engine <= 0:
        return "gateway cache"
    if not vendor_col:
        return "adapter"  # a source with no vendor behind it (synthetic, wifi)
    # vendor-adapter 0.19.2+ states it on its hop line; the span decides before that.
    cache = row.get(vendor_col[: -len("_span_ms")] + "_cache")
    if cache in ("hit", "miss"):
        return "adapter cache" if cache == "hit" else "vendor"
    span = float(row.get(vendor_col) or 0)
    if span <= 0:
        return "unknown"  # the vendor-adapter wrote no hop line for this trace
    if span < MISS_MS:
        return "adapter cache"
    return "vendor"


def by_status(rows: list[dict], status_of: dict[str, str], vendor_col: str | None) -> dict:
    """summarise() per HTTP status of the request, with the served_by counts. A 422
    under maxAge=0 still crosses the stack and writes hop lines; it is reported
    apart from the 200s, never mixed in."""
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(status_of.get(r.get("correlator", ""), "unknown"), []).append(r)
    out = {}
    for st, rs in sorted(groups.items()):
        out[st] = summarise(rs, vendor_col)
        counts: dict[str, int] = {}
        for r in rs:
            k = served_by(r, vendor_col)
            counts[k] = counts.get(k, 0) + 1
        out[st]["served_by"] = counts
    return out


def achieved_rate(requests: int, window: dict | None) -> float | None:
    """Requests per second over the measured window (the driver sleeps after each
    request, so this is below the nominal rate)."""
    if not window or not window.get("end_utc"):
        return None
    t = lambda k: datetime.datetime.strptime(window[k], "%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    secs = (t("end_utc") - t("start_utc")).total_seconds()
    return round(requests / secs, 2) if secs > 0 else None


def main() -> int:
    run_dir, vendor = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "vendor")
    with open(os.path.join(run_dir, "stages.csv")) as fh:
        rows = list(csv.DictReader(fh))
    col = next((c for c in (rows[0] if rows else {}) if vendor in c and c.endswith("_span_ms")), None)
    with open(next(os.path.join(run_dir, f) for f in os.listdir(run_dir) if f.startswith("requests_"))) as fh:
        reqs = list(csv.DictReader(fh))
    sent = len(reqs)
    status_of = {r.get("x_correlator") or r.get("correlator") or "": str(r.get("http_status") or "") for r in reqs}
    s = {"by_status": by_status(rows, status_of, col), "vendor_column": col}
    from lib.runmeta import windows
    ws = windows(run_dir)
    s["requests_sent"] = sent
    s["achieved_rate_per_s"] = achieved_rate(sent, ws[0] if ws else None)
    with open(os.path.join(run_dir, "summary.json"), "w") as fh:
        json.dump(s, fh, indent=2)
    print(json.dumps(s, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
