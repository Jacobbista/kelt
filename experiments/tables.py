#!/usr/bin/env python3
"""Build the thesis tables of one campaign from its runs.

  tables.py <resource-use|verification|response-time>

Uses every non-pilot run of the campaign, or only the runs listed in
experiments/thesis-runs.txt when that file exists (one "<slug>/<stamp>" per
line, "#" comments). Writes runs/_tables/<slug>.md and <slug>.csv (runs/ is
not committed). The thesis numbers come only from here. Owner: experiments/README.md.
"""
from __future__ import annotations

import csv
import json
import os
import sys

EXP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, EXP)
from lib.runmeta import discarded  # noqa: E402


def select_runs(runs_dir: str, slug: str, listed: set[str] | None) -> list[str]:
    base = os.path.join(runs_dir, slug)
    out = []
    for stamp in sorted(os.listdir(base)) if os.path.isdir(base) else []:
        d = os.path.join(base, stamp)
        if listed is not None and f"{slug}/{stamp}" not in listed:
            continue
        try:
            with open(os.path.join(d, "provenance.json")) as fh:
                prov = json.load(fh)
        except (OSError, ValueError):
            continue
        if prov.get("pilot"):
            continue
        out.append(d)
    return out


def _listed() -> set[str] | None:
    p = os.path.join(EXP, "thesis-runs.txt")
    if not os.path.exists(p):
        return None
    with open(p) as fh:
        return {ln.split("#")[0].strip() for ln in fh if ln.split("#")[0].strip()}


def _load(path: str):
    with open(path) as fh:
        return json.load(fh)


def resource_use_rows(runs: list[str]) -> list[dict]:
    rows = []
    for d in runs:
        for w in _load(os.path.join(d, "resource_use.json"))["windows"]:
            for p in w["pods"]:
                rows.append({"run": os.path.basename(d), "window": w["label"], "group": p["group"], "pod": p["pod"],
                             "cpu_mean_m": p["cpu_mcores"]["mean"], "cpu_peak_m": p["cpu_mcores"]["max"],
                             "mem_mean_mib": p["mem_mib"]["mean"], "mem_peak_mib": p["mem_mib"]["max"],
                             "samples": p["cpu_mcores"]["n"]})
    return rows


def verification_rows(runs: list[str]) -> list[dict]:
    rows = []
    for d in runs:
        with open(os.path.join(d, "cases.jsonl")) as fh:
            for ln in fh:
                c = json.loads(ln)
                rows.append({"run": os.path.basename(d), "group": c["group"], "case": c["case"],
                             "expected": json.dumps(c["expected"]), "actual": json.dumps(c["actual"]),
                             "verdict": c["verdict"], "note": c.get("note", "")})
    return rows


def response_time_rows(runs: list[str]) -> list[dict]:
    rows = []
    for d in runs:
        for cond in sorted(os.listdir(d)):
            p = os.path.join(d, cond, "summary.json")
            if not os.path.exists(p):
                continue
            s = _load(p)
            base = {"run": os.path.basename(d), "condition": cond, "rate_per_s": s.get("achieved_rate_per_s")}
            series = [("end to end", s["e2e_ms"]), ("stack (reached vendor)", s["stack_ms"]),
                      ("vendor call (adapter span)", s["vendor_ms"])]
            series += [(f"{k} own", v) for k, v in s["per_component"].items()]
            for name, q in series:
                rows.append({**base, "series": name, "n": q["n"], "median_ms": q["median"],
                             "p90_ms": q["p90"], "p99_ms": q["p99"]})
    return rows


BUILDERS = {"resource-use": resource_use_rows, "verification": verification_rows,
            "response-time": response_time_rows}


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in BUILDERS:
        print(__doc__, file=sys.stderr)
        return 2
    slug = sys.argv[1]
    runs_dir = os.environ.get("KELT_EXP_RUNS_DIR", os.path.join(EXP, "runs"))
    runs = select_runs(runs_dir, slug, _listed())
    if not runs:
        print(f"no non-pilot runs of {slug}", file=sys.stderr)
        return 1
    rows = BUILDERS[slug](runs)
    out = os.path.join(runs_dir, "_tables")
    os.makedirs(out, exist_ok=True)
    cols = list(rows[0].keys()) if rows else []
    with open(os.path.join(out, f"{slug}.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    gone = [f"{os.path.basename(d)}: {x['what']} ({x['reason']})" for d in runs for x in discarded(d)]
    md = [f"# {slug}", "", f"Runs: {len(runs)} ({', '.join(os.path.basename(d) for d in runs)}).",
          f"Discarded and repeated: {len(gone)}" + (": " + "; ".join(gone) if gone else "."), "",
          "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    md += ["| " + " | ".join(str(r[c]) for c in cols) + " |" for r in rows]
    with open(os.path.join(out, f"{slug}.md"), "w") as fh:
        fh.write("\n".join(md) + "\n")
    print(f"{slug}: {len(runs)} run(s), {len(rows)} row(s), {len(gone)} discarded -> {out}/{slug}.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
