import json
import os
import struct
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from lib.stats import summary  # noqa: E402
from network import throughput  # noqa: E402
from tests.test_rtt import TARGET, UE, frame, ipv4, pcap  # noqa: E402


def iperf(mbits, step=1.0, sent=None, retrans=None):
    """An iperf3 -J result with one interval per value (Mbit/s)."""
    ivs = [{"sum": {"start": i * step, "end": (i + 1) * step, "bits_per_second": v * 1e6}} for i, v in enumerate(mbits)]
    total = int(sum(mbits) * 1e6 * step / 8)
    end = {"sum_sent": {"bytes": sent if sent is not None else total}, "sum_received": {"bytes": total}}
    if retrans is not None:
        end["sum_sent"]["retransmits"] = retrans
    return {"intervals": ivs, "end": end}


def run_dir(runs, ue=None, worker_rows=None):
    """runs: list of (index, name, iperf_json, start, end, rc, if_before, if_after)."""
    d = tempfile.mkdtemp()
    r = os.path.join(d, "raw", "ue", "1", "runs")
    os.makedirs(r)
    for idx, name, js, start, end, rc, b, a in runs:
        with open(os.path.join(r, f"{idx}-{name}.json"), "w") as fh:
            json.dump(js, fh)
        with open(os.path.join(r, f"{idx}-{name}.meta"), "w") as fh:
            fh.write(f"start={start} end={end} rc={rc} if_before={','.join(map(str, b))} if_after={','.join(map(str, a))}\n")
    with open(os.path.join(d, "ue.json"), "w") as fh:
        json.dump(ue or {"ue_tcp_cc": "cubic", "server_tcp_cc": "cubic", "target": TARGET}, fh)
    if worker_rows is not None:
        os.makedirs(os.path.join(d, "raw", "worker", "1"))
        with open(os.path.join(d, "raw", "worker", "1", "ran-nic.csv"), "w") as fh:
            fh.write("epoch,rx_bytes,tx_bytes,rx_packets,tx_packets\n")
            for row in worker_rows:
                fh.write(",".join(map(str, row)) + "\n")
    return d


Z = (0, 0, 0, 0)


class SamplesTest(unittest.TestCase):
    def test_the_first_five_seconds_are_dropped(self):
        js = iperf([1, 2, 3, 4, 5, 60, 70], step=1.0)
        self.assertEqual(throughput.samples(js), [60.0, 70.0])

    def test_tenth_of_a_second_intervals(self):
        js = iperf([10] * 50 + [20] * 10, step=0.1)
        self.assertEqual(throughput.samples(js, discard_s=5.0), [20.0] * 10)


class ReceiverSideTest(unittest.TestCase):
    def test_uplink_samples_come_from_the_server_the_receiver(self):
        js = iperf([0] * 5 + [99, 99])                    # the client's write rate
        js["start"] = {"test_start": {"reverse": 0}}
        js["server_output_json"] = iperf([0] * 5 + [30, 31])
        self.assertEqual(throughput.samples(js), [30.0, 31.0])

    def test_downlink_samples_stay_the_clients(self):
        js = iperf([0] * 5 + [40])
        js["start"] = {"test_start": {"reverse": 1}}
        js["server_output_json"] = iperf([0] * 5 + [1])
        self.assertEqual(throughput.samples(js), [40.0])


SERVER_TEXT_4 = """Accepted connection from 10.45.0.7, port 50598
[ ID] Interval           Transfer     Bitrate
[ 23]   5.00-5.10   sec   256 KBytes  20.0 Mbits/sec
[ 25]   5.00-5.10   sec   128 KBytes  10.0 Mbits/sec
[SUM]   5.00-5.10   sec   384 KBytes  30.0 Mbits/sec
[ 23]   5.10-5.20   sec   256 KBytes  20.0 Mbits/sec
[SUM]   5.10-5.20   sec   384 KBytes  1.5 Gbits/sec
- - - - - - - - - - - - - - - - - - - - - - - - -
[SUM]   0.00-20.34  sec  69.1 MBytes  28.5 Mbits/sec                  receiver
"""
SERVER_TEXT_1 = """[  5]   4.90-5.00   sec   256 KBytes  9.0 Mbits/sec
[  5]   5.00-5.10   sec   256 KBytes  21.0 Mbits/sec
[  5]   5.10-5.20   sec     0.00 Bytes  0.00 bits/sec
[  5]   0.00-20.00  sec  69.1 MBytes  28.5 Mbits/sec                  receiver
"""


