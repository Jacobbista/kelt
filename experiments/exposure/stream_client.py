#!/usr/bin/env python3
"""Clients on the gateway's live position stream (`/positions/stream`) for one
response-time condition: what each receives and how old each position is when
it arrives.

  stream_client.py --gateway URL --token-file F --clients N --warmup W --duration D
                   --run-dir DIR --condition C

N clients connect at once (a WebSocket each, standard library only), receive
for W s of warm-up, then for D s inside the condition's window (lib/runmeta),
and disconnect. The token goes in the Sec-WebSocket-Protocol header as the
pair `bearer.jwt`, `<jwt>`, as the gateway expects; it is never written. The
gateway opens one upstream connection to the engine per client and forwards
each broadcast as a JSON list of positions.

Writes, inside the window only:
- messages.csv: client, recv_epoch, bytes, positions (one row per message);
- ages.csv: client, source, age_ms (one row per position: the time of receipt,
  on this host's clock, minus the position's `timestamp`, the time of its fix);
- first_message.json: the first message client 0 received in the window;
- stream.json: per client, the messages and positions per second and the
  median interval between messages; the ages per source (median, p90, p99,
  max over all clients); the clients that closed early and why.
Owner: experiments/exposure/README.md.
"""
from __future__ import annotations

import argparse
import base64
import csv
import datetime
import json
import os
import socket
import struct
import sys
import threading
import time
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from lib import runmeta  # noqa: E402
from lib.stats import percentile, summary  # noqa: E402


def handshake(url: urllib.parse.SplitResult, token: str, timeout: float) -> socket.socket:
    s = socket.create_connection((url.hostname, url.port or 80), timeout=timeout)
    key = base64.b64encode(os.urandom(16)).decode()
    s.sendall((f"GET /positions/stream HTTP/1.1\r\nHost: {url.netloc}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
               f"Sec-WebSocket-Protocol: bearer.jwt, {token}\r\n\r\n").encode())
    head = b""
    while b"\r\n\r\n" not in head:
        chunk = s.recv(1)
        if not chunk:
            raise ConnectionError("closed during the handshake")
        head += chunk
    status = head.split(b"\r\n", 1)[0].decode()
    if " 101 " not in status + " ":
        raise ConnectionError(status)
    return s


def recv_exact(s: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("connection closed")
        buf += chunk
    return buf


def send_frame(s: socket.socket, opcode: int, payload: bytes) -> None:
    """A client frame is always masked (RFC 6455 5.3); payloads here are small."""
    mask = os.urandom(4)
    s.sendall(bytes([0x80 | opcode, 0x80 | len(payload)]) + mask
              + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))


def messages(s: socket.socket):
    """Yields (opcode, payload, receipt time) of each complete message; answers
    pings; ends on a close frame, yielding it."""
    parts, first_op = [], None
    while True:
        b1, b2 = recv_exact(s, 2)
        n = b2 & 0x7F
        if n == 126:
            n = struct.unpack(">H", recv_exact(s, 2))[0]
        elif n == 127:
            n = struct.unpack(">Q", recv_exact(s, 8))[0]
        data = recv_exact(s, n)
        op = b1 & 0x0F
        if op == 9:
            send_frame(s, 10, data)
            continue
        if op == 8:
            yield 8, data, time.time()
            return
        if op in (1, 2):
            first_op, parts = op, [data]
        elif op == 0:
            parts.append(data)
        if b1 & 0x80 and first_op is not None:
            yield first_op, b"".join(parts), time.time()
            parts, first_op = [], None


def fix_epoch(ts: str) -> float | None:
    try:
        return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return None


