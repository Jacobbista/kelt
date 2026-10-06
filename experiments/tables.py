#!/usr/bin/env python3
"""Build the thesis tables of one campaign from its runs.

  tables.py <resource-use|verification|response-time|throughput|rtt>

Uses every non-pilot run of the campaign, or only the runs listed in
runs/thesis-runs.txt when that file exists (one "<slug>/<stamp>" per
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
from lib.stats import summary  # noqa: E402


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


def _listed(runs_dir: str) -> set[str] | None:
    p = os.path.join(runs_dir, "thesis-runs.txt")
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


# The thesis footprint table: these rows, the conditions as columns.
PIVOT_ROWS = [("group", "core", "core"), ("group", "exposure", "exposure"), ("group", "identity", "identity"),
              ("group", "mec-server", "measurement server"), ("group", "edge-apps", "edge applications"),
              ("group", "platform", "platform"), ("group", "diagnostic", "diagnostic"), ("machine", "host", "host"),
              ("machine", "vm-process total", "VM processes"), ("machine", "vms inside total", "VMs, from inside")]
PIVOT_LEGEND = [
    "- edge applications: the applications deployed at the edge, the measurement server apart.",
    "- platform: the pods that run the testbed itself: Kubernetes and KubeEdge system pods, monitoring "
    "(Prometheus, Grafana, Loki and their agents), the dashboard's web pods, the front door, the image registry. "
    "k3s itself runs as a process, not a pod (it is in VMs, from inside); the dashboard backend runs on a third "
    "VM, which is not sampled from inside (it is in host and VM processes).",
    "- diagnostic: the probe pod used to check the network planes, idle during the runs.",
    "- host: everything running on the host machine, the testbed and anything else.",
    "- VM processes: the VirtualBox processes on the host. Their memory is what each VM has touched since it "
    "started: VirtualBox does not give pages back to the host, so it grows towards the RAM assigned to the VM "
    "when the guest fills its cache (the captures written on the worker do).",
    "- VMs, from inside: CPU busy and memory in use (total minus available) inside the master and worker VMs, "
    "the k3s control plane included; the guest's cache is not counted.",
]
PIVOT_COLS = [("resource-use", "idle", "rest"), ("throughput", "dl1", "dl1"), ("throughput", "dl4", "dl4"),
              ("throughput", "ul1", "ul1"), ("throughput", "ul4", "ul4"), ("rtt", "idle", "RTT rest"),
              ("rtt", "load", "RTT load")]


def footprint_pivot(rows: list[dict]) -> list[str]:
    """CPU (millicores) and memory (MiB) as "mean / peak" per row and condition.
    Several sessions: the mean of their means, the highest of their peaks."""
    md = []
    for what, mean_k, peak_k in (("CPU, millicores", "cpu_mean_m", "cpu_peak_m"),
                                 ("Memory, MiB", "mem_mean_mib", "mem_peak_mib")):
        md += ["", f"{what}: mean / 1 s peak", "", "| what | " + " | ".join(c[2] for c in PIVOT_COLS) + " |",
               "|" + "---|" * (len(PIVOT_COLS) + 1)]
        for level, name, label in PIVOT_ROWS:
            cells = []
            for campaign, cond, _ in PIVOT_COLS:
                hit = [r for r in rows if (r["campaign"], r["condition"], r["level"], r["name"]) == (campaign, cond, level, name)
                       and r[mean_k] is not None]
                cells.append(f"{sum(r[mean_k] for r in hit) / len(hit):.0f} / {max(r[peak_k] for r in hit):.0f}" if hit else "—")
            md.append(f"| {label} | " + " | ".join(cells) + " |")
    return md + [""] + PIVOT_LEGEND


def machine_note(runs: list[str]) -> str:
    """The CPUs and memory each machine reports (its own /proc), from the
    footprint samples: the size the numbers are measured against."""
    seen: dict[str, tuple] = {}
    for d in runs:
        for c in _load(os.path.join(d, "footprint.json")).values():
            for name, x in c["nodes"].items():
                if x.get("cpus"):
                    seen.setdefault(name, (x["cpus"], x["mem_total_mib"]))
    return "Machines, as each reports itself: " + "; ".join(
        f"{n} {cpus} CPUs, {mem:.0f} MiB" for n, (cpus, mem) in sorted(seen.items())) + "."


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
    """One row per session (run directory) and combination; with several
    sessions, one "all" row per combination from their 1 s windows together."""
    rows, pool = [], {}
    for d in runs:
        ue = _ue(d)
        for name, c in _load(os.path.join(d, "summary.json"))["combinations"].items():
            sp = c.get("spread") or [None, None]
            rows.append({"run": os.path.basename(d), "target": ue.get("target"), "combination": name,
                         "mode": ue.get("mode"), "runs": c["n_runs"],
                         "failed": len(c["failed"]), "mean_mbit_s": c["samples"].get("mean"),
                         "median_mbit_s": c["samples"].get("median"),
                         "p90_mbit_s": c["samples"].get("p90"), "run_mean_min": sp[0], "run_mean_max": sp[1],
                         "sender_tcp_cc": c.get("sender_tcp_cc")})
            p = pool.setdefault(name, {"row": rows[-1], "windows": [], "means": [], "runs": 0, "failed": 0})
            p["windows"] += c.get("windows", [])
            p["means"] += c.get("run_means", [])
            p["runs"] += c["n_runs"]
            p["failed"] += len(c["failed"])
    if len(runs) > 1:
        for name, p in pool.items():
            st = summary(p["windows"])
            rows.append({**p["row"], "run": "all", "runs": p["runs"], "failed": p["failed"],
                         "mean_mbit_s": st["mean"], "median_mbit_s": st["median"], "p90_mbit_s": st["p90"],
                         "run_mean_min": min(p["means"]) if p["means"] else None,
                         "run_mean_max": max(p["means"]) if p["means"] else None})
    return rows


# Foreign traffic an idle run may carry: one 1500 B packet takes 0.34 ms on the
# 35 Mbit/s uplink measured by throughput, and at 1000 B/s at most one arrives
# every 1.5 s, so it can reach fewer than 1 ping in 15, by less than 0.35 ms.
FOREIGN_LIMIT_B_S = 1000


def _idle_runs(d: str) -> list[dict]:
    return _load(os.path.join(d, "summary.json"))["conditions"].get("idle", {}).get("runs", [])


def _rtt_row(run: str, target, cond: str, part: str, x: dict, losses: list, packets: list, rate: list) -> dict:
    return {"run": run, "target": target, "condition": cond,
            "part": part, "n": x["n"], "min_ms": x.get("min"), "mean_ms": x.get("mean"),
            "median_ms": x["median"], "p90_ms": x["p90"], "p99_ms": x["p99"], "max_ms": x["max"],
            "max_loss": max(losses) if losses else None,
            # the capture covers the idle runs only: the load rows have no figure
            "max_foreign_packets_in_run": max(packets) if packets and cond == "idle" else None,
            "max_foreign_bytes_per_s": max(rate) if rate and cond == "idle" else None}


def rtt_rows(runs: list[str]) -> list[dict]:
    """One row per session (run directory), condition and part; with several
    sessions, "all" rows from their samples together (rtt-samples.csv)."""
    rows = []
    pool: dict = {}
    for d in runs:
        s = _load(os.path.join(d, "summary.json"))
        idle = _idle_runs(d)
        packets = [r["foreign_packets"] for r in idle if r.get("foreign_packets") is not None]
        rate = [r["foreign_bytes_per_s"] for r in idle if r.get("foreign_bytes_per_s") is not None]
        for cond, c in s["conditions"].items():
            losses = [r["loss"] for r in c["runs"] if r.get("loss") is not None]
            parts = [(p, c[p]) for p in ("total", "core", "access") if p in c] + \
                    [(f"core: {name}", x) for name, x in c.get("segments", {}).items()]
            for part, x in parts:
                rows.append(_rtt_row(os.path.basename(d), _ue(d).get("target"), cond, part, x, losses, packets, rate))
            p = pool.setdefault(cond, {"losses": [], "packets": [], "rate": [], "values": {}})
            p["losses"] += losses
            p["packets"] += packets
            p["rate"] += rate
        try:
            with open(os.path.join(d, "rtt-samples.csv")) as fh:
                for r in csv.DictReader(fh):
                    vals = pool.setdefault(r["condition"], {"losses": [], "packets": [], "rate": [], "values": {}})["values"]
                    for col, part in RTT_SAMPLE_PARTS:
                        if r.get(col):
                            vals.setdefault(part, []).append(float(r[col]))
        except OSError:
            pass
    if len(runs) > 1:
        target = _ue(runs[0]).get("target")
        for cond, p in pool.items():
            for col, part in RTT_SAMPLE_PARTS:
                if p["values"].get(part):
                    digits = 3 if part in ("total", "access") else 4
                    rows.append(_rtt_row("all", target, cond, part, summary(p["values"][part], digits),
                                         p["losses"], p["packets"], p["rate"]))
    return rows


# rtt-samples.csv column -> table part, in table order
RTT_SAMPLE_PARTS = [("total_ms", "total"), ("core_ms", "core"), ("access_ms", "access")] + \
    [(n, f"core: {n}") for n in ("worker", "ovs_n3", "upf", "ovs_n6m", "server")]


def rtt_notes(runs: list[str]) -> list[str]:
    over = [f"{os.path.basename(d)} run {r.get('index')} ({r['foreign_bytes_per_s']} B/s)"
            for d in runs for r in _idle_runs(d) if (r.get("foreign_bytes_per_s") or 0) > FOREIGN_LIMIT_B_S]
    return [f"Idle runs over the foreign traffic limit ({FOREIGN_LIMIT_B_S} B/s): "
            + ("; ".join(over) if over else "none") + "."]


def check_note(runs: list[str]) -> str:
    """The runs whose checks (validation/check_run.py, checks.txt) did not all
    pass, or were never run: they stay in the table, named here."""
    flagged = []
    for d in runs:
        try:
            with open(os.path.join(d, "checks.txt")) as fh:
                bad = [ln[5:].strip() for ln in fh if ln.startswith("FAIL")]
        except OSError:
            bad = ["not checked"]
        flagged += [f"{os.path.basename(d)} ({'; '.join(bad)})"] if bad else []
    return "Runs with a failed check: " + ("; ".join(flagged) if flagged else "none") + "."


def cell_note(runs: list[str]) -> list[str]:
    """The other UEs' traffic on the cell per session, as check_run.py reported it."""
    out = []
    for d in runs:
        try:
            with open(os.path.join(d, "checks.txt")) as fh:
                out += [f"Other UEs on the cell, {os.path.basename(d)}: {ln.split(':', 1)[1].strip()}."
                        for ln in fh if ln[5:].startswith("cell:")]
        except OSError:
            pass
    return out


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
    runs = [d for c in campaigns for d in select_runs(runs_dir, c, _listed(runs_dir))]
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
    md += [check_note(runs)] + cell_note(runs)
    if slug == "resource-use":
        md += footprint_pivot(rows) + ["", machine_note(runs), "", "Every level and pod:"]
    md += ["", "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    md += ["| " + " | ".join(str(r[c]) for c in cols) + " |" for r in rows]
    with open(os.path.join(out, f"{slug}.md"), "w") as fh:
        fh.write("\n".join(md) + "\n")
    print(f"{slug}: {len(runs)} run(s), {len(rows)} row(s), {len(gone)} discarded -> {out}/{slug}.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
