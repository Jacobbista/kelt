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


QOS = {"supi": "imsi-001010000000001", "dnn": "internet", "five_qi": 9, "pdu_state": "active",
       "ue_ambr_dl_mbit": 500, "ue_ambr_ul_mbit": 500, "session_ambr_dl_mbit": 500,
       "session_ambr_ul_mbit": 500, "session_ambr_source": "subscriber_record"}


class QosTest(unittest.TestCase):
    def run_dir(self, doc):
        import json
        import tempfile
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "ue.json"), "w") as fh:
            json.dump(doc, fh)
        return d

    def test_recorded_and_unchanged(self):
        ok, msg = check_run.check_qos(self.run_dir({"mode": "direct", "attempts": "1",
                                                     "attempt_1_qos_start": QOS, "attempt_1_qos_end": QOS}))
        self.assertTrue(ok, msg)
        self.assertIn("500", msg)

    def test_an_unknown_value_fails(self):
        q = dict(QOS, session_ambr_dl_mbit="unknown")
        ok, msg = check_run.check_qos(self.run_dir({"mode": "direct", "attempts": "1",
                                                     "attempt_1_qos_start": q, "attempt_1_qos_end": q}))
        self.assertFalse(ok)
        self.assertIn("session_ambr_dl_mbit", msg)

    def test_a_change_during_the_attempt_fails(self):
        ok, msg = check_run.check_qos(self.run_dir({"mode": "direct", "attempts": "1", "attempt_1_qos_start": QOS,
                                                     "attempt_1_qos_end": dict(QOS, session_ambr_dl_mbit=100)}))
        self.assertFalse(ok)

    def test_a_missing_record_fails(self):
        ok, _ = check_run.check_qos(self.run_dir({"mode": "direct", "attempts": "2",
                                                   "attempt_1_qos_start": QOS, "attempt_1_qos_end": QOS}))
        self.assertFalse(ok)

    def test_nat_mode_is_not_applicable(self):
        ok, msg = check_run.check_qos(self.run_dir({"mode": "nat", "attempts": "1"}))
        self.assertTrue(ok)
        self.assertIn("nat", msg)


if __name__ == "__main__":
    unittest.main()


class ResponseTimeChecksTest(unittest.TestCase):
    def cond(self, root, name, served, without=0, statuses=None, load=None, starts=None, late=None):
        import json
        d = os.path.join(root, name)
        os.makedirs(d)
        with open(os.path.join(d, "stages.json"), "w") as fh:
            json.dump({"traces": sum(served.values()), "without_trace": without}, fh)
        with open(os.path.join(d, "summary.json"), "w") as fh:
            json.dump({"by_status": {"200": {"served_by": served}}}, fh)
        statuses = statuses or ["200"] * 3
        starts = starts or [100.0 + i for i in range(len(statuses))]
        late = late or [0.001] * len(statuses)
        with open(os.path.join(d, f"requests_{name}.csv"), "w") as fh:
            fh.write("i,http_status,x_correlator,start_epoch,late_s\n")
            for i, (s, t, lt) in enumerate(zip(statuses, starts, late)):
                fh.write(f"{i + 1},{s},c{i},{t},{lt}\n")
        if load:
            with open(os.path.join(d, "load.json"), "w") as fh:
                json.dump({"rate_per_s": load}, fh)

    def test_traced_and_routed(self):
        import tempfile
        root = tempfile.mkdtemp()
        self.cond(root, "hit", {"gateway cache": 2, "adapter cache": 1})
        self.cond(root, "local", {"adapter": 3})
        ok, msg = check_run.check_traces(root)
        self.assertTrue(ok, msg)

    def test_missing_trace_unanswered_and_wrong_route_fail(self):
        import tempfile
        root = tempfile.mkdtemp()
        self.cond(root, "fresh", {"vendor": 2, "unknown": 1}, without=1)
        self.cond(root, "local50", {"adapter": 2, "gateway cache": 1}, statuses=["200", "0", "200"])
        ok, msg = check_run.check_traces(root)
        self.assertFalse(ok)
        for part in ("fresh: 1 request(s) without a trace", "fresh: 1 trace(s) with a missing hop",
                     "local50: 1 request(s) without an HTTP answer", "local50: 200s not answered by the adapter"):
            self.assertIn(part, msg)

    def test_rejected_hop_lines_fail(self):
        import json
        import tempfile
        root = tempfile.mkdtemp()
        self.cond(root, "fresh", {"vendor": 3})
        p = os.path.join(root, "fresh", "stages.json")
        with open(p, "w") as fh:
            json.dump({"traces": 3, "without_trace": 0, "rejected_lines": 26}, fh)
        ok, msg = check_run.check_traces(root)
        self.assertFalse(ok)
        self.assertIn("fresh: 26 hop line(s) did not validate against the schema", msg)

    def test_synthetic_condition_answers_only_200(self):
        import tempfile
        root = tempfile.mkdtemp()
        self.cond(root, "local50", {"adapter": 2}, statuses=["200", "422", "200"])
        ok, msg = check_run.check_traces(root)
        self.assertFalse(ok)
        self.assertIn("local50: 1 request(s) answered other than 200", msg)

    def test_open_loop_rate_and_lateness(self):
        import tempfile
        root = tempfile.mkdtemp()
        n = 101
        self.cond(root, "local50", {"adapter": n}, statuses=["200"] * n,
                  load=50, starts=[i / 50 for i in range(n)], late=[0.002] * n)
        ok, msg = check_run.check_load(root)
        self.assertTrue(ok, msg)
        root = tempfile.mkdtemp()
        self.cond(root, "local50", {"adapter": n}, statuses=["200"] * n,
                  load=50, starts=[i / 40 for i in range(n)], late=[0.05] * n)
        ok, msg = check_run.check_load(root)
        self.assertFalse(ok)
        self.assertIn("sent 40.0/s of 50/s", msg)

    def rest_dir(self, hops, devices, journal=None):
        import json
        import tempfile
        root = tempfile.mkdtemp()
        d = os.path.join(root, "rest")
        os.makedirs(d)
        with open(os.path.join(d, "window.json"), "w") as fh:
            json.dump({"windows": [{"label": "rest", "start_utc": "2026-10-15T10:00:00Z",
                                    "end_utc": "2026-10-15T10:05:00Z"}]}, fh)
        with open(os.path.join(d, "hoplog_wifi-adapter_rest.txt"), "w") as fh:
            for t, st in hops:
                fh.write("INFO:hop:" + json.dumps({"event": "hop", "service": "wifi-adapter", "stage": "POST /ingest/wifi-scan",
                                                   "correlator": f"c{t}", "status": st, "t_receive": t, "t_emit": t,
                                                   "span_ms": 1.0}) + "\n")
        with open(os.path.join(d, "wifi-devices-end.json"), "w") as fh:
            json.dump(devices, fh)
        if journal is not None:
            with open(os.path.join(d, "scanner-journal.txt"), "w") as fh:
                fh.write("".join(f"  56 APs in 3.1s → sent (backlog 0)\n" for _ in range(journal)))
        return root

    def test_wifi_ingests_inside_the_rest_window(self):
        start = 1792058400.0   # 2026-10-15T10:00:00Z
        root = self.rest_dir([(start + 10, 200), (start + 13, 200), (start - 50, 200)],
                             [{"id": "s", "lastSeen": start + 13, "located": False}], journal=2)
        self.assertEqual(check_run.check_wifi(root), (True, "WiFi scan ingests at rest: 2 (200 2); no device located "
                                                            "at the end; the scanner logged 2 scan(s) in the window"))

    def test_scanner_and_adapter_disagree(self):
        start = 1792058400.0
        root = self.rest_dir([(start + 10, 200)], [], journal=5)
        ok, msg = check_run.check_wifi(root)
        self.assertFalse(ok)
        self.assertIn("do not agree", msg)

    def test_no_ingest(self):
        root = self.rest_dir([], [])
        self.assertEqual(check_run.check_wifi(root), (True, "no WiFi scan ingest at rest"))

