import json
import unittest
from unittest import mock

from app.services.ran_service import RanService


def link(flags, operstate):
    return json.dumps([{"ifname": "enp0s9", "flags": flags, "operstate": operstate}])


class NicStateTest(unittest.TestCase):
    """What the worker's RAN NIC looks like, from `ip -j link show` (live shapes)."""

    def state(self, out):
        svc = RanService(k8s=None)
        with mock.patch.object(svc, "_ssh", return_value=out):
            return svc._nic_state("enp0s9")

    def test_up(self):
        self.assertEqual(self.state(link(["BROADCAST", "MULTICAST", "UP", "LOWER_UP"], "UP")), "up")

    def test_admin_down(self):
        # After a reboot with networkd no longer managing the NIC (2026-09-26).
        self.assertEqual(self.state(link(["BROADCAST", "MULTICAST"], "DOWN")), "down")

    def test_no_carrier(self):
        self.assertEqual(self.state(link(["NO-CARRIER", "BROADCAST", "MULTICAST", "UP"], "DOWN")), "no_carrier")

    def test_missing(self):
        self.assertEqual(self.state("[]"), "missing")

    def test_no_name(self):
        self.assertEqual(RanService(k8s=None)._nic_state(""), "missing")

