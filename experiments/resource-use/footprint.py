#!/usr/bin/env python3
"""Footprint: CPU and memory per pod, per group, per VM and of the host, per
condition, from the 1 s samples of lib/footprint-sampler.sh.

  footprint.py <run-dir> --core RE --exposure RE --identity RE --apps RE
               [--probe NAME]... [--discard S] [--window LABEL START END]...

Samples: raw/footprint/<attempt>/<node>.txt[.gz] (node = master, worker, host)
and pods.tsv (uid, namespace, pod). Conditions: the given windows (epoch s), or
one per run from its .meta (raw/ue/<attempt>/runs/<i>-<condition>.meta), or,
for an rtt run, from its ping's first to last reply, the first --discard
seconds of each left out. A CPU value is the usage between two
consecutive samples over their time apart (about 1 s), so its maximum is the
1 s peak; memory is the working set, and for a machine the memory in use (total
minus available). A group is the sum of its pods on one 1 s grid per window
(the samplers of different machines are not aligned): at each grid point, each
pod's CPU over the interval holding it and its last memory sample.
Writes footprint.json and footprint.md. Owner: experiments/README.md.
"""
from __future__ import annotations

import argparse
import bisect
import collections
import glob
import gzip
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from lib.stats import summary  # noqa: E402

META_RE = re.compile(r"^\d+-(.+)\.meta$")


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
    if re.fullmatch(groups["apps"], namespace):
        return "mec-server" if pod.startswith(server_pod_prefix) else "edge-apps"
    # Everything else runs the testbed itself: Kubernetes and KubeEdge system
    # pods, monitoring, the dashboard, the front door, the image registry.
    return "platform"


def _open(path: str):
    return gzip.open(path, "rt") if path.endswith(".gz") else open(path)


def read_samples(path: str) -> tuple[list[tuple], dict[str, list[tuple]], dict[str, list[tuple]]]:
    """Node lines (t, cpus, total, idle, mem total kB, mem available kB), pod
    lines per uid (t, usage usec, working set bytes) and VM process lines per
    VM (t, cpu ticks, ticks per s, rss kB)."""
    node, pods, vms = [], collections.defaultdict(list), collections.defaultdict(list)
    with _open(path) as fh:
        for line in fh:
            f = line.split()
            if len(f) == 7 and f[0] == "N":
                node.append((float(f[1]), int(f[2]), int(f[3]), int(f[4]), int(f[5]), int(f[6])))
            elif len(f) == 5 and f[0] == "P":
                pods[f[2]].append((float(f[1]), int(f[3]), int(f[4])))
            elif len(f) == 6 and f[0] == "V":
                vms[f[2]].append((float(f[1]), int(f[3]), int(f[4]), int(f[5])))
    return node, pods, vms


def node_series(node: list[tuple]) -> tuple[list[tuple], list[tuple]]:
    """(t, millicores in use) between consecutive samples, (t, MiB in use) per sample."""
    cpu = [(b[0], round(b[1] * 1000 * (1 - (b[3] - a[3]) / (b[2] - a[2])), 3))
           for a, b in zip(node, node[1:]) if b[2] > a[2]]
    mem = [(s[0], round((s[4] - s[5]) / 1024, 3)) for s in node]
    return cpu, mem


def vm_series(samples: list[tuple]) -> tuple[list[tuple], list[tuple]]:
    """A VM process on the host: (t, millicores) between samples, (t, MiB resident)."""
    cpu = [(b[0], round((b[1] - a[1]) / b[2] / (b[0] - a[0]) * 1000, 3))
           for a, b in zip(samples, samples[1:]) if b[0] > a[0] and b[1] >= a[1]]
    mem = [(s[0], round(s[3] / 1024, 3)) for s in samples]
    return cpu, mem


def pod_series(samples: list[tuple]) -> tuple[list[tuple], list[tuple]]:
    """(t, millicores) between consecutive samples, (t, MiB working set) per sample."""
    cpu = [(b[0], round((b[1] - a[1]) / (b[0] - a[0]) / 1000, 3))
           for a, b in zip(samples, samples[1:]) if b[0] > a[0] and b[1] >= a[1]]
    mem = [(s[0], round(s[2] / 1048576, 3)) for s in samples]
    return cpu, mem


def _inside(series: list[tuple], windows: list[tuple]) -> list[tuple]:
    return [(t, v) for t, v in series if any(a <= t <= b for a, b in windows)]


