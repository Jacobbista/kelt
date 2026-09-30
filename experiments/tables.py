#!/usr/bin/env python3
"""Build the thesis tables of one campaign from its runs.

  tables.py <resource-use|verification|response-time|throughput|rtt>

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


# The footprint (thesis 4.10.2, 5.11.3) is recorded by every campaign that
# loads the testbed, and by resource-use for the testbed at rest.
FOOTPRINT_CAMPAIGNS = ("resource-use", "throughput", "rtt")


def footprint_rows(runs: list[str]) -> list[dict]:
    rows = []
    for d in runs:
        for cond, c in _load(os.path.join(d, "footprint.json")).items():
            items = [("machine", n, x) for n, x in sorted(c["nodes"].items())] + \
                    [("group", g, x) for g, x in c["groups"].items()] + \
                    [("pod", p["pod"], p) for p in c["pods"]]
            for level, name, x in items:
                rows.append({"run": os.path.basename(d), "campaign": os.path.basename(os.path.dirname(d)),
                             "condition": cond, "level": level, "name": name,
                             "cpu_mean_m": x["cpu_mcores"]["mean"], "cpu_peak_m": x["cpu_mcores"]["max"],
                             "mem_mean_mib": x["mem_mib"]["mean"], "mem_peak_mib": x["mem_mib"]["max"],
                             "samples": x["cpu_mcores"]["n"]})
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


def _ue(d: str) -> dict:
    try:
        return _load(os.path.join(d, "ue.json"))
    except (OSError, ValueError):
        return {}


def throughput_rows(runs: list[str]) -> list[dict]:
    rows = []
    for d in runs:
        ue = _ue(d)
        for name, c in _load(os.path.join(d, "summary.json"))["combinations"].items():
            sp = c.get("spread") or [None, None]
            rows.append({"run": os.path.basename(d), "target": ue.get("target"), "combination": name,
                         "mode": ue.get("mode"), "runs": c["n_runs"],
                         "failed": len(c["failed"]), "median_mbit_s": c["samples"]["median"],
                         "p90_mbit_s": c["samples"]["p90"], "run_mean_min": sp[0], "run_mean_max": sp[1],
                         "sender_tcp_cc": c.get("sender_tcp_cc")})
    return rows


# Foreign traffic an idle run may carry: one 1500 B packet takes 0.34 ms on the
# 35 Mbit/s uplink measured by throughput, and at 1000 B/s at most one arrives
# every 1.5 s, so it can reach fewer than 1 ping in 15, by less than 0.35 ms.
FOREIGN_LIMIT_B_S = 1000


def _idle_runs(d: str) -> list[dict]:
    return _load(os.path.join(d, "summary.json"))["conditions"].get("idle", {}).get("runs", [])


def rtt_rows(runs: list[str]) -> list[dict]:
    rows = []
    for d in runs:
        s = _load(os.path.join(d, "summary.json"))
        # Foreign traffic inside the measured windows: the capture covers the
        # idle runs only, so the load rows have no figure.
        idle = _idle_runs(d)
        packets = [r["foreign_packets"] for r in idle if r.get("foreign_packets") is not None]
        rate = [r["foreign_bytes_per_s"] for r in idle if r.get("foreign_bytes_per_s") is not None]
        for cond, c in s["conditions"].items():
            losses = [r["loss"] for r in c["runs"] if r.get("loss") is not None]
            parts = [(p, c[p]) for p in ("total", "core", "access") if p in c] + \
                    [(f"core: {name}", x) for name, x in c.get("segments", {}).items()]
            for part, x in parts:
                rows.append({"run": os.path.basename(d), "target": _ue(d).get("target"), "condition": cond,
                             "part": part, "n": x["n"],
                             "median_ms": x["median"], "p90_ms": x["p90"], "p99_ms": x["p99"], "max_ms": x["max"],
                             "max_loss": max(losses) if losses else None,
                             "max_foreign_packets_in_run": max(packets) if packets and cond == "idle" else None,
                             "max_foreign_bytes_per_s": max(rate) if rate and cond == "idle" else None})
    return rows


def rtt_notes(runs: list[str]) -> list[str]:
    over = [f"{os.path.basename(d)} run {r.get('index')} ({r['foreign_bytes_per_s']} B/s)"
            for d in runs for r in _idle_runs(d) if (r.get("foreign_bytes_per_s") or 0) > FOREIGN_LIMIT_B_S]
    return [f"Idle runs over the foreign traffic limit ({FOREIGN_LIMIT_B_S} B/s): "
            + ("; ".join(over) if over else "none") + "."]


NOTES = {"rtt": rtt_notes}

BUILDERS = {"resource-use": footprint_rows, "verification": verification_rows,
            "response-time": response_time_rows,
            "throughput": throughput_rows, "rtt": rtt_rows}


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in BUILDERS:
        print(__doc__, file=sys.stderr)
        return 2
    slug = sys.argv[1]
    runs_dir = os.environ.get("KELT_EXP_RUNS_DIR", os.path.join(EXP, "runs"))
    campaigns = FOOTPRINT_CAMPAIGNS if slug == "resource-use" else (slug,)
    runs = [d for c in campaigns for d in select_runs(runs_dir, c, _listed())]
    if slug == "resource-use":
        runs = [d for d in runs if os.path.exists(os.path.join(d, "footprint.json"))]
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
          f"Discarded and repeated: {len(gone)}" + (": " + "; ".join(gone) if gone else ".")]
    md += NOTES[slug](runs) if slug in NOTES else []
    md += ["", "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    md += ["| " + " | ".join(str(r[c]) for c in cols) + " |" for r in rows]
    with open(os.path.join(out, f"{slug}.md"), "w") as fh:
        fh.write("\n".join(md) + "\n")
    print(f"{slug}: {len(runs)} run(s), {len(rows)} row(s), {len(gone)} discarded -> {out}/{slug}.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
