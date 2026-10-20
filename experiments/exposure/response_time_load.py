#!/usr/bin/env python3
"""Open-loop load for one response-time condition: requests at a fixed rate,
each on its own schedule, without waiting for the ones before.

  response_time_load.py --gateway URL --path P --bodies FILE --rate R --count N --run-dir D --condition C
                        [--warmup W] [--token-file F]

Request i starts at t0 + i/R on a worker thread, whatever the earlier requests
are doing, so a slow answer does not lower the rate the stack receives (a
driver that waits would, and would hide the queue it causes). The bodies (one
JSON per line) are sent in turn: with one body per asset and R equal to the
number of assets, each asset is asked once a second. Each request opens its own
connection. The token is read from --token-file, again whenever the file
changes (run.sh renews it during a run longer than its lifetime), or else
from KELT_CAMARA_TOKEN; it is never written.

Writes <run-dir>/requests_<condition>.csv (i, http_status, x_correlator,
time_total_s, time_starttransfer_s, start_epoch: when the request was sent,
late_s: how long after its scheduled time, error: a request with no HTTP answer
has status 0), and <run-dir>/load.json with the nominal rate, the count, the
first W requests that are warm-up, and the number of bodies. Opens and closes
the condition's window (lib/runmeta).
Owner: experiments/exposure/README.md.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import csv
import http.client
import json
import os
import sys
import threading
import time
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from lib import runmeta  # noqa: E402

FIELDS = ["i", "http_status", "x_correlator", "time_total_s", "time_starttransfer_s",
          "start_epoch", "late_s", "error"]


def schedule(count: int, rate: float) -> list[float]:
    """Offsets from t0 of each request, in seconds."""
    return [i / rate for i in range(count)]


class Token:
    """The bearer token, from a file read again when its modification time
    changes, or a fixed value."""

    def __init__(self, path: str | None, fixed: str):
        self.path, self.value, self.mtime = path, fixed, None
        self.lock = threading.Lock()

    def get(self) -> str:
        if not self.path:
            return self.value
        with self.lock:
            try:
                m = os.stat(self.path).st_mtime_ns
                if m != self.mtime:
                    with open(self.path) as fh:
                        self.value, self.mtime = fh.read().strip(), m
            except OSError:
                pass
            return self.value


def send(url: urllib.parse.SplitResult, path: str, body: bytes, token: str, timeout: float) -> dict:
    start_epoch, t0 = time.time(), time.perf_counter()
    conn = http.client.HTTPConnection(url.hostname, url.port or 80, timeout=timeout)
    try:
        conn.request("POST", path, body=body, headers={
            "Authorization": f"Bearer {token}", "Content-Type": "application/json", "Connection": "close"})
        resp = conn.getresponse()
        first = time.perf_counter() - t0
        resp.read()
        total = time.perf_counter() - t0
        return {"http_status": resp.status, "x_correlator": resp.getheader("x-correlator") or "",
                "time_total_s": f"{total:.6f}", "time_starttransfer_s": f"{first:.6f}",
                "start_epoch": f"{start_epoch:.6f}", "error": ""}
    except (OSError, http.client.HTTPException) as e:
        return {"http_status": 0, "x_correlator": "", "time_total_s": f"{time.perf_counter() - t0:.6f}",
                "time_starttransfer_s": "", "start_epoch": f"{start_epoch:.6f}", "error": type(e).__name__}
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gateway", required=True)
    ap.add_argument("--path", required=True)
    ap.add_argument("--bodies", required=True)
    ap.add_argument("--rate", type=float, required=True)
    ap.add_argument("--count", type=int, required=True)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--condition", required=True)
    ap.add_argument("--workers", type=int, default=256)
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--warmup", type=int, default=0)
    ap.add_argument("--token-file")
    a = ap.parse_args()
    token = Token(a.token_file, os.environ.get("KELT_CAMARA_TOKEN", ""))
    if not token.get():
        print("no token: --token-file is empty or KELT_CAMARA_TOKEN is not set", file=sys.stderr)
        return 2
    with open(a.bodies) as fh:
        bodies = [ln.strip().encode() for ln in fh if ln.strip()]
    url = urllib.parse.urlsplit(a.gateway)
    rows: list[dict] = [{} for _ in range(a.count)]
    lock = threading.Lock()

    def job(i: int, due: float) -> None:
        late = max(0.0, time.perf_counter() - due)
        r = send(url, a.path, bodies[i % len(bodies)], token.get(), a.timeout)
        with lock:
            rows[i] = {"i": i + 1, **r, "late_s": f"{late:.6f}"}

    runmeta.open_window(a.run_dir, a.condition)
    t0 = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=a.workers) as pool:
        for i, off in enumerate(schedule(a.count, a.rate)):
            wait = t0 + off - time.perf_counter()
            if wait > 0:
                time.sleep(wait)
            pool.submit(job, i, t0 + off)
    runmeta.close_window(a.run_dir, a.condition)
    with open(os.path.join(a.run_dir, f"requests_{a.condition}.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    with open(os.path.join(a.run_dir, "load.json"), "w") as fh:
        json.dump({"rate_per_s": a.rate, "count": a.count, "warmup": a.warmup, "bodies": len(bodies),
                   "workers": a.workers, "open_loop": True}, fh, indent=2)
    failed = sum(1 for r in rows if not r.get("http_status"))
    print(f"{a.count} requests at {a.rate:g}/s, {failed} without an HTTP answer", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
