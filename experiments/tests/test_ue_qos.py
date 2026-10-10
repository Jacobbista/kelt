import json
import os
import tempfile
import unittest

from network import ue_qos

# Shapes of SMF /pdu-info and AMF /ue-info (Open5GS 2.7.7) and of a subscriber
# record's AMBR fields, as read on 2026-10-10.
PDU_INFO = {"items": [
    {"supi": "imsi-001010000000002", "pdu": [{"psi": 2, "dnn": "internet", "ipv4": "10.45.0.2",
     "snssai": {"sst": 1, "sd": "000001"}, "qos_flows": [{"qfi": 1, "5qi": 9}], "pdu_state": "inactive"}]},
    {"supi": "imsi-001010000000001", "pdu": [{"psi": 1, "dnn": "internet", "ipv4": "10.45.0.4",
     "snssai": {"sst": 1, "sd": "000001"}, "qos_flows": [{"qfi": 1, "5qi": 9}], "pdu_state": "active"}]},
], "pager": {"page": 0, "page_size": 100, "count": 2}}
UE_INFO = {"items": [
    {"supi": "imsi-001010000000001", "cm_state": "connected", "ambr": {"downlink": 500000000, "uplink": 500000000}},
    {"supi": "imsi-001010000000002", "cm_state": "idle", "ambr": {"downlink": 2000000000, "uplink": 2000000000}},
], "pager": {"page": 0, "page_size": 100, "count": 2}}
SUBSCRIBER = {"imsi": "001010000000001",
              "ambr": {"downlink": {"unit": 2, "value": 500}, "uplink": {"unit": 2, "value": 500}},
              "slice": [{"sst": 1, "sd": "000001", "session": [
                  {"name": "internet", "ambr": {"downlink": {"unit": 2, "value": 500}, "uplink": {"unit": 1, "value": 100000}},
                   "qos": {"index": 9}}]}]}


class UeQosTest(unittest.TestCase):
    def test_session_found_by_address(self):
        q = ue_qos.ue_qos("10.45.0.4/29", PDU_INFO, UE_INFO, SUBSCRIBER)
        self.assertEqual(q["supi"], "imsi-001010000000001")
        self.assertEqual(q["dnn"], "internet")
        self.assertEqual(q["five_qi"], 9)
        self.assertEqual(q["pdu_state"], "active")

    def test_ue_ambr_from_the_amf_in_mbit(self):
        q = ue_qos.ue_qos("10.45.0.4", PDU_INFO, UE_INFO, SUBSCRIBER)
        self.assertEqual((q["ue_ambr_dl_mbit"], q["ue_ambr_ul_mbit"]), (500, 500))

    def test_session_ambr_from_the_record_with_its_unit(self):
        q = ue_qos.ue_qos("10.45.0.4", PDU_INFO, UE_INFO, SUBSCRIBER)
        self.assertEqual((q["session_ambr_dl_mbit"], q["session_ambr_ul_mbit"]), (500, 100))
        self.assertEqual(q["session_ambr_source"], "subscriber_record")

    def test_unknown_address(self):
        q = ue_qos.ue_qos("10.45.0.9", PDU_INFO, UE_INFO, SUBSCRIBER)
        self.assertEqual(q["supi"], "unknown")
        self.assertEqual(q["ue_ambr_dl_mbit"], "unknown")
        self.assertEqual(q["session_ambr_dl_mbit"], "unknown")

    def test_inactive_session_is_reported_as_such(self):
        q = ue_qos.ue_qos("10.45.0.2", PDU_INFO, UE_INFO, {})
        self.assertEqual(q["pdu_state"], "inactive")
        self.assertEqual(q["ue_ambr_dl_mbit"], 2000)

    def test_supi_without_amf_context(self):
        q = ue_qos.ue_qos("10.45.0.4", PDU_INFO, {"items": []}, SUBSCRIBER)
        self.assertEqual(q["ue_ambr_dl_mbit"], "unknown")
        self.assertEqual(q["session_ambr_dl_mbit"], 500)

    def test_record_without_the_session_dnn(self):
        other = json.loads(json.dumps(SUBSCRIBER))
        other["slice"][0]["session"][0]["name"] = "mec"
        q = ue_qos.ue_qos("10.45.0.4", PDU_INFO, UE_INFO, other)
        self.assertEqual(q["session_ambr_dl_mbit"], "unknown")

    def test_record_of_another_slice(self):
        other = json.loads(json.dumps(SUBSCRIBER))
        other["slice"][0]["sd"] = "000002"
        q = ue_qos.ue_qos("10.45.0.4", PDU_INFO, UE_INFO, other)
        self.assertEqual(q["session_ambr_dl_mbit"], "unknown")

    def test_empty_responses(self):
        q = ue_qos.ue_qos("10.45.0.4", {}, {}, {})
        self.assertEqual(q["supi"], "unknown")


class MergeTest(unittest.TestCase):
    def test_merge_into_ue_json_under_a_key(self):
        d = tempfile.mkdtemp()
        paths = {}
        for name, doc in (("pdu", PDU_INFO), ("ue", UE_INFO), ("sub", SUBSCRIBER)):
            paths[name] = os.path.join(d, name + ".json")
            with open(paths[name], "w") as fh:
                json.dump(doc, fh)
        target = os.path.join(d, "ue.json")
        with open(target, "w") as fh:
            json.dump({"mode": "direct"}, fh)
        ue_qos.main([target, "attempt_1_qos_start", "10.45.0.4/29", paths["pdu"], paths["ue"], paths["sub"]])
        with open(target) as fh:
            doc = json.load(fh)
        self.assertEqual(doc["mode"], "direct")
        self.assertEqual(doc["attempt_1_qos_start"]["session_ambr_dl_mbit"], 500)

    def test_unreadable_input_counts_as_empty(self):
        d = tempfile.mkdtemp()
        target = os.path.join(d, "ue.json")
        bad = os.path.join(d, "bad.json")
        with open(bad, "w") as fh:
            fh.write("")
        ue_qos.main([target, "attempt_1_qos_end", "10.45.0.4", bad, bad, bad])
        with open(target) as fh:
            self.assertEqual(json.load(fh)["attempt_1_qos_end"]["supi"], "unknown")


class SupiTest(unittest.TestCase):
    def test_supi_of_an_address(self):
        self.assertEqual(ue_qos.supi_of("10.45.0.4/29", PDU_INFO), "001010000000001")

    def test_no_session_no_supi(self):
        self.assertEqual(ue_qos.supi_of("10.45.0.9", PDU_INFO), "")

    def test_only_digits_are_returned(self):
        odd = {"items": [{"supi": "imsi-00101'; db.x.drop(); '", "pdu": [{"ipv4": "10.45.0.7"}]}]}
        self.assertEqual(ue_qos.supi_of("10.45.0.7", odd), "")


if __name__ == "__main__":
    unittest.main()
