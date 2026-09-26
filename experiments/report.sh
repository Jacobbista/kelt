#!/usr/bin/env bash
# Every recorded run, one line each, oldest first: what the run holds, read from
# its summary. Pilot runs are marked; they never reach the thesis tables
# (tables.py). Runs without a summary are listed as "raw only".
#
#   experiments/report.sh                  all campaigns
#   experiments/report.sh response-time    one campaign
#
# Owner: experiments/README.md.

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

FILTER="${1:-}"
python3 - "$RUNS_DIR" "$FILTER" <<'PY'
import sys, os, json, glob
runs_dir, flt = sys.argv[1], sys.argv[2]
rows = []
for d in sorted(glob.glob(os.path.join(runs_dir, "*", "2*"))):
    campaign, stamp = d.split(os.sep)[-2:]
    if flt and campaign != flt:
        continue
    try:
        prov = json.load(open(os.path.join(d, "provenance.json")))
    except (OSError, ValueError):
        prov = {}
    tag = " [pilot]" if prov.get("pilot") else ""
    line = "raw only"
    if campaign == "resource-use" and os.path.exists(os.path.join(d, "resource_use.json")):
        ws = json.load(open(os.path.join(d, "resource_use.json")))["windows"]
        line = "; ".join(f"{w['label']}: {len(w['pods'])} pods" for w in ws)
    elif campaign == "verification" and os.path.exists(os.path.join(d, "summary.json")):
        c = json.load(open(os.path.join(d, "summary.json")))["counts"]
        line = ", ".join(f"{k} {v}" for k, v in sorted(c.items()))
    elif campaign == "response-time":
        parts = []
        for cond in sorted(os.listdir(d)):
            p = os.path.join(d, cond, "summary.json")
            if os.path.exists(p):
                s = json.load(open(p))
                parts.append(f"{cond}: e2e median {s['e2e_ms']['median']} ms, {s['with_vendor_span']}/{s['traces']} reached vendor")
        line = "; ".join(parts) or line
    rows.append((campaign, stamp, line + tag))
if not rows:
    print("no runs")
w = max((len(r[0]) for r in rows), default=8)
for c, st, line in rows:
    print(f"{c:<{w}}  {st}  {line}")
PY
