import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import figures  # noqa: E402


class RepresentativeTest(unittest.TestCase):
    def test_closest_to_the_median_of_all_sessions(self):
        # Session a alone would pick run 1 (95.0); over both sessions the
        # median of the five means is 94.0, run 3 of session a.
        runs = [("a", 1, 95.0), ("a", 2, 96.0), ("a", 3, 94.0),
                ("b", 1, 80.0), ("b", 2, 93.0)]
        self.assertEqual(figures.representative(runs), ("a", 3))

    def test_tie_goes_to_the_earlier_run(self):
        runs = [("a", 1, 90.0), ("a", 2, 100.0)]
        self.assertEqual(figures.representative(runs), ("a", 1))


class PartMeansTest(unittest.TestCase):
    def test_mean_per_condition_and_part(self):
        rows = [{"condition": "idle", "access_ms": "10", "worker": "0.1", "upf": "0.2"},
                {"condition": "idle", "access_ms": "14", "worker": "0.3", "upf": "0.2"},
                {"condition": "load", "access_ms": "100", "worker": "0.0", "upf": "0.1"}]
        got = figures.part_means(rows, ("access_ms", "worker", "upf"))
        self.assertEqual(got["idle"], {"access_ms": 12.0, "worker": 0.2, "upf": 0.2})
        self.assertEqual(got["load"], {"access_ms": 100.0, "worker": 0.0, "upf": 0.1})


class PooledWindowsTest(unittest.TestCase):
    def test_every_runs_windows_per_combination_as_the_table_uses(self):
        import json
        import tempfile
        runs = []
        for i, (dl1, ul4) in enumerate((([130.0, 129.0], [35.0]), ([20.0], [36.0, 35.5]))):
            d = tempfile.mkdtemp()
            with open(os.path.join(d, "summary.json"), "w") as fh:
                json.dump({"combinations": {"dl1": {"windows": dl1}, "ul4": {"windows": ul4}}}, fh)
            runs.append(d)
        got = figures.pooled_windows(runs)
        self.assertEqual(got["dl1"], [130.0, 129.0, 20.0])
        self.assertEqual(got["ul4"], [35.0, 36.0, 35.5])
        self.assertEqual(got["dl4"], [])

    def test_markers_are_the_tables_percentiles(self):
        from lib.stats import summary
        xs = [float(x) for x in (12, 130, 131, 129, 133, 90, 165, 128, 134, 127, 140)]
        st = summary(xs)
        self.assertEqual(figures.markers(xs), {"median": st["median"], "p90": st["p90"]})


if __name__ == "__main__":
    unittest.main()
