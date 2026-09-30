import unittest

from app.services.ran_chain import build_chain

SERVING = {
    "nic_state": "up", "iface": "enp0s9", "host_nic": "enp88s0",
    "nic_on_bridge": True, "bridge_ports": ["enp0s9", "veth1a2b"],
    "amf_attached": True, "amf_ip": "192.168.6.150", "ngap_port": 38412,
    "gnb_connected": True, "gnb_ip": "192.168.6.101",
    "upf_route": True, "subnet": "192.168.6.0/24", "n3_subnet": "10.203.0.0/24",
    "ues": 2, "pdu": 3,
}


def chain(**over):
    return build_chain({**SERVING, **over})


def states(c):
    return [l["state"] for l in c["links"]]


class ChainTest(unittest.TestCase):
    def test_serving(self):
        c = chain()
        self.assertEqual(c["state"], "serving")
        self.assertIsNone(c["first_broken"])
        self.assertEqual([l["id"] for l in c["links"]], ["cable", "bridge", "core", "user_plane", "ues"])
        self.assertEqual(states(c), ["ok"] * 5)
        self.assertEqual(c["links"][4]["verdict"], "2 registered, 3 PDU sessions")

    def test_zero_ues_is_idle_not_broken(self):
        c = chain(ues=0, pdu=0)
        self.assertEqual(c["state"], "serving")
        self.assertEqual(c["links"][4]["state"], "idle")
        self.assertEqual(c["links"][4]["verdict"], "No UE registered yet")

    def test_nic_missing_gives_cli_with_the_host_adapter(self):
        c = chain(nic_state="missing", nic_on_bridge=False)
        self.assertEqual(c["first_broken"], "cable")
        self.assertEqual(c["links"][0]["fix"], {"cli": "kelt ran enp88s0"})
        self.assertEqual(states(c)[1:], ["blocked"] * 4)

    def test_nic_missing_without_host_adapter_keeps_a_placeholder(self):
        c = chain(nic_state="missing", host_nic=None)
        self.assertEqual(c["links"][0]["fix"], {"cli": "kelt ran <adapter>"})

    def test_link_down_is_fixed_by_ran_link(self):
        c = chain(nic_state="down", gnb_connected=False, ues=0, pdu=0)
        l = c["links"][0]
        self.assertEqual((l["state"], l["verdict"], l["fix"]), ("bad", "The link is down", {"piece": "ran_link"}))
        self.assertEqual(c["links"][1]["verdict"], "Not checked until cable and link works")

    def test_no_carrier_has_no_software_fix(self):
        l = chain(nic_state="no_carrier")["links"][0]
        self.assertEqual(l["verdict"], "No carrier on the wire")
        self.assertIn("checklist", l["fix"])

    def test_nic_not_on_bridge(self):
        c = chain(nic_on_bridge=False)
        self.assertEqual(c["first_broken"], "bridge")
        # ran_link finds the NIC through br-ran's ports, so it cannot help here;
        # restarting the worker runs the whole setup again.
        self.assertEqual(c["links"][1]["fix"], {"cli": "kelt ran enp88s0"})

    def test_core_not_attached(self):
        c = chain(amf_attached=False, gnb_connected=False, upf_route=False, ues=0, pdu=0)
        self.assertEqual(c["first_broken"], "core")
        self.assertEqual(c["links"][2]["verdict"], "The core is not attached to the RAN")
        self.assertEqual(c["links"][2]["fix"], {"piece": "ran_attach"})

    def test_no_ng_setup_points_to_setup(self):
        l = chain(gnb_connected=False, gnb_ip=None, ues=0, pdu=0)["links"][2]
        self.assertEqual((l["verdict"], l["fix"]), ("No NG Setup from the gNB", {"see": "setup"}))
        self.assertEqual(l["value"], "192.168.6.150:38412")

    def test_no_upf_route(self):
        c = chain(upf_route=False)
        self.assertEqual(c["first_broken"], "user_plane")
        self.assertEqual(c["links"][3]["fix"], {"piece": "ran_attach"})
        self.assertEqual(c["links"][4]["state"], "blocked")

    def test_values_come_from_facts(self):
        c = chain(amf_ip="10.9.9.9", gnb_ip="10.9.9.8", iface="eth7")
        self.assertEqual(c["links"][0]["value"], "eth7")
        self.assertEqual(c["links"][2]["value"], "10.9.9.9 ↔ 10.9.9.8")

    def test_unknown_ngap_port_shows_the_address_alone(self):
        c = chain(ngap_port=None, gnb_connected=False, gnb_ip=None, ues=0, pdu=0)
        self.assertEqual(c["links"][2]["value"], "192.168.6.150")
        self.assertNotIn("None", str(c["links"][2]["facts"]))

if __name__ == "__main__":
    unittest.main()
