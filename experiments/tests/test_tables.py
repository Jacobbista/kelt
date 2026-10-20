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
        comb = {"n_runs": 5, "failed": [3], "samples": {"n": 2750, "mean": 24.0, "median": 23.5, "p90": 31.0, "p99": 40.0, "max": 50.0},
                "run_means": [20.0, 25.0], "spread": [20.0, 25.0], "representative": 2, "sender_tcp_cc": "cubic", "runs": []}
        d = mk_net(root, "throughput", "20260930T100000Z", {"discard_s": 5.0, "combinations": {"dl1": comb}})
        rows = tables.throughput_rows([d])
        self.assertEqual(rows, [{"run": "20260930T100000Z", "target": "10.208.0.202", "combination": "dl1", "mode": "nat",
                                 "runs": 5, "failed": 1, "mean_mbit_s": 24.0,
                                 "median_mbit_s": 23.5, "p90_mbit_s": 31.0, "run_mean_min": 20.0, "run_mean_max": 25.0,
                                 "sender_tcp_cc": "cubic"}])

    def test_throughput_sessions_pooled_from_their_windows(self):
        root = tempfile.mkdtemp()

        def comb(ws):
            return {"n_runs": 1, "failed": [], "samples": {}, "run_means": [sum(ws) / len(ws)],
                    "spread": [sum(ws) / len(ws)] * 2, "sender_tcp_cc": "cubic", "runs": [], "windows": ws}
        a = mk_net(root, "throughput", "20261005T100000Z", {"combinations": {"dl1": comb([20.0, 130.0])}})
        b = mk_net(root, "throughput", "20261006T100000Z", {"combinations": {"dl1": comb([128.0, 132.0])}})
        rows = tables.throughput_rows([a, b])
        pooled = rows[-1]
        self.assertEqual((pooled["run"], pooled["runs"], pooled["median_mbit_s"], pooled["mean_mbit_s"]), ("all", 2, 130.0, 102.5))
        self.assertEqual((pooled["run_mean_min"], pooled["run_mean_max"]), (75.0, 130.0))

    def test_rtt_sessions_pooled_from_their_samples(self):
        root = tempfile.mkdtemp()
        st = {"n": 1, "median": 1.0, "p90": 1.0, "p99": 1.0, "max": 1.0}
        doc = {"conditions": {"idle": {"runs": [{"loss": 0.0}], "total": st, "core": st, "access": st}}}
        ds = []
        for stamp, rows in (("20261005T100000Z", "idle,1,1,12.0,0.2,11.8\n"), ("20261006T100000Z", "idle,1,1,14.0,0.4,13.6\n")):
            d = mk_net(root, "rtt", stamp, doc)
            with open(os.path.join(d, "rtt-samples.csv"), "w") as fh:
                fh.write("condition,run,seq,total_ms,core_ms,access_ms,worker,ovs_n3,upf,ovs_n6m,server\n" + rows.replace("\n", ",,,,,\n"))
            ds.append(d)
        pooled = [r for r in tables.rtt_rows(ds) if r["run"] == "all"]
        self.assertEqual([(r["part"], r["n"], r["min_ms"], r["mean_ms"]) for r in pooled],
                         [("total", 2, 12.0, 13.0), ("core", 2, 0.2, 0.3), ("access", 2, 11.8, 12.7)])

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
        self.assertEqual(tables.rtt_notes([d])[0],
                         f"Idle runs over the foreign traffic limit ({tables.FOREIGN_LIMIT_B_S} B/s): "
                         "20260930T100000Z run 2 (2500.0 B/s).")
        doc["conditions"]["idle"]["runs"].pop()
        d = mk_net(root, "rtt", "20260930T110000Z", doc)
        self.assertEqual(tables.rtt_notes([d])[0],
                         f"Idle runs over the foreign traffic limit ({tables.FOREIGN_LIMIT_B_S} B/s): none.")


class RttOverTest(unittest.TestCase):
    def test_echoes_above_the_rest_cut_are_counted_across_sessions(self):
        root = tempfile.mkdtemp()
        runs = []
        for stamp, totals in (("20261010T100000Z", [13.0, 41.0, 39.9]), ("20261011T100000Z", [55.0, 40.0])):
            d = mk_net(root, "rtt", stamp, {"conditions": {}})
            with open(os.path.join(d, "rtt-samples.csv"), "w") as fh:
                fh.write("condition,run,seq,total_ms\n")
                fh.writelines(f"idle,1,{i},{t}\n" for i, t in enumerate(totals))
                fh.write("load,1,9,120.0\n")
            runs.append(d)
        self.assertEqual(tables.rtt_over(runs, "idle", 40.0), 2)

    def test_note_names_the_count(self):
        root = tempfile.mkdtemp()
        d = mk_net(root, "rtt", "20261010T100000Z", {"conditions": {}})
        with open(os.path.join(d, "rtt-samples.csv"), "w") as fh:
            fh.write("condition,run,seq,total_ms\nidle,1,1,45.0\n")
        self.assertIn(f"At rest, echoes above {tables.REST_CUT_MS:g} ms: 1.", tables.rtt_notes([d]))


