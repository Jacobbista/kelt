import math
import unittest

from lib.stats import percentile, summary


class PercentileTest(unittest.TestCase):
    def test_nearest_rank_like_hop_aggregate(self):
        xs = list(range(1, 101))            # 1..100
        self.assertEqual(percentile(xs, 0.5), 51)   # round(0.5*99)=50 -> xs[50]
        self.assertEqual(percentile(xs, 0.9), 90)   # round(89.1)=89
        self.assertEqual(percentile(xs, 0.99), 99)  # round(98.01)=98

    def test_empty_is_nan(self):
        self.assertTrue(math.isnan(percentile([], 0.5)))

    def test_order_does_not_matter(self):
        self.assertEqual(percentile([3, 1, 2], 0.5), 2)


class SummaryTest(unittest.TestCase):
    def test_keys_and_values(self):
        s = summary([10.0, 20.0, 30.0, 40.0])
        self.assertEqual(s, {"n": 4, "median": 30.0, "p90": 40.0, "p99": 40.0, "max": 40.0})

    def test_empty_has_no_numbers(self):
        self.assertEqual(summary([]), {"n": 0, "median": None, "p90": None, "p99": None, "max": None})
