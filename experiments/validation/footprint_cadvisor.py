#!/usr/bin/env python3
"""Validation F1: the footprint sampler against cAdvisor.

  footprint_cadvisor.py <run-dir> <prometheus-url> [min-millicores]

Both read the same counter, a pod's cgroup CPU usage: the sampler once a second,
cAdvisor at Prometheus's scrape. For every pod, two raw cAdvisor samples inside
the sampler's span give its mean CPU between their timestamps; the sampler's
counter is interpolated at the same two instants. Pods using at least
min-millicores (default 50) must agree within 10%. Owner: experiments/README.md.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "resource-use"))
import footprint  # noqa: E402


def _range_vector(prom: str, at: float, span_s: int) -> dict:
    # A range vector returns the raw samples with their scrape timestamps.
    q = f'container_cpu_usage_seconds_total{{container!="", container!="POD"}}[{span_s}s]'
    url = f"{prom}/api/v1/query?" + urllib.parse.urlencode({"query": q, "time": f"{at:.3f}"})
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.load(resp)


def _interp(samples: list[tuple], t: float) -> float | None:
    """Cumulative usage (usec) at t, linear between the samples around it."""
    for a, b in zip(samples, samples[1:]):
        if a[0] <= t <= b[0]:
            return a[1] + (b[1] - a[1]) * (t - a[0]) / (b[0] - a[0])
    return None


def main() -> int:
    run_dir, prom = sys.argv[1], sys.argv[2]
    floor = float(sys.argv[3]) if len(sys.argv) > 3 else 50.0
    nodes, names, _ = footprint._load(run_dir)
    by_pod = {}
    for node, (_, pods) in nodes.items():
        for uid, s in pods.items():
            ns, pod = names.get(uid, ("", uid))
            by_pod[(ns, pod)] = sorted((t, u) for t, u, _ in s)
    spans = [s for s in by_pod.values() if s]
    lo, hi = max(s[0][0] for s in spans), min(s[-1][0] for s in spans)
    doc = _range_vector(prom, hi, int(hi - lo))
    # cAdvisor: per (namespace, pod) the sum over containers, sample by sample
    # (one scrape stamps every container of a node at the same time).
    cad: dict[tuple, dict[float, float]] = {}
    for r in doc["data"]["result"]:
        key = (r["metric"].get("namespace"), r["metric"].get("pod"))
        for t, v in r["values"]:
            cad.setdefault(key, {}).setdefault(float(t), 0.0)
            cad[key][float(t)] += float(v)
    rows, fails = [], 0
    for key, series in sorted(cad.items()):
        if key not in by_pod:
            continue
        ts = sorted(t for t in series if lo <= t <= hi)
        if len(ts) < 2:
            continue
        t0, t1 = ts[0], ts[-1]
        c = (series[t1] - series[t0]) / (t1 - t0) * 1000
        a, b = _interp(by_pod[key], t0), _interp(by_pod[key], t1)
        if a is None or b is None:
            continue
        s = (b - a) / (t1 - t0) / 1000
        if max(c, s) < floor:
            continue
        diff = (s - c) / c * 100 if c else float("inf")
        ok = abs(diff) <= 10
        fails += not ok
        rows.append(f"{'ok  ' if ok else 'FAIL'} {key[0]}/{key[1]}: sampler {s:.1f} m, cAdvisor {c:.1f} m, "
                    f"{diff:+.1f}% over {t1 - t0:.0f} s")
    print("\n".join(rows) or f"no pod above {floor} m")
    return 1 if fails or not rows else 0


if __name__ == "__main__":
    raise SystemExit(main())