class RetransmissionRowsTest(unittest.TestCase):
    def test_data_direction_per_session_and_all(self):
        root = tempfile.mkdtemp()
        def run(dl_seg, dl_re, ul_seg, ul_re):
            return {"index": 1, "br_ran": {"dl": {"segments": dl_seg, "retransmitted": dl_re},
                                           "ul": {"segments": ul_seg, "retransmitted": ul_re}}}
        runs = []
        for stamp, dl1, ul1 in (("20261010T100000Z", run(1000, 10, 50, 0), run(40, 0, 2000, 2)),
                                ("20261011T100000Z", run(3000, 5, 60, 1), run(30, 0, 1000, 0))):
            runs.append(mk_net(root, "throughput", stamp,
                               {"combinations": {"dl1": {"runs": [dl1]}, "ul1": {"runs": [ul1]}}}))
        rows = tables.retransmission_rows(runs)
        got = {(r["run"], r["combination"]): (r["direction"], r["segments"], r["retransmitted"], r["share_pct"]) for r in rows}
        self.assertEqual(got[("20261010T100000Z", "dl1")], ("dl", 1000, 10, 1.0))
        self.assertEqual(got[("20261010T100000Z", "ul1")], ("ul", 2000, 2, 0.1))
        self.assertEqual(got[("all", "dl1")], ("dl", 4000, 15, 0.375))
        self.assertEqual(got[("all", "ul1")], ("ul", 3000, 2, 0.0667))


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


class FootprintPivotTest(unittest.TestCase):
    def test_groups_host_and_vm_processes_by_condition_sessions_together(self):
        def row(campaign, cond, level, name, mean, peak):
            return {"campaign": campaign, "condition": cond, "level": level, "name": name,
                    "cpu_mean_m": mean, "cpu_peak_m": peak, "mem_mean_mib": mean / 10, "mem_peak_mib": peak / 10}
        rows = [row("resource-use", "idle", "group", "core", 100.0, 200.0),
                row("throughput", "dl1", "group", "core", 300.0, 500.0),
                row("throughput", "dl1", "group", "core", 500.0, 900.0),       # second session
                row("rtt", "load", "machine", "host", 2000.0, 3000.0),
                row("throughput", "dl1", "pod", "upf-cloud-1", 1.0, 1.0)]       # pods stay out
        md = tables.footprint_pivot(rows)
        self.assertIn("| what | rest | dl1 | dl4 | ul1 | ul4 | RTT rest | RTT load |", md)
        core = next(ln for ln in md if ln.startswith("| core |"))
        self.assertEqual(core, "| core | 100 / 200 | 400 / 900 | — | — | — | — | — |")
        host = next(ln for ln in md if ln.startswith("| host |"))
        self.assertTrue(host.endswith("| 2000 / 3000 |"))


class MachineNoteTest(unittest.TestCase):
    def test_the_size_each_machine_reports(self):
        root = tempfile.mkdtemp()
        d = mk(root, "resource-use", "20261005T100000Z")
        doc = {"idle": {"nodes": {"host": {"cpus": 16, "mem_total_mib": 63960.0},
                                  "worker": {"cpus": 4, "mem_total_mib": 9941.0},
                                  "vm-process worker": {}}, "groups": {}, "pods": []}}
        with open(os.path.join(d, "footprint.json"), "w") as fh:
            json.dump(doc, fh)
        self.assertEqual(tables.machine_note([d]),
                         "Machines, as each reports itself: host 16 CPUs, 63960 MiB; worker 4 CPUs, 9941 MiB.")


class ResponseTimeRowsTest(unittest.TestCase):
    def test_one_block_per_status(self):
        import json
        import tempfile
        q = {"n": 2, "median": 1.0, "p90": 2.0, "p99": 3.0, "max": 4.0}
        empty = {"n": 0, "median": None, "p90": None, "p99": None, "max": None}
        per = {"traces": 2, "e2e_ms": q, "stack_ms": empty, "vendor_ms": empty,
               "per_component": {"camara-gateway": q}, "served_by": {"vendor": 1, "adapter cache": 1}}
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "fresh"))
            with open(os.path.join(d, "fresh", "summary.json"), "w") as fh:
                json.dump({"by_status": {"200": per, "422": per}, "achieved_rate_per_s": 0.15}, fh)
            rows = tables.response_time_rows([d])
        self.assertEqual(sorted({r["status"] for r in rows}), ["200", "422"])
        e2e = [r for r in rows if r["status"] == "200" and r["series"] == "end to end"][0]
        self.assertEqual((e2e["median_ms"], e2e["max_ms"]), (1.0, 4.0))
        self.assertEqual(e2e["served_by"], "adapter cache 1, vendor 1")
        self.assertEqual(e2e["rate_per_s"], 0.15)

    def test_open_loop_rate_and_split(self):
        import json
        import tempfile
        q = {"n": 4, "median": 1.0, "p90": 2.0, "p99": 3.0, "max": 4.0}
        per = {"traces": 4, "e2e_ms": q, "stack_ms": q, "vendor_ms": q, "per_component": {},
               "served_by": {"adapter": 4}, "e2e_split": {"threshold_ms": 9.0, "below": 3, "above": 1},
               "e2e_hist_1ms": {"5": 3, "12": 1}}
        load = {"planned_per_s": 10.0, "sent_per_s": 9.99, "late_p99_ms": 0.4, "warmup": 100, "sent": 1100}
        with tempfile.TemporaryDirectory() as d:
            for c in ("synthetic-10", "wittra-cached"):
                os.makedirs(os.path.join(d, c))
                with open(os.path.join(d, c, "summary.json"), "w") as fh:
                    json.dump({"by_status": {"200": per}, "achieved_rate_per_s": 9.0, "load": load}, fh)
            rows = tables.response_time_rows([d])
            split = tables.split_rows([d])
            hist = tables.hist_rows([d])
        e2e = [r for r in rows if r["series"] == "end to end"][0]
        self.assertEqual((e2e["rate_planned_per_s"], e2e["rate_per_s"], e2e["late_p99_ms"]), (10.0, 9.99, 0.4))
        self.assertEqual([(r["condition"], r["below"], r["above"], r["below_pct"]) for r in split],
                         [("synthetic-10", 3, 1, 75.0)])
        self.assertIn({"run": os.path.basename(d), "condition": "synthetic-10", "bin_ms": 12, "count": 1}, hist)


