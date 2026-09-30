import json
import os
import socket
import struct
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from lib.stats import summary  # noqa: E402
from network import rtt  # noqa: E402

TARGET = "10.45.0.1"
UE = "10.45.0.7"
OTHER_UE = "10.45.0.9"


def ipv4(src, dst, proto, payload):
    hdr = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(payload), 0, 0, 64, proto, 0,
                      socket.inet_aton(src), socket.inet_aton(dst))
    return hdr + payload


def icmp(kind, ident, seq):
    return struct.pack("!BBHHH", kind, 0, 0, ident, seq) + b"x" * 8


def gtpu(inner, ext=True):
    if ext:   # E flag + PDU session container (one 4-byte extension, then none)
        body = struct.pack("!HBB", 0, 0, 0x85) + bytes([1, 0x10, 0x01, 0x00]) + inner
        return struct.pack("!BBHI", 0x34, 0xFF, len(body), 0x1234) + body
    return struct.pack("!BBHI", 0x30, 0xFF, len(inner), 0x1234) + inner


def frame(inner, ext=True):
    udp_payload = gtpu(inner, ext)
    udp = struct.pack("!HHHH", 2152, 2152, 8 + len(udp_payload), 0) + udp_payload
    return b"\x00" * 12 + b"\x08\x00" + ipv4("192.168.6.101", "192.168.6.160", 17, udp)


def pcap(packets):
    """packets: list of (time, frame bytes)."""
    fd, path = tempfile.mkstemp(suffix=".pcap")
    with os.fdopen(fd, "wb") as fh:
        # nanosecond pcap, as tcpdump --time-stamp-precision=nano writes
        fh.write(struct.pack("<IHHiIII", 0xA1B23C4D, 2, 4, 0, 0, 65535, 1))
        for t, f in packets:
            fh.write(struct.pack("<IIII", int(t), int(round((t % 1) * 1e9)), len(f), len(f)) + f)
    return path


def echo(t_req, t_rep, ident, seq, src=UE, ext=True):
    out = [(t_req, frame(ipv4(src, TARGET, 1, icmp(8, ident, seq)), ext))]
    if t_rep is not None:
        out.append((t_rep, frame(ipv4(TARGET, src, 1, icmp(0, ident, seq)), ext)))
    return out


PING = """PING 10.45.0.1 (10.45.0.1) 56(84) bytes of data.
[1727460000.100000] 64 bytes from 10.45.0.1: icmp_seq=1 ttl=64 time=12.5 ms
[1727460000.200000] 64 bytes from 10.45.0.1: icmp_seq=2 ttl=64 time=14.0 ms
[1727460000.400000] 64 bytes from 10.45.0.1: icmp_seq=4 ttl=64 time=11.0 ms

--- 10.45.0.1 ping statistics ---
4 packets transmitted, 3 received, 25% packet loss, time 300ms
"""


class PingTest(unittest.TestCase):
    def test_parse_keeps_seq_and_counts_the_lost(self):
        rtts, sent = rtt.parse_ping(PING)
        self.assertEqual(rtts, {1: 12.5, 2: 14.0, 4: 11.0})
        self.assertEqual(sent, 4)


class GtpuTest(unittest.TestCase):
    def test_inner_packet_with_and_without_the_extension_header(self):
        inner = ipv4(UE, TARGET, 1, icmp(8, 7, 1))
        self.assertEqual(rtt.gtpu_inner(frame(inner, ext=True)), inner)
        self.assertEqual(rtt.gtpu_inner(frame(inner, ext=False)), inner)

    def test_not_gtpu(self):
        f = b"\x00" * 12 + b"\x08\x00" + ipv4("1.1.1.1", "2.2.2.2", 6, b"\x00" * 20)
        self.assertIsNone(rtt.gtpu_inner(f))


class SplitTest(unittest.TestCase):
    def test_pairs_counts_unpaired_and_foreign(self):
        pkts = []
        pkts += echo(100.000, 100.004, 7, 1)
        pkts += echo(100.100, 100.106, 7, 2)
        pkts += echo(100.200, None, 7, 3)                       # no reply seen: unpaired
        pkts += echo(100.300, 100.302, 55, 1, src=OTHER_UE)     # another UE: ignored
        pkts.append((100.4, frame(ipv4(UE, "100.100.1.1", 17, b"\x00" * 20))))   # foreign, from the UE
        pkts.append((100.5, frame(ipv4(UE, TARGET, 6, b"\x00" * 20))))            # iperf3 load: the experiment
        s = rtt.split(pcap(pkts), TARGET)
        self.assertEqual(s["ue"], UE)
        g = s["flows"][7]
        self.assertEqual({k: round(v, 3) for k, v in g["core_ms"].items()}, {1: 4.0, 2: 6.0})
        self.assertEqual(g["unpaired"], 1)
        self.assertEqual(s["foreign_packets"], 1)
        self.assertNotIn(55, s["flows"])