PING_STAMP_RE = re.compile(r"^\[(\d+(?:\.\d+)?)\] .*icmp_seq=")


def _ping_stamps(path: str) -> list[float]:
    try:
        with open(path) as fh:
            return [float(m.group(1)) for m in map(PING_STAMP_RE.match, fh) if m]
    except OSError:
        return []


def condition_windows(run_dir: str, discard_s: float = 0.0) -> dict[str, list[tuple]]:
    """condition -> [(start, end)] from every run's .meta, after the discard."""
    out: dict[str, list[tuple]] = collections.defaultdict(list)
    for path in sorted(glob.glob(os.path.join(run_dir, "raw", "ue", "*", "runs", "*.meta"))):
        m = META_RE.match(os.path.basename(path))
        if not m:
            continue
        with open(path) as fh:
            f = dict(kv.split("=", 1) for kv in fh.read().split())
        start, end = float(f["start"]) + discard_s, float(f["end"])
        # An rtt run: the time its ping measured (ping -D stamps each reply),
        # not the load tool's lead and teardown around it.
        stamps = _ping_stamps(path[:-len(".meta")] + ".ping")
        if stamps:
            start, end = stamps[0] + discard_s, stamps[-1]
        out[m.group(1)].append((start, end))
    return {k: sorted(v) for k, v in out.items()}


def _load(run_dir: str):
    """node -> (node samples, pod samples), and uid -> (namespace, pod), over every attempt."""
    nodes: dict[str, tuple[list, dict]] = {}
    vms: dict[str, list] = collections.defaultdict(list)
    names: dict[str, tuple[str, str]] = {}
    for path in sorted(glob.glob(os.path.join(run_dir, "raw", "footprint", "*", "*.txt*"))):
        name = os.path.basename(path).split(".txt")[0]
        n, p, v = read_samples(path)
        have = nodes.setdefault(name, ([], collections.defaultdict(list)))
        have[0].extend(n)
        for uid, s in p.items():
            have[1][uid].extend(s)
        for vm, s in v.items():
            vms[vm].extend(s)
    for path in glob.glob(os.path.join(run_dir, "raw", "footprint", "*", "pods.tsv")):
        with open(path) as fh:
            for line in fh:
                f = line.rstrip("\n").split("\t")
                if len(f) == 3:
                    names[f[0]] = (f[1], f[2])
    return nodes, names, vms


def _steps(samples: list[tuple]) -> tuple[list, list, list, list, list]:
    """A pod as step functions: CPU interval starts, ends and millicores (the
    value holds over (start, end]), memory sample times and MiB."""
    starts, ends, cpu = [], [], []
    for a, b in zip(samples, samples[1:]):
        if b[0] > a[0] and b[1] >= a[1]:
            starts.append(a[0])
            ends.append(b[0])
            cpu.append((b[1] - a[1]) / (b[0] - a[0]) / 1000)
    return starts, ends, cpu, [x[0] for x in samples], [x[2] / 1048576 for x in samples]


def _group(pods: list[tuple], grid: list[float]) -> dict:
    """The sum of the pods at each grid point: each pod's CPU over the interval
    holding the point, its last memory sample (no older than 1.5 s)."""
    cpu, mem = [], []
    for g in grid:
        c = m = None
        for starts, ends, values, times, mib in pods:
            i = bisect.bisect_left(ends, g)
            if i < len(ends) and starts[i] < g:
                c = (c or 0.0) + values[i]
            j = bisect.bisect_right(times, g) - 1
            if j >= 0 and g - times[j] <= 1.5:
                m = (m or 0.0) + mib[j]
        if c is not None:
            cpu.append(round(c, 3))
        if m is not None:
            mem.append(round(m, 3))
    return {"cpu_mcores": summary(cpu), "mem_mib": summary(mem)}


