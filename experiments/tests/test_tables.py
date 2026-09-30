import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import tables  # noqa: E402


def mk(root, slug, stamp, pilot=False, dirty=False):
    d = os.path.join(root, slug, stamp)
    os.makedirs(d)
    with open(os.path.join(d, "provenance.json"), "w") as fh:
        json.dump({"pilot": pilot, "kelt_worktree_dirty": dirty}, fh)
    return d


class SelectTest(unittest.TestCase):
    def test_pilots_never_selected(self):
        root = tempfile.mkdtemp()
        good = mk(root, "resource-use", "20260930T100000Z")
        mk(root, "resource-use", "20260930T110000Z", pilot=True)
        dirty = mk(root, "resource-use", "20260930T120000Z", dirty=True)
        self.assertEqual(tables.select_runs(root, "resource-use", None), [good, dirty])

    def test_listed_runs_only(self):
        root = tempfile.mkdtemp()
        a = mk(root, "resource-use", "20260930T100000Z")
        mk(root, "resource-use", "20260930T130000Z")
        self.assertEqual(tables.select_runs(root, "resource-use", {"resource-use/20260930T100000Z"}), [a])

    def test_run_without_provenance_is_skipped(self):
        root = tempfile.mkdtemp()
        os.makedirs(os.path.join(root, "resource-use", "20260930T100000Z"))
        self.assertEqual(tables.select_runs(root, "resource-use", None), [])


def mk_net(root, slug, stamp, summary_doc, ue=None):
    d = mk(root, slug, stamp)
    with open(os.path.join(d, "summary.json"), "w") as fh:
        json.dump(summary_doc, fh)
    with open(os.path.join(d, "ue.json"), "w") as fh:
        json.dump(ue or {"mode": "nat", "target": "10.208.0.202"}, fh)
    return d


class NetworkRowsTest(unittest.TestCase):
    def test_throughput_one_row_per_combination(self):
        root = tempfile.mkdtemp()
        comb = {"n_runs": 5, "failed": [3], "samples": {"n": 2750, "median": 23.5, "p90": 31.0, "p99": 40.0, "max": 50.0},
                "run_means": [20.0, 25.0], "spread": [20.0, 25.0], "representative": 2, "sender_tcp_cc": "cubic", "runs": []}
        d = mk_net(root, "throughput", "20260930T100000Z", {"discard_s": 5.0, "combinations": {"dl1": comb}})
        rows = tables.throughput_rows([d])
        self.assertEqual(rows, [{"run": "20260930T100000Z", "target": "10.208.0.202", "combination": "dl1", "mode": "nat",
                                 "runs": 5, "failed": 1,
                                 "median_mbit_s": 23.5, "p90_mbit_s": 31.0, "run_mean_min": 20.0, "run_mean_max": 25.0,
                                 "sender_tcp_cc": "cubic"}])

    def test_rtt_one_row_per_condition_and_part(self):
        root = tempfile.mkdtemp()
        st = {"n": 3000, "median": 14.0, "p90": 18.0, "p99": 25.0, "max": 40.0}
        doc = {"ue_address": "10.45.0.7", "foreign_packets": 12, "conditions": {
            "idle": {"runs": [{"loss": 0.0, "foreign_packets": 40}, {"loss": 0.001, "foreign_packets": 52}],
                     "total": st, "core": st, "access": st,
                     "pairing": {"paired": 2990, "unpaired": 10}},
            "load": {"runs": [{"loss": 0.01}], "total": st}}}
        d = mk_net(root, "rtt", "20260930T100000Z", doc)
        rows = tables.rtt_rows([d])
        self.assertEqual([(r["condition"], r["part"]) for r in rows],
                         [("idle", "total"), ("idle", "core"), ("idle", "access"), ("load", "total")])
        self.assertEqual(rows[0]["max_loss"], 0.001)
        self.assertEqual(rows[0]["max_foreign_packets_in_run"], 52)
        self.assertEqual(rows[1]["median_ms"], 14.0)
        self.assertEqual(rows[0]["target"], "10.208.0.202")

    def test_rtt_rows_for_every_part_of_the_core(self):
        root = tempfile.mkdtemp()
        st = {"n": 600, "median": 0.1, "p90": 0.2, "p99": 0.3, "max": 0.4}
        doc = {"conditions": {"idle": {"runs": [{"loss": 0.0}], "total": st, "core": st, "access": st,
                                       "segments": {"worker": st, "upf": st}}}}
        rows = tables.rtt_rows([mk_net(root, "rtt", "20260930T100000Z", doc)])
        self.assertEqual([r["part"] for r in rows], ["total", "core", "access", "core: worker", "core: upf"])

    def test_rtt_foreign_traffic_only_where_the_capture_ran(self):
        # the capture covers the idle runs only: the load rows have no figure
        root = tempfile.mkdtemp()
        st = {"n": 3000, "median": 14.0, "p90": 18.0, "p99": 25.0, "max": 40.0}
        doc = {"conditions": {
            "idle": {"runs": [{"loss": 0.0, "foreign_packets": 40, "foreign_bytes_per_s": 44.0},
                              {"loss": 0.0, "foreign_packets": 60, "foreign_bytes_per_s": 11.0}], "total": st},
            "load": {"runs": [{"loss": 0.01}], "total": st}}}
        rows = tables.rtt_rows([mk_net(root, "rtt", "20260930T100000Z", doc)])
        self.assertEqual(rows[0]["max_foreign_bytes_per_s"], 44.0)
        self.assertIsNone(rows[1]["max_foreign_packets_in_run"])
        self.assertIsNone(rows[1]["max_foreign_bytes_per_s"])

    def test_rtt_idle_runs_over_the_foreign_limit_are_named(self):
        root = tempfile.mkdtemp()
        st = {"n": 3000, "median": 14.0, "p90": 18.0, "p99": 25.0, "max": 40.0}
        doc = {"conditions": {"idle": {"runs": [
            {"index": 1, "loss": 0.0, "foreign_packets": 40, "foreign_bytes_per_s": 44.0},
            {"index": 2, "loss": 0.0, "foreign_packets": 900, "foreign_bytes_per_s": 2500.0}], "total": st}}}
        d = mk_net(root, "rtt", "20260930T100000Z", doc)
        self.assertEqual(tables.rtt_notes([d]),
                         [f"Idle runs over the foreign traffic limit ({tables.FOREIGN_LIMIT_B_S} B/s): "
                          "20260930T100000Z run 2 (2500.0 B/s)."])
        doc["conditions"]["idle"]["runs"].pop()
        d = mk_net(root, "rtt", "20260930T110000Z", doc)
        self.assertEqual(tables.rtt_notes([d]),
                         [f"Idle runs over the foreign traffic limit ({tables.FOREIGN_LIMIT_B_S} B/s): none."])


