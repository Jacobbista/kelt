#!/usr/bin/env python3
"""Draw the thesis figures of the network measurements from their runs.

  .venv/bin/python figures.py

Uses the runs listed in runs/thesis-runs.txt (as tables.py does) and writes
runs/_figures/*.pdf (runs/ is not committed):

  throughput-series.pdf  Figure 4.7: per direction, 1 and 4 streams; for each
                         the run whose mean is closest to the median of the
                         run means of all sessions, in 1 s windows over the
                         whole run, the discarded start shaded
  rtt-parts.pdf          Figure 4.8: mean of each part of the core per
                         condition, stacked (means add up, medians do not);
                         the mean RTT under each bar
  rtt-distribution.pdf   fig:res:latency: distribution of the RTT, at rest and
                         under load, each on its own scale

Needs matplotlib: python3 -m venv .venv && .venv/bin/pip install matplotlib.
Owner: experiments/README.md.
"""
from __future__ import annotations

import csv
import json
import os
import statistics
import sys

EXP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, EXP)
from network.throughput import DISCARD_S, run_files, windows  # noqa: E402
from lib.stats import percentile  # noqa: E402
from tables import _listed, select_runs  # noqa: E402

STYLE = os.path.join(EXP, "thesis.mplstyle")
# The thesis palette (thesis.mplstyle), in its order.
BLUE, RED, GREEN, ORANGE, GREY = "#6c8ebf", "#b85450", "#82b366", "#d79b00", "#666666"
PART_COLORS = [BLUE, RED, GREEN, ORANGE, GREY]
WIDTH = 5.12  # in, the thesis text width (13 cm)
# Theoretical maximum of this cell (TS 38.306 clause 4.1.2 with the uplink and
# downlink symbol shares of Hakegard et al., computed in the thesis), Mbit/s.
CAPACITY = {"dl": 168.5, "ul": 55.5}
YMAX = {"dl": 180, "ul": 60}
DIRECTIONS = [("dl", "Downlink"), ("ul", "Uplink")]
STREAMS = [("1", "1 stream", BLUE), ("4", "4 streams", RED)]
CONDITIONS = [("idle", "At rest"), ("load", "Under uplink load")]
# Each part is the time between two capture points, out and back (rtt.py).
# In the order of the path; OVS = Open vSwitch (said in the caption).
CORE_PARTS = [("worker", "Worker routing, RAN to N3"), ("ovs_n3", "N3 bridge (OVS)"), ("upf", "UPF, N3 to N6m"),
              ("ovs_n6m", "N6m bridge (OVS)"), ("server", "Server reply")]


def representative(runs: list[tuple[str, int, float]]) -> tuple[str, int]:
    """(run dir, index) of the run whose mean is closest to the median of all
    the means given; on a tie, the first one."""
    med = statistics.median(m for _, _, m in runs)
    d, i, _ = min(runs, key=lambda r: abs(r[2] - med))
    return d, i


def part_means(rows: list[dict], parts) -> dict[str, dict[str, float]]:
    """Mean of each part per condition, over the rtt-samples.csv rows."""
    out: dict[str, dict[str, list[float]]] = {}
    for r in rows:
        c = out.setdefault(r["condition"], {p: [] for p in parts})
        for p in parts:
            c[p].append(float(r[p]))
    return {cond: {p: statistics.fmean(v) for p, v in c.items()} for cond, c in out.items()}


def _rtt_rows(runs: list[str]) -> list[dict]:
    rows = []
    for d in runs:
        with open(os.path.join(d, "rtt-samples.csv")) as fh:
            rows += list(csv.DictReader(fh))
    return rows


