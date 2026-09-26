import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "exposure"))
import response_time_summary as rts  # noqa: E402

ROWS = [
    {"e2e_ms": "200", "wittra_span_ms": "150", "camara-gateway_self_ms": "20", "wittra_self_ms": "150"},
    {"e2e_ms": "30", "wittra_span_ms": "0.3", "camara-gateway_self_ms": "22", "wittra_self_ms": "0.3"},
    {"e2e_ms": "210", "wittra_span_ms": "160", "camara-gateway_self_ms": "21", "wittra_self_ms": "160"},
]


class SummaryTest(unittest.TestCase):
    def test_stack_share_only_on_traces_that_reached_the_vendor(self):
        # 0.3 ms = the adapter answered from its own cache (live, 2026-09-26);
        # the cloud call takes ~150 ms. The split is at MISS_MS.
        s = rts.summarise(ROWS, "wittra_span_ms")
        self.assertEqual(rts.MISS_MS, 10.0)
        self.assertEqual(s["traces"], 3)
        self.assertEqual(s["with_vendor_span"], 2)
        self.assertEqual(s["stack_ms"]["n"], 2)
        self.assertEqual(s["stack_ms"]["median"], 50.0)   # 200-150, 210-160

    def test_no_vendor_column(self):
        s = rts.summarise(ROWS, None)
        self.assertEqual(s["with_vendor_span"], 0)
        self.assertEqual(s["stack_ms"]["n"], 0)
        self.assertEqual(s["e2e_ms"]["n"], 3)

    def test_per_component(self):
        s = rts.summarise(ROWS, "wittra_span_ms")
        self.assertEqual(s["per_component"]["camara-gateway"]["median"], 21.0)


class RateTest(unittest.TestCase):
    def test_achieved_rate_from_the_window(self):
        w = {"start_utc": "2026-09-26T12:41:25Z", "end_utc": "2026-09-26T12:42:10Z"}
        self.assertEqual(rts.achieved_rate(90, w), 2.0)

    def test_no_window_no_rate(self):
        self.assertIsNone(rts.achieved_rate(90, None))