class ServerTextTest(unittest.TestCase):
    def test_sum_lines_when_several_streams(self):
        js = {"start": {"test_start": {"reverse": 0}}, "server_output_text": SERVER_TEXT_4}
        self.assertEqual(throughput.samples(js, discard_s=5.0), [30.0, 1500.0])

    def test_stream_lines_when_one_stream_and_the_total_left_out(self):
        js = {"start": {"test_start": {"reverse": 0}}, "server_output_text": SERVER_TEXT_1}
        self.assertEqual(throughput.samples(js, discard_s=5.0), [21.0, 0.0])

    def test_a_gap_left_by_the_two_decimals_is_closed(self):
        # iperf3 starts each interval where the last ended; the text rounds both
        # to 0.01 s, so 5.06 then 5.07 is the same instant, not missing data
        text = """[SUM]   4.61-5.06   sec   2 MBytes  18.0 Mbits/sec
[SUM]   5.07-5.50   sec   2 MBytes  24.0 Mbits/sec
[SUM]   5.50-6.10   sec   2 MBytes  30.0 Mbits/sec
"""
        js = {"start": {"test_start": {"reverse": 0}}, "server_output_text": text}
        self.assertEqual(throughput.intervals(js)[1][0], 5.06)
        self.assertEqual(len(throughput.windows(js, width=1.0, discard_s=5.0)), 1)


class WindowTest(unittest.TestCase):
    def test_tenth_of_a_second_intervals_become_one_second_windows(self):
        # 20 intervals of 0.1 s after the discard: 10 at 0 (a stall), 10 at 60
        js = iperf([5] * 50 + [0] * 10 + [60] * 10, step=0.1)
        self.assertEqual(throughput.windows(js, width=1.0, discard_s=5.0), [0.0, 60.0])

    def test_a_partial_last_window_is_left_out(self):
        js = iperf([5] * 50 + [10] * 15, step=0.1)
        self.assertEqual(throughput.windows(js, width=1.0, discard_s=5.0), [10.0])

    def test_uneven_intervals_are_weighted_by_their_time(self):
        js = {"intervals": [{"sum": {"start": 5.0, "end": 5.5, "bits_per_second": 10e6}},
                            {"sum": {"start": 5.5, "end": 6.0, "bits_per_second": 30e6}}]}
        self.assertEqual(throughput.windows(js, width=1.0, discard_s=5.0), [20.0])


