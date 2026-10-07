import unittest

from app.services.ran_chain import attach_verdict, build_chain, detach_verdict
from tests.test_ran_chain import SERVING

READY_PHY = {"ready": True, "deleting": False, "phy": True}
READY_NO_PHY = {"ready": True, "deleting": False, "phy": False}
ATTACHED = {"br_exists": True, "nic_on_bridge": True, "amf_veth": True, "nad_exists": True, "amf_pods": [READY_PHY]}
DETACHED = {"br_exists": False, "nic_on_bridge": False, "amf_veth": False, "nad_exists": False, "amf_pods": [READY_NO_PHY]}


class VerdictTest(unittest.TestCase):
    def test_attached(self):
        self.assertEqual(attach_verdict(ATTACHED), (True, "br-ran carries the RAN adapter and the AMF; the AMF has its RAN interface"))

    def test_attach_not_done_names_what_is_missing(self):
        ok, msg = attach_verdict({**ATTACHED, "amf_veth": False})
        self.assertFalse(ok)
        self.assertIn("the AMF is not on br-ran", msg)

    def test_an_amf_still_rolling_is_not_a_verdict(self):
        for pods in ([READY_PHY, {**READY_NO_PHY, "deleting": True}], [{**READY_PHY, "ready": False}], []):
            ok, msg = attach_verdict({**ATTACHED, "amf_pods": pods})
            self.assertIsNone(ok, pods)
            self.assertIn("AMF", msg)
            ok, _ = detach_verdict({**DETACHED, "amf_pods": pods})
            self.assertIsNone(ok, pods)

    def test_something_concretely_missing_fails_even_while_the_amf_rolls(self):
        ok, msg = attach_verdict({**ATTACHED, "br_exists": False, "amf_pods": []})
        self.assertFalse(ok)
        self.assertIn("br-ran is missing", msg)

    def test_detached(self):
        self.assertEqual(detach_verdict(DETACHED), (True, "br-ran is gone and the AMF runs without its RAN interface"))

    def test_detach_reads_the_running_pod_not_the_old_one(self):
        ok, msg = detach_verdict({**DETACHED, "amf_pods": [READY_PHY]})
        self.assertFalse(ok)
        self.assertIn("RAN interface", msg)

    def test_detach_with_the_bridge_left(self):
        ok, msg = detach_verdict({**DETACHED, "br_exists": True})
        self.assertFalse(ok)
        self.assertIn("br-ran", msg)


class DetachedChainTest(unittest.TestCase):
    def test_detached_on_purpose_offers_attach(self):
        c = build_chain({**SERVING, "intent_attached": False, "bridge_exists": False, "nic_on_bridge": False,
                         "amf_attached": False, "gnb_connected": False, "upf_route": False, "ues": 0, "pdu": 0})
        self.assertEqual(c["state"], "detached")
        self.assertIsNone(c["first_broken"])
        bridge = c["links"][1]
        self.assertEqual((bridge["state"], bridge["fix"]), ("idle", {"piece": "ran_attach"}))
        self.assertEqual([l["state"] for l in c["links"][2:]], ["idle"] * 3)

    def test_attached_but_bridge_missing_offers_attach_not_a_worker_restart(self):
        c = build_chain({**SERVING, "bridge_exists": False, "nic_on_bridge": False, "bridge_ports": []})
        self.assertEqual(c["first_broken"], "bridge")
        self.assertEqual(c["links"][1]["fix"], {"piece": "ran_attach"})

    def test_bridge_without_the_adapter_still_needs_the_host(self):
        c = build_chain({**SERVING, "bridge_exists": True, "nic_on_bridge": False, "bridge_ports": ["patch-ran-n2"]})
        self.assertEqual(c["links"][1]["fix"], {"cli": "kelt ran enp88s0"})

    def test_intent_defaults_to_attached(self):
        self.assertEqual(build_chain(SERVING)["state"], "serving")


class AttachmentFactsTest(unittest.TestCase):
    def pod(self, phy, ready=True, deleting=False):
        from types import SimpleNamespace as NS
        nets = '[{"name": "n2-static"}' + (', {"name": "n2-ran", "interface": "n2ran"}' if phy else "") + "]"
        return NS(metadata=NS(annotations={"k8s.v1.cni.cncf.io/networks": nets}, deletion_timestamp="t" if deleting else None),
                  status=NS(phase="Running", container_statuses=[NS(ready=ready)]))

    def test_reads_the_running_amf_pods(self):
        from types import SimpleNamespace as NS
        from unittest import mock
        from app.services.ran_service import RanService
        k8s = NS(core=mock.Mock())
        k8s.core.list_namespaced_pod.return_value = NS(items=[self.pod(True), self.pod(False, deleting=True)])
        svc = RanService(k8s)
        with mock.patch.object(svc, "_br_ran_exists", return_value=True), \
             mock.patch.object(svc, "_br_ran_ports", return_value=["enp0s9", "patch-ran-n2", "veth1"]), \
             mock.patch.object(svc, "_ran_iface", return_value="enp0s9"), \
             mock.patch.object(svc, "_nad_exists", return_value=True):
            f = svc.attachment_facts()
        self.assertEqual(f, {"br_exists": True, "nic_on_bridge": True, "amf_veth": True, "nad_exists": True,
                             "amf_pods": [{"ready": True, "deleting": False, "phy": True},
                                          {"ready": True, "deleting": True, "phy": False}]})

    def test_finished_or_evicted_amf_pods_are_ignored(self):
        from types import SimpleNamespace as NS
        from unittest import mock
        from app.services.ran_service import RanService
        gone = self.pod(True)
        gone.status.phase = "Failed"
        k8s = NS(core=mock.Mock())
        k8s.core.list_namespaced_pod.return_value = NS(items=[self.pod(True), gone])
        self.assertEqual(RanService(k8s)._amf_pods(), [{"ready": True, "deleting": False, "phy": True}])

    def test_the_pieces_router_offers_both_checks(self):
        from app.routers.pieces import _checks
        self.assertLessEqual({"ran_link_up", "ran_attached", "ran_detached"}, set(_checks(k8s=None)))


class UnfinishedDetachTest(unittest.TestCase):
    def test_intent_detached_but_bridge_still_there_offers_detach_again(self):
        c = build_chain({**SERVING, "intent_attached": False, "bridge_exists": True, "amf_attached": True})
        self.assertEqual((c["state"], c["first_broken"]), ("broken", "bridge"))
        bridge = c["links"][1]
        self.assertEqual((bridge["state"], bridge["fix"]), ("bad", {"piece": "ran_detach"}))
        self.assertIn("not finished", bridge["verdict"])
        self.assertNotIn("removed", bridge["value"])
