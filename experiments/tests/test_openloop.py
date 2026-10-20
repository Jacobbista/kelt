import csv
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from lib import openloop  # noqa: E402


def cond(root: str, name: str, totals_ms: list[float], rate: float = 10.0, warmup: int = 0,
         statuses: list[str] | None = None, late_s: float = 0.0) -> str:
    d = os.path.join(root, name)
    os.makedirs(d)
    with open(os.path.join(d, "load.json"), "w") as fh:
        json.dump({"rate_per_s": rate, "count": len(totals_ms), "warmup": warmup}, fh)
    with open(os.path.join(d, f"requests_{name}.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["i", "http_status", "time_total_s", "start_epoch", "late_s"])
        w.writeheader()
        for i, t in enumerate(totals_ms):
            w.writerow({"i": i + 1, "http_status": (statuses or ["200"] * len(totals_ms))[i],
                        "time_total_s": t / 1000, "start_epoch": 1000 + i / rate, "late_s": late_s})
    return d


class LoadStatsTest(unittest.TestCase):
    def test_rate_lateness_and_warmup(self):
        d = cond(tempfile.mkdtemp(), "synthetic-10", [5.0] * 11, warmup=3, late_s=0.002)
        ls = openloop.load_stats(d)
        self.assertEqual((ls["planned_per_s"], ls["sent"], ls["sent_per_s"], ls["warmup"]), (10.0, 11, 10.0, 3))
        self.assertEqual(ls["late_p99_ms"], 2.0)
        self.assertEqual(openloop.load_problems("synthetic-10", ls), [])

    def test_not_open_loop(self):
        self.assertIsNone(openloop.load_stats(tempfile.mkdtemp()))

    def test_late_over_one_period(self):
        d = cond(tempfile.mkdtemp(), "synthetic-10", [5.0] * 11, late_s=0.2)
        self.assertIn("over one period", openloop.load_problems("synthetic-10", openloop.load_stats(d))[0])


if __name__ == "__main__":
    unittest.main()
