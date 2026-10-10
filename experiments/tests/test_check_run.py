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