def _day(run_dir: str) -> str:
    s = os.path.basename(run_dir)
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def _style():
    """The thesis style (thesis.mplstyle), and IEEE practice on top: quantity
    (unit) on every axis, the explanation in the caption. The figures are drawn
    at the thesis text width, to be included without scaling. Stops if Arial is
    missing rather than draw with a fallback font."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    plt.style.use(STYLE)
    try:
        font_manager.findfont("Arial", fallback_to_default=False)
    except ValueError:
        sys.exit("figures.py: the thesis style needs the Arial font (Ubuntu: ttf-mscorefonts-installer), "
                 "then rm -rf ~/.cache/matplotlib")
    return plt


def throughput_series(runs: list[str], out: str) -> None:
    plt = _style()
    fig, axes = plt.subplots(1, 2, figsize=(WIDTH, 2.6), constrained_layout=True)
    for ax, (direction, title), tag in zip(axes, DIRECTIONS, "ab"):
        for streams, label, color in STREAMS:
            means = []
            for d in runs:
                with open(os.path.join(d, "summary.json")) as fh:
                    c = json.load(fh)["combinations"][direction + streams]
                means += [(d, r["index"], r["mean"]) for r in c["runs"] if r["mean"] is not None]
            d, idx = representative(means)
            with open(run_files(d)[idx]) as fh:
                ws = windows(json.load(fh), 1.0, 0.0)
            ax.stairs(ws, range(len(ws) + 1), color=color, baseline=None, label=label)
        ax.axvspan(0, DISCARD_S, color="0.92", zorder=0)
        cap = CAPACITY[direction]
        ax.axhline(cap, color=GREY, linestyle="--", linewidth=1)
        ax.text(59, cap - YMAX[direction] * 0.02, f"Theoretical max. {cap:g} Mbit/s", ha="right", va="top",
                fontsize=7, color=GREY)
        ax.set_title(f"({tag}) {title}")
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Throughput (Mbit/s)")
        ax.set_xlim(0, 60)
        ax.set_ylim(0, YMAX[direction])
    axes[1].legend(loc="lower right")
    fig.savefig(out)
    plt.close(fig)


def rtt_parts(rows: list[dict], out: str) -> None:
    plt = _style()
    means = part_means(rows, ["core_ms"] + [p for p, _ in CORE_PARTS])
    fig, ax = plt.subplots(figsize=(WIDTH, 2.6), constrained_layout=True)
    ax.grid(False, axis="y")
    ys = list(range(len(CONDITIONS)))[::-1]
    left = [0.0] * len(CONDITIONS)
    for k, (key, lab) in enumerate(CORE_PARTS):
        vals = [means[c][key] for c, _ in CONDITIONS]
        ax.barh(ys, vals, left=left, color=PART_COLORS[k], label=lab, height=0.55, zorder=2)
        left = [x + v for x, v in zip(left, vals)]
    for y, (c, _) in zip(ys, CONDITIONS):
        ax.text(left[ys.index(y)] + 0.004, y, f"{means[c]['core_ms']:.2f} ms", va="center", fontsize=8)
    ax.set_yticks(ys, [lab for _, lab in CONDITIONS])
    ax.set_xlabel("Mean core delay (ms)")
    ax.set_xlim(0, 0.4)
    fig.legend(loc="outside upper center", ncol=3)
    fig.savefig(out)
    plt.close(fig)


# At rest the RTT sits near 14 ms, under load near 120 ms: one linear axis
# for both flattens the first, so each condition has its own panel and scale.
DIST_XMAX = {"idle": 40, "load": 300}
PERCENTILES = [(0.5, "p50"), (0.9, "p90"), (0.99, "p99")]


def rtt_distribution(rows: list[dict], out: str) -> None:
    plt = _style()
    fig, axes = plt.subplots(1, 2, figsize=(WIDTH, 2.6), constrained_layout=True)
    for ax, (cond, lab), tag in zip(axes, CONDITIONS, "ab"):
        xs = sorted(float(r["total_ms"]) for r in rows if r["condition"] == cond)
        ax.step(xs, [(i + 1) / len(xs) for i in range(len(xs))], where="post", color=BLUE, zorder=3)
        # The percentiles the tables print (nearest rank, lib/stats.py).
        for q, name in PERCENTILES:
            v = percentile(xs, q)
            ax.plot([0, v, v], [q, q, 0], color="0.45", linestyle=":", linewidth=0.8, zorder=2)
            ax.text(v + DIST_XMAX[cond] * 0.015, q - 0.015, f"{name} {v:g} ms", va="top", fontsize=7)
        ax.set_title(f"({tag}) {lab}")
        ax.set_xlabel("Round-trip time (ms)")
        ax.set_xlim(0, DIST_XMAX[cond])
        ax.set_ylim(0, 1.02)
    axes[0].set_ylabel("Cumulative probability")
    fig.savefig(out)
    plt.close(fig)


def main() -> None:
    runs_dir = os.path.join(EXP, "runs")
    listed = _listed(runs_dir)
    out = os.path.join(runs_dir, "_figures")
    os.makedirs(out, exist_ok=True)
    throughput_series(select_runs(runs_dir, "throughput", listed), os.path.join(out, "throughput-series.pdf"))
    rows = _rtt_rows(select_runs(runs_dir, "rtt", listed))
    rtt_parts(rows, os.path.join(out, "rtt-parts.pdf"))
    rtt_distribution(rows, os.path.join(out, "rtt-distribution.pdf"))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