def run_dir(idle_ping, pcap_path=None, idle_window=(1, 2)):
    d = tempfile.mkdtemp()
    r = os.path.join(d, "raw", "ue", "1", "runs")
    os.makedirs(r)
    with open(os.path.join(r, "1-idle.ping"), "w") as fh:
        fh.write(idle_ping)
    with open(os.path.join(r, "1-idle.meta"), "w") as fh:
        fh.write(f"start={idle_window[0]} end={idle_window[1]} rc=0 if_before=0,0,0,0 if_after=0,0,0,0\n")
    with open(os.path.join(r, "2-load.ping"), "w") as fh:
        fh.write(PING)
    with open(os.path.join(r, "2-load.meta"), "w") as fh:
        fh.write("start=3 end=4 rc=0 if_before=0,0,0,0 if_after=0,0,0,0\n")
    with open(os.path.join(d, "ue.json"), "w") as fh:
        json.dump({"target": TARGET}, fh)
    if pcap_path:
        os.makedirs(os.path.join(d, "raw", "worker", "1"))
        os.replace(pcap_path, os.path.join(d, "raw", "worker", "1", "br-ran.pcap"))
    return d


class SummarizeTest(unittest.TestCase):
    def test_access_is_total_minus_core_for_the_same_seq(self):
        pkts = echo(100.000, 100.004, 7, 1) + echo(100.100, 100.106, 7, 2)   # seq 4 never captured
        d = run_dir(PING, pcap(pkts), idle_window=(99.5, 101))
        s = rtt.summarize(d)
        idle = s["conditions"]["idle"]
        self.assertEqual(idle["total"], summary([12.5, 14.0, 11.0]))
        self.assertEqual(idle["core"], summary([4.0, 6.0]))
        self.assertEqual(idle["access"], summary([8.5, 8.0]))
        self.assertEqual(idle["runs"][0]["loss"], 0.25)
        self.assertEqual(idle["pairing"]["paired"], 2)
        self.assertEqual(s["conditions"]["load"]["total"], summary([12.5, 14.0, 11.0]))
        self.assertNotIn("core", s["conditions"]["load"])

    def test_the_idle_run_takes_the_flow_inside_its_window(self):
        pkts = echo(90.0, 90.004, 3, 1)                                  # the job's ping check, before the run
        pkts += echo(100.000, 100.004, 7, 1) + echo(100.100, 100.106, 7, 2)
        pkts += echo(120.0, 120.2, 11, 1) + echo(120.1, 120.3, 11, 2)    # the load run's ping, after it
        s = rtt.summarize(run_dir(PING, pcap(pkts), idle_window=(99.5, 101)))
        self.assertEqual(s["conditions"]["idle"]["core"], summary([4.0, 6.0]))

    def test_foreign_packets_counted_inside_each_idle_run_only(self):
        pkts = [(95.0, frame(ipv4(UE, "100.100.1.1", 17, b"\x00" * 20)))]    # before the run (the launch)
        pkts += echo(100.000, 100.004, 7, 1)
        pkts.append((100.5, frame(ipv4("100.100.1.1", UE, 17, b"\x00" * 20))))  # during the run
        s = rtt.summarize(run_dir(PING, pcap(pkts), idle_window=(99.5, 101)))
        self.assertEqual(s["foreign_packets"], 2)
        self.assertEqual(s["conditions"]["idle"]["runs"][0]["foreign_packets"], 1)

    def test_foreign_bytes_per_second_inside_each_idle_run(self):
        # what can delay a ping is the foreign bytes queued ahead of it: IP
        # length 40 (20 header + 20 payload) over the run's 1.5 s window
        pkts = echo(100.000, 100.004, 7, 1)
        pkts.append((100.5, frame(ipv4("100.100.1.1", UE, 17, b"\x00" * 20))))
        run = rtt.summarize(run_dir(PING, pcap(pkts), idle_window=(99.5, 101)))["conditions"]["idle"]["runs"][0]
        self.assertEqual(run["foreign_bytes"], 40)
        self.assertEqual(run["foreign_bytes_per_s"], 26.7)

    def test_a_run_cut_before_its_meta_is_left_out(self):
        d = run_dir(PING, pcap(echo(100.0, 100.004, 7, 1)), idle_window=(99.5, 101))
        os.remove(os.path.join(d, "raw", "ue", "1", "runs", "2-load.meta"))
        s = rtt.summarize(d)
        self.assertNotIn("load", s["conditions"])

    def test_captures_of_two_attempts_compressed_or_not(self):
        import gzip
        d = run_dir(PING, pcap(echo(100.0, 100.004, 7, 1)), idle_window=(99.5, 101))
        r2 = os.path.join(d, "raw", "ue", "2", "runs")
        os.makedirs(r2)
        with open(os.path.join(r2, "3-idle.ping"), "w") as fh:
            fh.write(PING)
        with open(os.path.join(r2, "3-idle.meta"), "w") as fh:
            fh.write("start=199.5 end=201 rc=0 if_before=0,0,0,0 if_after=0,0,0,0\n")
        w2 = os.path.join(d, "raw", "worker", "2")
        os.makedirs(w2)
        with open(pcap(echo(200.0, 200.008, 9, 1)), "rb") as src, gzip.open(os.path.join(w2, "br-ran.pcap.gz"), "wb") as dst:
            dst.write(src.read())
        idle = rtt.summarize(d)["conditions"]["idle"]
        self.assertEqual(idle["core"], summary([4.0, 8.0]))
        self.assertEqual(len(idle["runs"]), 2)

    def test_without_a_capture_there_is_no_split(self):
        s = rtt.summarize(run_dir(PING))
        self.assertNotIn("core", s["conditions"]["idle"])

    def test_outputs_written(self):
        d = run_dir(PING, pcap(echo(100.0, 100.004, 7, 1)), idle_window=(99.5, 101))
        rtt.write(d, rtt.summarize(d))
        for f in ("summary.json", "summary.md", "rtt-samples.csv"):
            self.assertTrue(os.path.exists(os.path.join(d, f)), f)


