import unittest
from unittest import mock

from app.services.ran_service import RanService

GNB_INFO = {"items": [
    {"gnb_id": 1, "ng": {"sctp": {"peer": "[10.202.0.100]:38412"}, "setup_success": True},
     "network": {"ngap_port": 38412}, "num_connected_ues": 1},
    {"gnb_id": 333, "ng": {"sctp": {"peer": "[192.168.6.101]:60110"}, "setup_success": True},
     "network": {"ngap_port": 38412}, "num_connected_ues": 2},
]}
UE_INFO = {"items": [
    {"gnb": {"gnb_id": 333}, "pdu_sessions_count": 1},
    {"gnb": {"gnb_id": 333}, "pdu_sessions_count": 2},
    {"gnb": {"gnb_id": 1}, "pdu_sessions_count": 1},
], "pager": {"page": 0, "page_size": 100, "count": 3}}


def fake_nf(core, ns, app, port, path):
    return GNB_INFO if path.startswith("gnb-info") else UE_INFO


class PhysicalGnbTest(unittest.TestCase):
    def svc(self):
        return RanService(k8s=None)

    def test_picks_the_gnb_in_the_physical_subnet(self):
        with mock.patch("app.services.ran_service.nf_api_get", side_effect=fake_nf), \
             mock.patch("app.services.ran_service.thread_core"):
            g = self.svc()._physical_gnb("192.168.6.0/24", 38412)
        self.assertEqual(g, {"connected": True, "ip": "192.168.6.101", "gnb_id": 333, "ngap_port": 38412})

    def test_counts_only_the_physical_gnbs_ues(self):
        with mock.patch("app.services.ran_service.nf_api_get", side_effect=fake_nf), \
             mock.patch("app.services.ran_service.thread_core"):
            self.assertEqual(self.svc()._ue_counts(333), {"ues": 2, "pdu": 3})

    def test_amf_unreachable_means_not_connected(self):
        with mock.patch("app.services.ran_service.nf_api_get", return_value={}), \
             mock.patch("app.services.ran_service.thread_core"):
            g = self.svc()._physical_gnb("192.168.6.0/24", 38412)
            counts = self.svc()._ue_counts(None)
        self.assertEqual(g, {"connected": False, "ip": None, "gnb_id": None, "ngap_port": 38412})
        self.assertEqual(counts, {"ues": 0, "pdu": 0})

    def test_gnb_seen_without_setup_is_not_connected(self):
        info = {"items": [{"gnb_id": 333, "ng": {"sctp": {"peer": "[192.168.6.101]:1"}, "setup_success": False}}]}
        with mock.patch("app.services.ran_service.nf_api_get", return_value=info), \
             mock.patch("app.services.ran_service.thread_core"):
            g = self.svc()._physical_gnb("192.168.6.0/24", 38412)
        self.assertFalse(g["connected"])
        self.assertEqual(g["ip"], "192.168.6.101")


    def test_prefers_the_association_with_a_completed_setup(self):
        info = {"items": [
            {"gnb_id": 333, "ng": {"sctp": {"peer": "[192.168.6.101]:1"}, "setup_success": False}},
            {"gnb_id": 333, "ng": {"sctp": {"peer": "[192.168.6.101]:2"}, "setup_success": True}},
        ]}
        with mock.patch("app.services.ran_service.nf_api_get", return_value=info), \
             mock.patch("app.services.ran_service.thread_core"):
            g = self.svc()._physical_gnb("192.168.6.0/24", 38412)
        self.assertTrue(g["connected"])

if __name__ == "__main__":
    unittest.main()
