import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "validation"))
import check_run  # noqa: E402


class CoveredTest(unittest.TestCase):
    def test_only_runs_the_capture_spans_whole(self):
        # rtt: the full capture spans the idle runs and stops 2 s into the
        # first load run; that run is not covered (its rate would be 2 s of
        # traffic over 322 s).
        wins = [("1-idle", 10.0, 312.0), ("2-idle", 322.0, 624.0), ("4-load", 950.0, 1272.0)]
        self.assertEqual(check_run.covered(wins, 5.0, 952.0), [("1-idle", 10.0, 312.0), ("2-idle", 322.0, 624.0)])

    def test_sorted_by_start(self):
        wins = [("2", 20.0, 30.0), ("1", 0.0, 10.0)]
        self.assertEqual([r for r, _, _ in check_run.covered(wins, 0.0, 30.0)], ["1", "2"])


if __name__ == "__main__":
    unittest.main()