class FootprintCheckTest(unittest.TestCase):
    def run_with(self, pods, names):
        import json
        import tempfile
        from unittest import mock
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "footprint.json"), "w") as fh:
            json.dump({"rest": {"windows": [[0, 10]]}, "local50": {"windows": [[100, 110]]}}, fh)
        node = [(float(t),) for t in list(range(0, 11)) + list(range(100, 111))]
        loaded = ({"worker": (node, {uid: [(float(t),) for t in ts] for uid, ts in pods.items()})}, names, {})
        with mock.patch.object(check_run.footprint, "_load", return_value=loaded):
            return check_run.check_footprint(d)

    def test_a_pod_replaced_between_conditions_is_not_a_gap(self):
        ok, msg = self.run_with({"old": range(0, 11), "new": range(100, 111)},
                                {"old": ("positioning", "synthetic-adapter-a-1"), "new": ("positioning", "synthetic-adapter-b-2")})
        self.assertTrue(ok, msg)

    def test_a_pod_that_stops_inside_a_window_is_a_gap(self):
        ok, msg = self.run_with({"old": range(0, 5)}, {"old": ("positioning", "synthetic-adapter-a-1")})
        self.assertFalse(ok)
        self.assertIn("synthetic-adapter-a-1", msg)

    def test_a_pod_without_a_name_fails(self):
        ok, msg = self.run_with({"new": range(100, 111)}, {})
        self.assertFalse(ok)
        self.assertIn("pods without a name: 1", msg)


class ScannerOutcomesTest(unittest.TestCase):
    def test_counts_by_outcome(self):
        import tempfile
        p = os.path.join(tempfile.mkdtemp(), "scanner-journal.txt")
        with open(p, "w") as fh:
            fh.write('  [!] adapter 422: {"detail":"no known access points in scan"} (dropped)\n'
                     "  58 APs in 3.1s → dropped (backlog 0)\n"
                     "  56 APs in 3.2s → sent (backlog 0)\n")
        self.assertEqual(check_run.scanner_outcomes(p), {"dropped": 1, "sent": 1, "of which refused 422": 1})

    def test_no_journal(self):
        self.assertIsNone(check_run.scanner_outcomes("/nonexistent/scanner-journal.txt"))
