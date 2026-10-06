import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "resource-use"))
import footprint  # noqa: E402

GROUPS = {"core": "5g", "exposure": "positioning|camara", "identity": "iam", "apps": "mec"}


class GroupTest(unittest.TestCase):
    def test_groups(self):
        def g(ns, pod):
            return footprint.group_of(ns, pod, GROUPS, "measurement-server")
        self.assertEqual(g("5g", "upf-cloud-abc"), "core")
        self.assertEqual(g("camara", "camara-gateway-1"), "exposure")
        self.assertEqual(g("iam", "keycloak-0"), "identity")
        self.assertEqual(g("mec", "measurement-server-1"), "mec-server")
        self.assertEqual(g("mec", "face-recognition-1"), "edge-apps")
        self.assertEqual(g("monitoring", "prometheus-0"), "platform")
        self.assertEqual(g("kube-system", "coredns-1"), "platform")

    def test_probes_are_diagnostic_wherever_they_live(self):
        def g(ns, pod):
            return footprint.group_of(ns, pod, GROUPS, "measurement-server", probes=("netshoot",))
        self.assertEqual(g("5g", "netshoot-6b4f66b747-68nf9"), "diagnostic")
        self.assertEqual(g("5g", "netshootx-1"), "core")


# worker: 4 CPUs; per second 400 jiffies, 300 then 200 idle -> 1000 then 2000 m;
# memory used (4000000 - 3000000) kB. upf: 0.1 s then 0.3 s of CPU per second.
WORKER = """N 100.0 4 1000 800 4000000 3000000
P 100.0 u-upf 1000000 104857600
P 100.0 u-amf 0 1048576
N 101.0 4 1400 1100 4000000 3000000
P 101.0 u-upf 1100000 104857600
P 101.0 u-amf 5000 1048576
N 102.0 4 1800 1300 4000000 2976000
P 102.0 u-upf 1400000 209715200
P 102.0 u-amf 10000 1048576
"""
HOST = """N 100.2 8 1000 1000 8000000 6000000
N 101.2 8 1800 1600 8000000 6000000
"""
PODS = "u-upf\t5g\tupf-cloud-1\nu-amf\t5g\tamf-1\n"


def run(files=None, pods=PODS):
    d = tempfile.mkdtemp()
    f = os.path.join(d, "raw", "footprint", "1")
    os.makedirs(f)
    for name, text in (files or {"worker": WORKER, "host": HOST}).items():
        with open(os.path.join(f, f"{name}.txt"), "w") as fh:
            fh.write(text)
    with open(os.path.join(f, "pods.tsv"), "w") as fh:
        fh.write(pods)
    return d