if __name__ == "__main__":
    unittest.main()


def plain(inner):
    """An Ethernet frame carrying a plain IPv4 packet (the N6m veths)."""
    return b"\x00" * 12 + b"\x08\x00" + inner


def points_echo(times, ident, seq, missing=()):
    """One echo seen at the five capture points. times: point -> (request, reply)."""
    out = {}
    for p, (tq, tr) in times.items():
        if p in missing:
            continue
        wrap = plain if p in ("upf-n6m", "server") else frame
        out[p] = [(tq, wrap(ipv4(UE, TARGET, 1, icmp(8, ident, seq)))),
                  (tr, wrap(ipv4(TARGET, UE, 1, icmp(0, ident, seq))))]
    return out


def add_points(d, attempt, per_point):
    """per_point: point -> list of (time, frame); written as raw/worker/<n>/points/<point>.pcap."""
    p = os.path.join(d, "raw", "worker", str(attempt), "points")
    os.makedirs(p, exist_ok=True)
    for name, pkts in per_point.items():
        os.replace(pcap(sorted(pkts)), os.path.join(p, f"{name}.pcap"))


# request / reply times (s) of seq 1 at each point, on the worker's clock:
# worker 0.1+0.1 ms, OVS on N3 0.02+0.02, UPF 0.05+0.05, OVS on N6m 0.02+0.02, server 0.03
T = {"br-ran": (100.0, 100.00058), "br-n3": (100.0001, 100.00048), "upf-n3": (100.00012, 100.00046),
     "upf-n6m": (100.00017, 100.00041), "server": (100.00019, 100.00039)}


