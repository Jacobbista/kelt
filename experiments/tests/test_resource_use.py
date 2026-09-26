import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "resource-use"))
import resource_use  # noqa: E402

GROUPS = {"core": "5g", "exposure": "positioning|camara", "identity": "iam", "apps": "mec"}


def fixture(name):
    with open(os.path.join(HERE, "fixtures", name)) as fh:
        return json.load(fh)


def rows():
    return {r["pod"]: r for r in resource_use.per_pod(fixture("prom_cpu_range.json"), fixture("prom_mem_range.json"))}


class GroupTest(unittest.TestCase):
    def test_groups(self):
        def g(ns, pod):
            return resource_use.group_of(ns, pod, GROUPS, "measurement-server")
        self.assertEqual(g("5g", "upf-cloud-abc"), "core")
        self.assertEqual(g("camara", "camara-gateway-1"), "exposure")
        self.assertEqual(g("iam", "keycloak-0"), "identity")
        self.assertEqual(g("mec", "measurement-server-1"), "mec-server")
        self.assertEqual(g("mec", "face-recognition-1"), "other")

    def test_probes_are_diagnostic_wherever_they_live(self):
        def g(ns, pod):
            return resource_use.group_of(ns, pod, GROUPS, "measurement-server", probes=("netshoot",))
        self.assertEqual(g("5g", "netshoot-6b4f66b747-68nf9"), "diagnostic")
        self.assertEqual(g("mec", "netshoot-abc-1"), "diagnostic")
        self.assertEqual(g("5g", "netshootx-1"), "core")


class PerPodTest(unittest.TestCase):
    def test_mean_and_peak(self):
        upf = rows()["upf-cloud-abc"]
        self.assertEqual(upf["cpu_mcores"]["mean"], 200.0)
        self.assertEqual(upf["cpu_mcores"]["max"], 300.0)
        self.assertEqual(upf["mem_mib"]["max"], 66.0)

    def test_pod_without_samples_has_no_numbers(self):
        amf = rows()["amf-restarted"]
        self.assertEqual(amf["cpu_mcores"]["n"], 0)
        self.assertIsNone(amf["cpu_mcores"]["mean"])


class WindowsTest(unittest.TestCase):
    def test_unclosed_windows_are_skipped(self):
        ok, skipped = resource_use.collect([
            {"label": "a", "start_utc": "2026-09-26T10:00:00Z", "end_utc": "2026-09-26T10:05:00Z"},
            {"label": "b", "start_utc": "2026-09-26T11:00:00Z", "end_utc": None}])
        self.assertEqual([w["label"] for w in ok], ["a"])
        self.assertEqual(skipped, ["b"])
