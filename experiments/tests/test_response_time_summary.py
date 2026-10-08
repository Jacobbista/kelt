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


class ServedByTest(unittest.TestCase):
    # northbound 0.19: a gateway cache hit leaves only the gateway's hop line; an
    # adapter cache hit leaves the adapter's line without the vendor time.
    def test_classification(self):
        rows = [
            {"correlator": "a", "e2e_ms": "2", "camara-gateway_span_ms": "2", "positioning-engine_span_ms": "0", "wittra_span_ms": "0"},
            {"correlator": "b", "e2e_ms": "30", "camara-gateway_span_ms": "30", "positioning-engine_span_ms": "20", "wittra_span_ms": "0.3"},
            {"correlator": "c", "e2e_ms": "200", "camara-gateway_span_ms": "200", "positioning-engine_span_ms": "180", "wittra_span_ms": "150"},
        ]
        self.assertEqual([rts.served_by(r, "wittra_span_ms") for r in rows], ["gateway cache", "adapter cache", "vendor"])

    def test_an_adapter_without_a_vendor_is_not_a_cache(self):
        # synthetic: the adapter computes the fix; there is no vendor hop at all.
        row = {"correlator": "s", "e2e_ms": "20", "camara-gateway_span_ms": "20", "positioning-engine_span_ms": "15",
               "synthetic-adapter_span_ms": "3"}
        self.assertEqual(rts.served_by(row, None), "adapter")

    def test_the_hop_line_cache_field_decides_over_the_span(self):
        # vendor-adapter 0.19.2 writes cache: hit|miss on its GET /measurement line.
        base = {"correlator": "c", "e2e_ms": "30", "camara-gateway_span_ms": "30", "positioning-engine_span_ms": "20"}
        self.assertEqual(rts.served_by(dict(base, **{"vendor-adapter_span_ms": "25", "vendor-adapter_cache": "hit"}),
                                       "vendor-adapter_span_ms"), "adapter cache")
        self.assertEqual(rts.served_by(dict(base, **{"vendor-adapter_span_ms": "4", "vendor-adapter_cache": "miss"}),
                                       "vendor-adapter_span_ms"), "vendor")

    def test_a_vendor_trace_without_the_adapter_line_is_unknown(self):
        # stages.csv writes 0 for a service with no hop line in that trace.
        row = {"correlator": "u", "e2e_ms": "40", "camara-gateway_span_ms": "40", "positioning-engine_span_ms": "30",
               "vendor-adapter_span_ms": "0.000"}
        self.assertEqual(rts.served_by(row, "vendor-adapter_span_ms"), "unknown")

    def test_split_by_request_status(self):
        rows = [dict(r, correlator=c) for r, c in zip(ROWS, ("x1", "x2", "x3"))]
        out = rts.by_status(rows, {"x1": "200", "x2": "422", "x3": "200"}, "wittra_span_ms")
        self.assertEqual(sorted(out), ["200", "422"])
        self.assertEqual(out["200"]["traces"], 2)
        self.assertEqual(out["422"]["traces"], 1)


class RateTest(unittest.TestCase):
    def test_achieved_rate_from_the_window(self):
        w = {"start_utc": "2026-09-26T12:41:25Z", "end_utc": "2026-09-26T12:42:10Z"}
        self.assertEqual(rts.achieved_rate(90, w), 2.0)

    def test_no_window_no_rate(self):
        self.assertIsNone(rts.achieved_rate(90, None))