class FootprintRowsTest(unittest.TestCase):
    def test_machines_groups_and_pods_per_condition_from_every_campaign(self):
        root = tempfile.mkdtemp()
        st = lambda m, p: {"mean": m, "max": p, "n": 50}
        doc = {"dl1": {"nodes": {"worker": {"cpu_mcores": st(900.0, 1400.0), "mem_mib": st(6000.0, 6100.0)}},
                       "groups": {"core": {"cpu_mcores": st(300.0, 500.0), "mem_mib": st(700.0, 710.0)}},
                       "pods": [{"node": "worker", "namespace": "5g", "pod": "upf-cloud-1", "group": "core",
                                 "cpu_mcores": st(250.0, 450.0), "mem_mib": st(90.0, 91.0)}]}}
        idle = {"idle": {"nodes": {"host": {"cpu_mcores": st(800.0, 1000.0), "mem_mib": st(15000.0, 15100.0)}},
                         "groups": {}, "pods": []}}
        a = mk(root, "throughput", "20260930T100000Z")
        b = mk(root, "resource-use", "20260930T090000Z")
        for d, x in ((a, doc), (b, idle)):
            with open(os.path.join(d, "footprint.json"), "w") as fh:
                json.dump(x, fh)
        rows = tables.footprint_rows([b, a])
        self.assertEqual([(r["campaign"], r["condition"], r["level"], r["name"]) for r in rows],
                         [("resource-use", "idle", "machine", "host"), ("throughput", "dl1", "machine", "worker"),
                          ("throughput", "dl1", "group", "core"), ("throughput", "dl1", "pod", "upf-cloud-1")])
        self.assertEqual((rows[3]["cpu_mean_m"], rows[3]["cpu_peak_m"], rows[3]["mem_peak_mib"]), (250.0, 450.0, 91.0))

    def test_the_footprint_table_takes_the_runs_of_three_campaigns(self):
        self.assertEqual(tables.FOOTPRINT_CAMPAIGNS, ("resource-use", "throughput", "rtt"))