class SummarizeTest(unittest.TestCase):
    def test_the_first_ten_seconds_are_left_out_by_default(self):
        # the uplink reaches its level in 8-10 s in every run (2026-09-28 campaign)
        d = run_dir([(1, "ul1", iperf([0] * 10 + [30, 30]), 0, 12, 0, Z, Z)])
        s = throughput.summarize(d)
        self.assertEqual(s["discard_s"], 10.0)
        self.assertEqual(s["combinations"]["ul1"]["run_means"], [30.0])

    def test_combination_stats_use_lib_stats(self):
        a = iperf([0] * 5 + [10, 20, 30])
        b = iperf([0] * 5 + [40, 50])
        d = run_dir([(1, "dl1", a, 0, 8, 0, Z, Z), (2, "dl1", b, 10, 17, 0, Z, Z)])
        s = throughput.summarize(d, discard_s=5.0)["combinations"]["dl1"]
        self.assertEqual(s["samples"], summary([10.0, 20.0, 30.0, 40.0, 50.0]))
        self.assertEqual(s["run_means"], [20.0, 45.0])
        self.assertEqual(s["spread"], [20.0, 45.0])
        self.assertEqual(s["n_runs"], 2)

    def test_representative_is_the_run_closest_to_the_median_of_run_means(self):
        runs = [(1, "ul1", iperf([0] * 5 + [10]), 0, 6, 0, Z, Z),
                (2, "ul1", iperf([0] * 5 + [30]), 10, 16, 0, Z, Z),
                (3, "ul1", iperf([0] * 5 + [50]), 20, 26, 0, Z, Z)]
        self.assertEqual(throughput.summarize(run_dir(runs), discard_s=5.0)["combinations"]["ul1"]["representative"], 2)

    def test_a_run_without_windows_is_not_the_representative(self):
        runs = [(1, "ul4", iperf([0] * 5 + [10]), 0, 6, 0, Z, Z),
                (2, "ul4", iperf([0] * 5), 10, 15, 0, Z, Z),
                (3, "ul4", iperf([0] * 5 + [30]), 20, 26, 0, Z, Z)]
        s = throughput.summarize(run_dir(runs), discard_s=5.0)["combinations"]["ul4"]
        self.assertIn(s["representative"], (1, 3))
        self.assertEqual(s["run_means"], [10.0, 30.0])

    def test_a_failed_run_is_listed_and_left_out(self):
        good = iperf([0] * 5 + [10])
        bad = {"error": "unable to connect to server"}
        cut = iperf([0] * 5 + [99])
        d = run_dir([(1, "dl4", good, 0, 6, 0, Z, Z), (2, "dl4", bad, 10, 11, 1, Z, Z), (3, "dl4", cut, 20, 26, 124, Z, Z)])
        s = throughput.summarize(d, discard_s=5.0)["combinations"]["dl4"]
        self.assertEqual(s["failed"], [2, 3])
        self.assertEqual(s["n_runs"], 1)
        self.assertEqual(s["samples"]["n"], 1)

    def test_link_bytes_next_to_the_tool_bytes(self):
        js = iperf([0] * 5 + [8], sent=1_000_000)   # received 6 MB over 6 s at the mbit values
        d = run_dir([(1, "ul1", js, 0, 6, 0, (100, 200, 1, 2), (1_100, 1_050_200, 10, 20))])
        run = throughput.summarize(d, discard_s=5.0)["combinations"]["ul1"]["runs"][0]
        self.assertEqual(run["link_bytes"], 1_000 + 1_050_000)
        self.assertEqual(run["tool_bytes"], js["end"]["sum_received"]["bytes"])

    def test_the_ue_counters_wrap_at_32_bits(self):
        # the Pi 3B+'s lan78xx exposes 32-bit byte counters: 4 GiB wraps to 0
        js = iperf([0] * 5 + [8])
        before, after = (2 ** 32 - 1_000, 500, 1, 2), (4_000, 1_500, 10, 20)
        d = run_dir([(1, "dl1", js, 0, 6, 0, before, after)])
        run = throughput.summarize(d, discard_s=5.0)["combinations"]["dl1"]["runs"][0]
        self.assertEqual(run["link_bytes"], 5_000 + 1_000)

    def test_worker_counters_interpolated_at_the_run_edges(self):
        # samples every 3 s; the run starts and ends between two of them
        rows = [(10, 100, 1000, 1, 10), (13, 400, 5000, 4, 50), (16, 700, 9000, 7, 90)]
        d = run_dir([(1, "dl1", iperf([0] * 5 + [1]), 11.5, 14.5, 0, Z, Z)], worker_rows=rows)
        run = throughput.summarize(d, discard_s=5.0)["combinations"]["dl1"]["runs"][0]
        self.assertEqual(run["ran_nic"], {"rx_bytes": 300, "tx_bytes": 4000, "rx_packets": 3, "tx_packets": 40})

    def test_worker_counters_inside_the_run_window_only(self):
        rows = [(5, 0, 0, 0, 0), (10, 100, 1000, 1, 10), (13, 400, 5000, 4, 50), (16, 700, 9000, 7, 90), (30, 9999, 9999, 99, 99)]
        d = run_dir([(1, "dl1", iperf([0] * 5 + [1]), 10, 16, 0, Z, Z)], worker_rows=rows)
        run = throughput.summarize(d, discard_s=5.0)["combinations"]["dl1"]["runs"][0]
        self.assertEqual(run["ran_nic"], {"rx_bytes": 600, "tx_bytes": 8000, "rx_packets": 6, "tx_packets": 80})

    def test_sender_congestion_control_per_direction(self):
        d = run_dir([(1, "dl1", iperf([0] * 5 + [1]), 0, 6, 0, Z, Z), (2, "ul1", iperf([0] * 5 + [1]), 10, 16, 0, Z, Z)],
                    ue={"ue_tcp_cc": "reno", "server_tcp_cc": "bbr"})
        c = throughput.summarize(d, discard_s=5.0)["combinations"]
        self.assertEqual((c["dl1"]["sender_tcp_cc"], c["ul1"]["sender_tcp_cc"]), ("bbr", "reno"))

    def test_a_run_cut_before_its_meta_is_left_out(self):
        d = run_dir([(1, "dl1", iperf([0] * 5 + [1]), 0, 6, 0, Z, Z)])
        with open(os.path.join(d, "raw", "ue", "1", "runs", "2-dl4.json"), "w") as fh:
            fh.write("{")
        self.assertNotIn("dl4", throughput.summarize(d, discard_s=5.0)["combinations"])

    def test_a_run_cut_in_one_attempt_and_redone_in_the_next_counts_once(self):
        d = run_dir([(1, "dl1", iperf([0] * 5 + [10]), 0, 6, 0, Z, Z)])
        with open(os.path.join(d, "raw", "ue", "1", "runs", "2-dl1.json"), "w") as fh:
            fh.write("{")                                                       # cut in attempt 1
        r2 = os.path.join(d, "raw", "ue", "2", "runs")
        os.makedirs(r2)
        with open(os.path.join(r2, "2-dl1.json"), "w") as fh:
            json.dump(iperf([0] * 5 + [30]), fh)
        with open(os.path.join(r2, "2-dl1.meta"), "w") as fh:
            fh.write("start=100 end=106 rc=0 if_before=0,0,0,0 if_after=0,0,0,0\n")
        c = throughput.summarize(d, discard_s=5.0)["combinations"]["dl1"]
        self.assertEqual(c["run_means"], [10.0, 30.0])

    def test_retransmissions_seen_at_br_ran_per_direction(self):
        def tcp(src, dst, seq, n):
            hdr = struct.pack("!HHIIBBHHH", 5201, 40000, seq, 0, 0x50, 0x18, 0, 0, 0)
            return frame(ipv4(src, dst, 6, hdr + b"x" * n))
        pkts = [(1.0, tcp(TARGET, UE, 1000, 100)), (1.1, tcp(TARGET, UE, 1100, 100)),
                (1.2, tcp(TARGET, UE, 1000, 100)),                               # sent again: retransmitted
                (1.3, tcp(UE, TARGET, 5000, 0)),                                 # a pure ACK
                (9.0, tcp(TARGET, UE, 1200, 100))]                               # after the run
        d = run_dir([(1, "dl1", iperf([0] * 5 + [1]), 0.5, 6, 0, Z, Z)])
        os.makedirs(os.path.join(d, "raw", "worker", "1"), exist_ok=True)
        os.replace(pcap(pkts), os.path.join(d, "raw", "worker", "1", "br-ran.pcap"))
        run = throughput.summarize(d, discard_s=5.0)["combinations"]["dl1"]["runs"][0]
        self.assertEqual(run["br_ran"]["dl"], {"segments": 3, "payload_bytes": 300, "retransmitted": 1})
        self.assertEqual(run["br_ran"]["ul"], {"segments": 1, "payload_bytes": 0, "retransmitted": 0})

    def test_a_sequence_number_that_wraps_is_not_a_retransmission(self):
        # the 32-bit sequence starts at random: a run of ~1 GB wraps it about
        # one time in four, after which every segment is numerically "older"
        def tcp(seq, n):
            hdr = struct.pack("!HHIIBBHHH", 5201, 40000, seq, 0, 0x50, 0x18, 0, 0, 0)
            return frame(ipv4(TARGET, UE, 6, hdr + b"x" * n))
        top = 2 ** 32 - 100
        pkts = [(1.0, tcp(top, 100)), (1.1, tcp(0, 100)), (1.2, tcp(100, 100)),
                (1.3, tcp(0, 100))]                                              # sent again after the wrap
        d = run_dir([(1, "dl1", iperf([0] * 5 + [1]), 0.5, 6, 0, Z, Z)])
        os.makedirs(os.path.join(d, "raw", "worker", "1"), exist_ok=True)
        os.replace(pcap(pkts), os.path.join(d, "raw", "worker", "1", "br-ran.pcap"))
        run = throughput.summarize(d, discard_s=5.0)["combinations"]["dl1"]["runs"][0]
        self.assertEqual(run["br_ran"]["dl"]["retransmitted"], 1)

    def test_outputs_written(self):
        d = run_dir([(1, "dl1", iperf([0] * 5 + [1, 2]), 0, 7, 0, Z, Z)])
        throughput.write(d, throughput.summarize(d, discard_s=5.0))
        for f in ("summary.json", "summary.md", "series-dl1.csv"):
            self.assertTrue(os.path.exists(os.path.join(d, f)), f)


if __name__ == "__main__":
    unittest.main()