class StreamRowsTest(unittest.TestCase):
    def test_clients_then_ages(self):
        import json
        import tempfile
        q = {"n": 10, "median": 250.0, "p90": 450.0, "p99": 490.0, "max": 499.0}
        st = {"clients": 1, "engine_interval_ms": 500, "age_ms": {"synthetic": q},
              "per_client": [{"client": 0, "messages_per_s": 2.0, "positions_per_s": 102.0,
                              "interval_median_ms": 500.1, "interval_max_ms": 650.0, "closed_early": None}]}
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "stream-1"))
            with open(os.path.join(d, "stream-1", "stream.json"), "w") as fh:
                json.dump(st, fh)
            rows = tables.stream_rows([d])
        self.assertEqual([r["what"] for r in rows], ["client 0", "age on arrival, synthetic"])
        self.assertEqual((rows[0]["positions_per_s"], rows[1]["p99_ms"], rows[1]["engine_interval_ms"]),
                         (102.0, 490.0, 500))


class ExposureRowsTest(unittest.TestCase):
    def test_deployment_of_a_pod(self):
        self.assertEqual(tables.deployment_of("camara-gateway-88895f5bc-m9np6"), "camara-gateway")
        self.assertEqual(tables.deployment_of("wittra-558578f9fb-vztnc"), "wittra")

    def test_exposure_group_and_its_pods_per_condition(self):
        root = tempfile.mkdtemp()
        st = lambda m, p: {"mean": m, "max": p, "n": 60}
        pod = lambda name, group, m: {"node": "worker", "namespace": "positioning", "pod": name, "group": group,
                                      "cpu_mcores": st(m, 2 * m), "mem_mib": st(100.0, 101.0)}
        doc = {"rest": {"nodes": {}, "groups": {"exposure": {"cpu_mcores": st(10.0, 20.0), "mem_mib": st(500.0, 510.0)}},
                        "pods": []},
               "synthetic-50": {"nodes": {}, "groups": {"exposure": {"cpu_mcores": st(300.0, 400.0), "mem_mib": st(500.0, 510.0)}},
                                "pods": [pod("synthetic-adapter-6c9d8f7b5-abcde", "exposure", 120.0),
                                         pod("upf-cloud-0-x-y", "core", 9.0)]}}
        d = mk(root, "response-time", "20261015T100000Z")
        with open(os.path.join(d, "footprint.json"), "w") as fh:
            json.dump(doc, fh)
        rows = tables.exposure_rows([d])
        self.assertEqual([(r["condition"], r["level"], r["name"]) for r in rows],
                         [("rest", "group", "exposure"), ("synthetic-50", "group", "exposure"),
                          ("synthetic-50", "deployment", "synthetic-adapter")])
        md = tables.exposure_pivot(rows)
        self.assertIn("| what | rest | synthetic-50 |", md)
        self.assertIn("| synthetic-adapter | — | 120 / 240 |", md)
        self.assertIn("| exposure, all pods | 10 / 20 | 300 / 400 |", md)

    def test_a_discarded_condition_is_left_out(self):
        root = tempfile.mkdtemp()
        st = lambda m: {"mean": m, "max": m, "n": 60}
        doc = {c: {"nodes": {}, "groups": {"exposure": {"cpu_mcores": st(1.0), "mem_mib": st(1.0)}}, "pods": []}
               for c in ("rest", "hit")}
        d = mk(root, "response-time", "20261015T100000Z")
        with open(os.path.join(d, "footprint.json"), "w") as fh:
            json.dump(doc, fh)
        with open(os.path.join(d, "window.json"), "w") as fh:
            json.dump({"windows": [], "discarded": [{"what": "rest", "reason": "measured again"}]}, fh)
        self.assertEqual([r["condition"] for r in tables.exposure_rows([d])], ["hit"])