class Client(threading.Thread):
    def __init__(self, n: int, url, token: str, window: list, stop: threading.Event):
        super().__init__(daemon=True)
        self.n, self.url, self.token, self.window, self.stop = n, url, token, window, stop
        self.msgs: list[tuple[float, int, int]] = []
        self.ages: list[tuple[str, float]] = []
        self.first: bytes | None = None
        self.closed: str | None = None

    def run(self) -> None:
        try:
            s = handshake(self.url, self.token, 10.0)
            s.settimeout(5.0)
            for op, data, t in messages(s):
                if self.stop.is_set():
                    break
                if op == 8:
                    code = struct.unpack(">H", data[:2])[0] if len(data) >= 2 else None
                    self.closed = f"closed by the gateway: {code} {data[2:].decode(errors='replace')}"
                    return
                a, b = self.window
                if a is None or t < a or (b is not None and t > b):
                    continue
                if self.first is None:
                    self.first = data
                try:
                    items = json.loads(data)
                except ValueError:
                    items = []
                self.msgs.append((t, len(data), len(items) if isinstance(items, list) else 0))
                for it in items if isinstance(items, list) else []:
                    f = fix_epoch(it.get("timestamp"))
                    if f is not None:
                        self.ages.append((str(it.get("source")), (t - f) * 1000))
            try:
                send_frame(s, 8, struct.pack(">H", 1000))
            except OSError:
                pass
            s.close()
        except (OSError, ConnectionError) as e:
            if not self.stop.is_set():
                self.closed = f"{type(e).__name__}: {e}"


def per_client(c: Client, secs: float) -> dict:
    ts = [m[0] for m in c.msgs]
    gaps = [b - a for a, b in zip(ts, ts[1:])]
    return {"client": c.n, "messages": len(c.msgs), "messages_per_s": round(len(c.msgs) / secs, 3),
            "positions_per_s": round(sum(m[2] for m in c.msgs) / secs, 3),
            "interval_median_ms": round(percentile(gaps, 0.5) * 1000, 1) if gaps else None,
            "interval_max_ms": round(max(gaps) * 1000, 1) if gaps else None,
            "closed_early": c.closed}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gateway", required=True)
    ap.add_argument("--token-file", required=True)
    ap.add_argument("--clients", type=int, required=True)
    ap.add_argument("--warmup", type=float, default=10.0)
    ap.add_argument("--duration", type=float, required=True)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--condition", required=True)
    a = ap.parse_args()
    with open(a.token_file) as fh:
        token = fh.read().strip()
    url = urllib.parse.urlsplit(a.gateway)
    window: list = [None, None]
    stop = threading.Event()
    clients = [Client(i, url, token, window, stop) for i in range(a.clients)]
    for c in clients:
        c.start()
    time.sleep(a.warmup)
    runmeta.open_window(a.run_dir, a.condition)
    window[0] = time.time()
    time.sleep(a.duration)
    window[1] = time.time()
    runmeta.close_window(a.run_dir, a.condition)
    stop.set()
    for c in clients:
        c.join(timeout=10)
    secs = window[1] - window[0]
    with open(os.path.join(a.run_dir, "messages.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["client", "recv_epoch", "bytes", "positions"])
        w.writerows([c.n, f"{t:.6f}", b, p] for c in clients for t, b, p in c.msgs)
    with open(os.path.join(a.run_dir, "ages.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["client", "source", "age_ms"])
        w.writerows([c.n, src, f"{age:.3f}"] for c in clients for src, age in c.ages)
    if clients and clients[0].first is not None:
        with open(os.path.join(a.run_dir, "first_message.json"), "wb") as fh:
            fh.write(clients[0].first)
    sources = sorted({src for c in clients for src, _ in c.ages})
    out = {"clients": a.clients, "window_s": round(secs, 3), "warmup_s": a.warmup,
           "per_client": [per_client(c, secs) for c in clients],
           "age_ms": {src: summary([age for c in clients for s, age in c.ages if s == src]) for src in sources}}
    with open(os.path.join(a.run_dir, "stream.json"), "w") as fh:
        json.dump(out, fh, indent=2)
    early = [c.n for c in clients if c.closed]
    print(f"{a.clients} stream client(s) for {secs:.0f} s, {len(early)} closed early", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