class SegmentsTest(unittest.TestCase):
    def test_every_part_of_the_core_from_the_five_points(self):
        d = run_dir(PING, idle_window=(99.5, 101))
        add_points(d, 1, points_echo(T, 7, 1))
        idle = rtt.summarize(d)["conditions"]["idle"]
        seg = {k: v["median"] for k, v in idle["segments"].items()}
        self.assertEqual(seg, {"worker": 0.2, "ovs_n3": 0.04, "upf": 0.1, "ovs_n6m": 0.04, "server": 0.2})
        self.assertEqual(idle["core"]["median"], 0.58)

    def test_an_echo_missing_at_one_point_has_no_segments(self):
        d = run_dir(PING, idle_window=(99.5, 101))
        pts = points_echo(T, 7, 1)
        later = {p: (a + 0.1, b + 0.1) for p, (a, b) in T.items()}
        for p, pk in points_echo(later, 7, 2, missing=("upf-n6m",)).items():
            pts[p] += pk
        add_points(d, 1, pts)
        idle = rtt.summarize(d)["conditions"]["idle"]
        self.assertEqual(idle["segments"]["upf"]["n"], 1)
        self.assertEqual(idle["core"]["n"], 2)          # br-ran saw both

    def test_the_captures_own_reports_are_not_captures(self):
        # each capture has tcpdump's report next to it (<capture>.log)
        d = run_dir(PING, pcap(echo(100.0, 100.00058, 7, 1)), idle_window=(99.5, 101))
        add_points(d, 1, points_echo(T, 7, 1))
        for p in [os.path.join(d, "raw", "worker", "1", "br-ran.pcap.log")] + \
                 [os.path.join(d, "raw", "worker", "1", "points", f"{n}.pcap.log") for n in T]:
            with open(p, "w") as fh:
                fh.write("9 packets captured\n9 packets received by filter\n0 packets dropped by kernel\n")
        idle = rtt.summarize(d)["conditions"]["idle"]
        self.assertEqual(idle["segments"]["upf"]["median"], 0.1)

    def test_sub_microsecond_parts_are_kept(self):
        # nanosecond captures: an OVS step of 2.75 us each way is 5.5 us, not 5 or 6
        t = dict(T)
        t["upf-n3"] = (100.0001 + 2.75e-6, 100.00048 - 2.75e-6)
        d = run_dir(PING, idle_window=(99.5, 101))
        add_points(d, 1, points_echo(t, 7, 1))
        seg = rtt.summarize(d)["conditions"]["idle"]["segments"]["ovs_n3"]["median"]
        self.assertAlmostEqual(seg, 0.0055, delta=0.0003)

    def test_core_is_exactly_the_sum_of_its_parts(self):
        # parts and core at the same precision (0.1 us): the identity holds per echo
        t = {p: (a + 1.23e-7 * i, b - 3.1e-7 * i) for i, (p, (a, b)) in enumerate(T.items())}
        t["br-ran"] = (T["br-ran"][0], T["br-ran"][1] + 4e-7)      # core 0.5804 ms: not a whole us
        d = run_dir(PING, idle_window=(99.5, 101))
        add_points(d, 1, points_echo(t, 7, 1))
        row = next(r for r in rtt.summarize(d)["samples"] if r["core_ms"] is not None)
        parts = sum(row[p] for p in ("worker", "ovs_n3", "upf", "ovs_n6m", "server"))
        self.assertAlmostEqual(parts, row["core_ms"], delta=0.00025)

    def test_the_load_runs_are_split_too(self):
        d = run_dir(PING, idle_window=(99.5, 101))      # the load run's window is 3..4
        pts = points_echo({p: (a - 97, b - 97) for p, (a, b) in T.items()}, 9, 1)
        add_points(d, 1, pts)
        load = rtt.summarize(d)["conditions"]["load"]
        self.assertEqual(load["core"]["median"], 0.58)
        self.assertEqual(load["segments"]["upf"]["median"], 0.1)


class NanosecondPcapTest(unittest.TestCase):
    def test_nanosecond_captures_keep_microsecond_differences(self):
        # tcpdump --time-stamp-precision=nano: magic a1b23c4d, fraction in ns
        fd, path = tempfile.mkstemp(suffix=".pcap")
        f = frame(ipv4(UE, TARGET, 1, icmp(8, 7, 1)))
        with os.fdopen(fd, "wb") as fh:
            fh.write(struct.pack("<IHHiIII", 0xA1B23C4D, 2, 4, 0, 0, 65535, 1))
            for ns in (123_456_000, 123_461_500):
                fh.write(struct.pack("<IIII", 1_790_700_000, ns, len(f), len(f)) + f)
        from network import pcap
        t = [x[0] for x in pcap.read_pcap(path)]
        self.assertAlmostEqual((t[1] - t[0]) * 1e6, 5.5, delta=0.5)


class CaptureReportTest(unittest.TestCase):
    def test_tcpdump_report_read_back(self):
        fd, path = tempfile.mkstemp(suffix=".log")
        with os.fdopen(fd, "w") as fh:
            fh.write("tcpdump: listening on br-ran, link-type EN10MB\n"
                     "1066085 packets captured\n1066101 packets received by filter\n16 packets dropped by kernel\n")
        from network import pcap
        self.assertEqual(pcap.capture_report(path), {"captured": 1066085, "received": 1066101, "dropped": 16})

    def test_no_report_is_none(self):
        from network import pcap
        self.assertIsNone(pcap.capture_report("/nonexistent/x.log"))


class WriteTest(unittest.TestCase):
    def test_samples_carry_every_part_of_the_core(self):
        d = run_dir(PING, idle_window=(99.5, 101))
        add_points(d, 1, points_echo(T, 7, 1))
        rtt.write(d, rtt.summarize(d))
        with open(os.path.join(d, "rtt-samples.csv")) as fh:
            head, first = fh.readline().strip().split(","), fh.readline().strip().split(",")
        self.assertEqual(head, ["condition", "run", "seq", "total_ms", "core_ms", "access_ms",
                                "worker", "ovs_n3", "upf", "ovs_n6m", "server"])
        self.assertEqual(dict(zip(head, first))["upf"], "0.1")
        with open(os.path.join(d, "summary.md")) as fh:
            self.assertIn("| idle | upf |", fh.read())