def summarize(run_dir: str, windows: dict[str, list[tuple]], groups: dict,
              server_prefix: str = "measurement-server", probes: tuple = ()) -> dict:
    nodes, names, vms = _load(run_dir)
    series, steps = {}, {}
    for node, (ns, pods) in nodes.items():
        series[node] = (node_series(sorted(ns)), {uid: pod_series(sorted(s)) for uid, s in pods.items()})
        steps[node] = {uid: _steps(sorted(s)) for uid, s in pods.items()}
    out = {}
    for cond, win in windows.items():
        c = {"windows": [list(w) for w in win], "nodes": {}, "pods": [], "groups": {}}
        members = collections.defaultdict(list)
        # What each VM takes from the host, apart from anything else on it.
        for vm, samples in sorted(vms.items()):
            vcpu, vmem = vm_series(sorted(samples))
            c["nodes"][f"vm-process {vm}"] = {"cpu_mcores": summary([v for _, v in _inside(vcpu, win)]),
                                              "mem_mib": summary([v for _, v in _inside(vmem, win)])}
        grid = [a + 0.5 + k for a, b in win for k in range(int(b - a))]
        # All VM processes together, on the grid like a group: the sum of the
        # VMs' peaks would overstate the busiest second.
        if vms:
            c["nodes"]["vm-process total"] = _group(
                [_steps([(t, ticks / clk * 1e6, rss * 1024) for t, ticks, clk, rss in sorted(x)]) for x in vms.values()], grid)
        # Every VM from inside, together: CPU busy time and memory in use
        # (total minus available: the guest's cache is not counted). /proc/stat
        # counts in USER_HZ, 100 per second on Linux.
        inside = [_steps([(x[0], (x[2] - x[3]) * 1e4, (x[4] - x[5]) * 1024) for x in sorted(ns)])
                  for node, (ns, _) in nodes.items() if node != "host" and ns]
        if inside:
            c["nodes"]["vms inside total"] = _group(inside, grid)
        for node, ((ncpu, nmem), pods) in series.items():
            first = min(nodes[node][0]) if nodes[node][0] else None
            c["nodes"][node] = {"cpu_mcores": summary([v for _, v in _inside(ncpu, win)]),
                                "mem_mib": summary([v for _, v in _inside(nmem, win)]),
                                "cpus": first[1] if first else None,
                                "mem_total_mib": round(first[4] / 1024, 3) if first else None}
            for uid, (pcpu, pmem) in pods.items():
                cpu, mem = _inside(pcpu, win), _inside(pmem, win)
                if not cpu and not mem:
                    continue
                namespace, pod = names.get(uid, ("", uid))
                group = group_of(namespace, pod, groups, server_prefix, probes)
                c["pods"].append({"node": node, "namespace": namespace, "pod": pod, "group": group,
                                  "cpu_mcores": summary([v for _, v in cpu]), "mem_mib": summary([v for _, v in mem])})
                members[group].append(steps[node][uid])
        c["groups"] = {g: _group(pods, grid) for g, pods in sorted(members.items())}
        c["pods"].sort(key=lambda p: (p["group"], p["pod"]))
        out[cond] = c
    return out


def write(run_dir: str, s: dict) -> None:
    with open(os.path.join(run_dir, "footprint.json"), "w") as fh:
        json.dump(s, fh, indent=1)
    md = ["# footprint (1 s samples; CPU in millicores, memory in MiB)", "",
          "| condition | what | CPU mean | CPU peak | mem mean | mem peak | n |", "|---|---|---|---|---|---|---|"]
    for cond, c in s.items():
        rows = [(f"machine {n}", x) for n, x in sorted(c["nodes"].items())] + \
               [(f"group {g}", x) for g, x in c["groups"].items()]
        for what, x in rows:
            md.append(f"| {cond} | {what} | {x['cpu_mcores']['mean']} | {x['cpu_mcores']['max']} "
                      f"| {x['mem_mib']['mean']} | {x['mem_mib']['max']} | {x['cpu_mcores']['n']} |")
    with open(os.path.join(run_dir, "footprint.md"), "w") as fh:
        fh.write("\n".join(md) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--window", nargs=3, action="append", metavar=("LABEL", "START", "END"), default=[])
    ap.add_argument("--discard", type=float, default=0.0)
    ap.add_argument("--core", required=True)
    ap.add_argument("--exposure", required=True)
    ap.add_argument("--identity", required=True)
    ap.add_argument("--apps", required=True)
    ap.add_argument("--server-prefix", default="measurement-server")
    ap.add_argument("--probe", action="append", default=[], help="Deployment name of a diagnostic probe (repeatable)")
    a = ap.parse_args()
    groups = {"core": a.core, "exposure": a.exposure, "identity": a.identity, "apps": a.apps}
    windows: dict[str, list[tuple]] = collections.defaultdict(list)
    for label, start, end in a.window:
        windows[label].append((float(start), float(end)))
    windows = dict(windows) or condition_windows(a.run_dir, a.discard)
    s = summarize(a.run_dir, windows, groups, a.server_prefix, tuple(a.probe))
    write(a.run_dir, s)
    print(open(os.path.join(a.run_dir, "footprint.md")).read())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
