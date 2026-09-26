#!/usr/bin/env python3
"""Resource use: CPU and memory per pod over measured windows, from Prometheus.

The load windows are the ones the other campaigns recorded (window.json), so the
load is the one their tables report. Groups: core, exposure (northbound),
identity, the mec measurement server, diagnostic probes (by Deployment name,
wherever they live); anything else is "other" and listed apart.
Owner: experiments/README.md.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from lib.stats import summary  # noqa: E402

SEL = 'namespace=~"{ns}",container!="",container!="POD"'
CPU_Q = '1000 * sum by (namespace,pod) (rate(container_cpu_usage_seconds_total{%s}[1m]))'
MEM_Q = 'sum by (namespace,pod) (container_memory_working_set_bytes{%s}) / 1048576'


def group_of(namespace: str, pod: str, groups: dict, server_pod_prefix: str, probes: tuple = ()) -> str:
    # A probe sits wherever it needs to reach (netshoot is in the core namespace
    # to attach to every plane); it is diagnostic, not part of what it probes.
    if any(pod.startswith(p + "-") for p in probes):
        return "diagnostic"
    if re.fullmatch(groups["core"], namespace):
        return "core"
    if re.fullmatch(groups["exposure"], namespace):
        return "exposure"
    if re.fullmatch(groups["identity"], namespace):
        return "identity"
    if re.fullmatch(groups["apps"], namespace) and pod.startswith(server_pod_prefix):
        return "mec-server"
    return "other"


def _stats(values: list) -> dict:
    xs = [float(v) for _, v in values]
    s = summary(xs)
    s["mean"] = round(sum(xs) / len(xs), 3) if xs else None
    return s


def per_pod(cpu_json: dict, mem_json: dict) -> list[dict]:
    mem = {(r["metric"]["namespace"], r["metric"]["pod"]): r["values"] for r in mem_json["data"]["result"]}
    rows = []
    for r in cpu_json["data"]["result"]:
        key = (r["metric"]["namespace"], r["metric"]["pod"])
        rows.append({"namespace": key[0], "pod": key[1], "cpu_mcores": _stats(r["values"]),
                     "mem_mib": _stats(mem.get(key, []))})
    return rows


def collect(windows: list[dict]) -> tuple[list[dict], list[str]]:
    ok = [w for w in windows if w.get("end_utc")]
    return ok, [w["label"] for w in windows if not w.get("end_utc")]


def _query_range(prom: str, q: str, start: str, end: str) -> dict:
    qs = urllib.parse.urlencode({"query": q, "start": start, "end": end, "step": "15s"})
    with urllib.request.urlopen(f"{prom}/api/v1/query_range?{qs}", timeout=30) as resp:
        return json.load(resp)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prom", required=True)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--window", nargs=3, action="append", metavar=("LABEL", "START", "END"), default=[])
    ap.add_argument("--core", required=True)
    ap.add_argument("--exposure", required=True)
    ap.add_argument("--identity", required=True)
    ap.add_argument("--apps", required=True)
    ap.add_argument("--server-prefix", default="measurement-server")
    ap.add_argument("--probe", action="append", default=[], help="Deployment name of a diagnostic probe (repeatable)")
    a = ap.parse_args()
    groups = {"core": a.core, "exposure": a.exposure, "identity": a.identity, "apps": a.apps}
    ns_re = "|".join(groups.values())
    out = {"windows": []}
    for label, start, end in a.window:
        cpu = _query_range(a.prom, CPU_Q % SEL.format(ns=ns_re), start, end)
        mem = _query_range(a.prom, MEM_Q % SEL.format(ns=ns_re), start, end)
        for kind, doc in (("cpu", cpu), ("mem", mem)):
            with open(os.path.join(a.run_dir, "raw", f"prom_{label}_{kind}.json"), "w") as fh:
                json.dump(doc, fh)
        rows = per_pod(cpu, mem)
        for r in rows:
            r["group"] = group_of(r["namespace"], r["pod"], groups, a.server_prefix, tuple(a.probe))
        out["windows"].append({"label": label, "start_utc": start, "end_utc": end, "pods": rows})
    with open(os.path.join(a.run_dir, "resource_use.json"), "w") as fh:
        json.dump(out, fh, indent=2)
    md = ["# resource use", "", "| window | group | pod | CPU mean m | CPU peak m | mem mean MiB | mem peak MiB | n |",
          "|---|---|---|---|---|---|---|---|"]
    for w in out["windows"]:
        for r in sorted(w["pods"], key=lambda r: (r["group"], r["pod"])):
            c, m = r["cpu_mcores"], r["mem_mib"]
            md.append(f"| {w['label']} | {r['group']} | {r['pod']} | {c['mean']} | {c['max']} | {m['mean']} | {m['max']} | {c['n']} |")
    with open(os.path.join(a.run_dir, "summary.md"), "w") as fh:
        fh.write("\n".join(md) + "\n")
    print("\n".join(md))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
