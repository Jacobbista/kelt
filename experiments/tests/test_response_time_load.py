import csv
import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "exposure"))
import response_time_load  # noqa: E402

DRIVER = os.path.join(HERE, "..", "exposure", "response_time_load.py")


class Slow(http.server.BaseHTTPRequestHandler):
    """Answers every POST after 200 ms with a fresh x-correlator."""
    seen = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        Slow.seen.append((self.headers.get("Authorization"), json.loads(body)))
        n = len(Slow.seen)
        time.sleep(0.2)
        self.send_response(200)
        self.send_header("x-correlator", f"c-{n}")
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *a):
        pass


class ScheduleTest(unittest.TestCase):
    def test_offsets_at_the_rate(self):
        self.assertEqual(response_time_load.schedule(4, 50), [0.0, 0.02, 0.04, 0.06])


class OpenLoopTest(unittest.TestCase):
    def test_slow_answers_do_not_lower_the_rate(self):
        # 200 ms answers at 20/s: a driver that waits would send 5/s.
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Slow)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        d = tempfile.mkdtemp()
        bodies = os.path.join(d, "bodies.jsonl")
        with open(bodies, "w") as fh:
            fh.write('{"device":{"assetId":"a"}}\n{"device":{"assetId":"b"}}\n')
        Slow.seen = []
        p = subprocess.run([sys.executable, DRIVER, "--gateway", f"http://127.0.0.1:{srv.server_port}",
                            "--path", "/retrieve", "--bodies", bodies, "--rate", "20", "--count", "20",
                            "--run-dir", d, "--condition", "local50"],
                           env={**os.environ, "KELT_CAMARA_TOKEN": "t"}, capture_output=True, text=True)
        srv.shutdown()
        self.assertEqual(p.returncode, 0, p.stderr)
        with open(os.path.join(d, "requests_local50.csv")) as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual([r["http_status"] for r in rows], ["200"] * 20)
        starts = sorted(float(r["start_epoch"]) for r in rows)
        self.assertGreater(19 / (starts[-1] - starts[0]), 18.0)
        self.assertEqual(len({r["x_correlator"] for r in rows}), 20)
        self.assertEqual([b["device"]["assetId"] for _, b in Slow.seen[:2]].count("a"), 1)
        self.assertEqual(Slow.seen[0][0], "Bearer t")
        with open(os.path.join(d, "load.json")) as fh:
            self.assertEqual(json.load(fh)["rate_per_s"], 20.0)
        with open(os.path.join(d, "window.json")) as fh:
            self.assertEqual(json.load(fh)["windows"][0]["label"], "local50")


if __name__ == "__main__":
    unittest.main()