class RatesTest(unittest.TestCase):
    def test_pod_cpu_is_usage_over_time_mean_and_one_second_peak(self):
        s = footprint.summarize(run(), {"all": [(99.0, 103.0)]}, GROUPS)["all"]
        upf = next(p for p in s["pods"] if p["pod"] == "upf-cloud-1")
        self.assertEqual((upf["cpu_mcores"]["mean"], upf["cpu_mcores"]["max"]), (200.0, 300.0))
        self.assertEqual(upf["mem_mib"]["max"], 200.0)
        self.assertEqual(upf["node"], "worker")

    def test_a_machine_is_cores_in_use_and_memory_in_use(self):
        s = footprint.summarize(run(), {"all": [(99.0, 103.0)]}, GROUPS)["all"]
        self.assertEqual(s["nodes"]["worker"]["cpu_mcores"]["max"], 2000.0)
        self.assertEqual(s["nodes"]["worker"]["cpu_mcores"]["mean"], 1500.0)
        self.assertEqual(s["nodes"]["worker"]["mem_mib"]["max"], round(1024000 / 1024, 3))
        self.assertEqual(s["nodes"]["host"]["cpu_mcores"]["mean"], 2000.0)

    def test_each_vm_process_on_the_host(self):
        # 100 ticks per second: 50 then 150 ticks a second -> 500 then 1500 m
        host = """N 100.0 8 1000 1000 8000000 6000000
V 100.0 worker-5g-k8s-testbed 1000 100 9437184
N 101.0 8 1800 1600 8000000 6000000
V 101.0 worker-5g-k8s-testbed 1050 100 9437184
N 102.0 8 2600 2200 8000000 6000000
V 102.0 worker-5g-k8s-testbed 1200 100 10485760
"""
        s = footprint.summarize(run({"host": host}), {"all": [(99.0, 103.0)]}, GROUPS)["all"]
        vm = s["nodes"]["vm-process worker-5g-k8s-testbed"]
        self.assertEqual((vm["cpu_mcores"]["mean"], vm["cpu_mcores"]["max"]), (1000.0, 1500.0))
        self.assertEqual(vm["mem_mib"]["max"], 10240.0)

    def test_the_vm_processes_summed_second_by_second(self):
        # two VMs whose peaks fall in different seconds: the total's peak is
        # the busiest second (1500 + 500), not the sum of the two peaks (2500)
        host = """N 100.0 8 1000 1000 8000000 6000000
V 100.0 master 0 100 1048576
V 100.0 worker 0 100 2097152
N 101.0 8 1800 1600 8000000 6000000
V 101.0 master 150 100 1048576
V 101.0 worker 50 100 2097152
N 102.0 8 2600 2200 8000000 6000000
V 102.0 master 200 100 1048576
V 102.0 worker 150 100 2097152
"""
        s = footprint.summarize(run({"host": host}), {"all": [(100.0, 102.0)]}, GROUPS)["all"]
        total = s["nodes"]["vm-process total"]
        self.assertEqual((total["cpu_mcores"]["mean"], total["cpu_mcores"]["max"]), (1750.0, 2000.0))
        self.assertEqual(total["mem_mib"]["max"], 3072.0)

    def test_the_vms_from_inside_summed_second_by_second(self):
        # memory in use inside each VM (total minus available, the guest's
        # cache left out) and its CPU, master + worker; the host is not a VM
        master = """N 100.0 2 1000 900 2000000 1500000
N 101.0 2 1200 1050 2000000 1500000
N 102.0 2 1400 1200 2000000 1488000
"""
        d = run({"master": master, "worker": WORKER, "host": HOST})
        total = footprint.summarize(d, {"all": [(100.0, 102.0)]}, GROUPS)["all"]["nodes"]["vms inside total"]
        # CPU: master 50 then 50 busy jiffies a second (500 m), worker 1000 then 2000 m
        self.assertEqual((total["cpu_mcores"]["mean"], total["cpu_mcores"]["max"]), (2000.0, 2500.0))
        # memory at the grid points: 500000 + 1000000 kB (the next samples come at 102 s)
        self.assertEqual(total["mem_mib"]["max"], round(1500000 / 1024, 3))

    def test_each_machine_records_its_size(self):
        # what the machine itself reports: CPUs and MemTotal (a VM shows a little
        # less than the RAM assigned to it: the guest kernel keeps some)
        s = footprint.summarize(run(), {"all": [(99.0, 103.0)]}, GROUPS)["all"]
        self.assertEqual((s["nodes"]["worker"]["cpus"], s["nodes"]["worker"]["mem_total_mib"]), (4, 3906.25))
        self.assertEqual(s["nodes"]["host"]["cpus"], 8)

    def test_a_group_is_the_sum_of_its_pods_second_by_second(self):
        s = footprint.summarize(run(), {"all": [(99.0, 103.0)]}, GROUPS)["all"]
        self.assertEqual(s["groups"]["core"]["cpu_mcores"]["max"], 305.0)

    def test_a_group_across_two_machines_is_summed_on_one_grid(self):
        # the samplers of two VMs are not aligned: the master's pod is sampled
        # 0.4 s after the worker's; the group is 100 + 50 = 150 m every second
        master = """N 100.4 2 1000 900 2000000 1000000
P 100.4 u-smf 0 1048576
N 101.4 2 1200 1050 2000000 1000000
P 101.4 u-smf 50000 1048576
N 102.4 2 1400 1200 2000000 1000000
P 102.4 u-smf 100000 1048576
N 103.4 2 1600 1350 2000000 1000000
P 103.4 u-smf 150000 1048576
"""
        worker = """N 100.0 4 1000 800 4000000 3000000
P 100.0 u-upf 1000000 104857600
N 101.0 4 1400 1100 4000000 3000000
P 101.0 u-upf 1100000 104857600
N 102.0 4 1800 1300 4000000 3000000
P 102.0 u-upf 1200000 104857600
N 103.0 4 2200 1500 4000000 3000000
P 103.0 u-upf 1300000 104857600
"""
        d = run({"master": master, "worker": worker}, pods=PODS + "u-smf\t5g\tsmf-1\n")
        g = footprint.summarize(d, {"all": [(100.0, 103.0)]}, GROUPS)["all"]["groups"]["core"]["cpu_mcores"]
        self.assertEqual((g["mean"], g["max"]), (150.0, 150.0))

    def test_only_the_samples_inside_the_windows(self):
        s = footprint.summarize(run(), {"second": [(100.5, 101.5)]}, GROUPS)["second"]
        upf = next(p for p in s["pods"] if p["pod"] == "upf-cloud-1")
        self.assertEqual(upf["cpu_mcores"]["n"], 1)
        self.assertEqual(upf["cpu_mcores"]["mean"], 100.0)

    def test_a_pod_missing_from_the_map_is_kept_by_uid(self):
        s = footprint.summarize(run(pods="u-upf\t5g\tupf-cloud-1\n"), {"all": [(99.0, 103.0)]}, GROUPS)["all"]
        self.assertIn("u-amf", [p["pod"] for p in s["pods"]])


