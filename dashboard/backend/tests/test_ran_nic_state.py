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


class BringLinkUpTest(unittest.TestCase):
    """Bringing the link up re-runs the OVS setup that owns it (phase 04 script in
    the worker's network DaemonSet), never an ad-hoc `ip link set` on the node."""

    def run_it(self, states):
        svc = RanService(k8s=None)
        seq = iter(states)
        with mock.patch.object(svc, "_restart_ovs_ds_pod") as restart, \
             mock.patch.object(svc, "_ran_iface", return_value="enp0s9"), \
             mock.patch.object(svc, "_nic_state", side_effect=lambda i: next(seq)), \
             mock.patch("app.services.ran_service.time.sleep"):
            out = svc.bring_link_up(retries=3, delay=0)
        return out, restart

    def test_restarts_setup_and_waits_for_up(self):
        out, restart = self.run_it(["down", "down", "up"])
        restart.assert_called_once()
        self.assertEqual(out, {"ok": True, "nic_state": "up", "interface": "enp0s9"})

    def test_reports_last_state_when_it_does_not_come_up(self):
        out, _ = self.run_it(["down", "no_carrier", "no_carrier", "no_carrier"])
        self.assertEqual(out["ok"], False)
        self.assertEqual(out["nic_state"], "no_carrier")

    def test_missing_nic_is_not_touched(self):
        out, restart = self.run_it(["missing"])
        restart.assert_not_called()
        self.assertEqual(out["nic_state"], "missing")
