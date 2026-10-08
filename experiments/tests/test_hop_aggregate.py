import csv
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
AGG = os.path.join(HERE, "..", "exposure", "hop_aggregate.py")


def hop(service, stage, t0, t1, **extra):
    return json.dumps({"event": "hop", "service": service, "stage": stage, "correlator": "c1", "status": 200,
                       "t_receive": t0, "t_emit": t1, "span_ms": round((t1 - t0) * 1000, 3), **extra})


class CacheColumnTest(unittest.TestCase):
    def test_the_vendor_adapter_cache_field_reaches_stages_csv(self):
        lines = [
            hop("camara-gateway", "POST /location-retrieval/v0.5/retrieve", 100.000, 100.400),
            hop("positioning-engine", "GET /position/{id}", 100.010, 100.390),
            hop("vendor-adapter", "GET /measurement/{id}", 100.020, 100.330, cache="miss"),
        ]
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "hoplog_all.txt"), "w") as fh:
                fh.write("\n".join(lines) + "\n")
            r = subprocess.run([sys.executable, AGG, d], capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(os.path.join(d, "stages.csv")) as fh:
                row = next(csv.DictReader(fh))
        self.assertEqual(row["vendor-adapter_cache"], "miss")
        self.assertEqual(row["camara-gateway_cache"], "")