class WindowsTest(unittest.TestCase):
    def meta(self, d, name, start, end):
        r = os.path.join(d, "raw", "ue", "1", "runs")
        os.makedirs(r, exist_ok=True)
        with open(os.path.join(r, name + ".meta"), "w") as fh:
            fh.write(f"start={start} end={end} rc=0 if_before=0,0,0,0 if_after=0,0,0,0\n")

    def test_one_window_per_run_grouped_by_condition_after_the_discard(self):
        d = tempfile.mkdtemp()
        self.meta(d, "1-dl1", 100, 160)
        self.meta(d, "5-dl1", 300, 360)
        self.meta(d, "3-ul1", 200, 260)
        self.assertEqual(footprint.condition_windows(d, discard_s=10),
                         {"dl1": [(110.0, 160.0), (310.0, 360.0)], "ul1": [(210.0, 260.0)]})

    def test_rtt_runs_keep_their_whole_window(self):
        d = tempfile.mkdtemp()
        self.meta(d, "1-idle", 100, 400)
        self.meta(d, "4-load", 500, 820)
        self.assertEqual(footprint.condition_windows(d, discard_s=0),
                         {"idle": [(100.0, 400.0)], "load": [(500.0, 820.0)]})

    def test_a_run_with_a_ping_is_the_pings_own_span(self):
        # the load starts 10 s before the ping and ends after it; the condition
        # is the time the ping measured (its -D timestamps), not the load tool's
        # start and end (2026-10-05: iperf3 ending took 0.8 of a core for 1 s)
        d = tempfile.mkdtemp()
        self.meta(d, "4-load", 500, 832)
        with open(os.path.join(d, "raw", "ue", "1", "runs", "4-load.ping"), "w") as fh:
            fh.write("PING x (x) 56(84) bytes of data.\n"
                     "[510.25] 64 bytes from x: icmp_seq=1 ttl=63 time=111 ms\n"
                     "[809.75] 64 bytes from x: icmp_seq=3000 ttl=63 time=120 ms\n\n"
                     "--- x ping statistics ---\n")
        self.assertEqual(footprint.condition_windows(d, discard_s=0), {"load": [(510.25, 809.75)]})


if __name__ == "__main__":
    unittest.main()
