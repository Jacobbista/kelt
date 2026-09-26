"""The NetworkPolicy evaluator, on the policies phase 13 rendered on 2026-09-25.

Each case is a flow docs/architecture/namespaces.md documents as allowed or
blocked; a change in phase 13 that alters one of them should fail here too.
"""
import json
import unittest
from pathlib import Path

from app.services.netpol_eval import evaluate

POLICIES = json.loads((Path(__file__).parent / "fixtures" / "netpol-2026-09-25.json").read_text())["items"]
NAMESPACES = {
    ns: {"kubernetes.io/metadata.name": ns}
    for ns in ["5g", "mec", "camara", "positioning", "iam", "dashboard", "registry",
               "monitoring", "frontdoor", "kube-system"]
}


def pod(namespace, **labels):
    return {"namespace": namespace, "labels": labels, "ip": None}


def service(namespace, port, labels):
    return {"namespace": namespace, "labels": labels, "port": port, "protocol": "TCP", "ip": None}


def address(ip, port=443):
    return {"namespace": None, "labels": {}, "port": port, "protocol": "TCP", "ip": ip}


def verdict(src, dst, policies=POLICIES):
    return evaluate(policies, NAMESPACES, src, dst)["verdict"]


class NetpolEvalTest(unittest.TestCase):
    def test_app_cannot_reach_the_subscriber_database(self):
        self.assertEqual(verdict(pod("mec", app="x"), service("5g", 27017, {"app": "mongodb"})), "blocked")

    def test_app_reaches_the_camara_gateway(self):
        self.assertEqual(verdict(pod("mec", app="x"), service("camara", 8080, {"app": "camara-gateway"})), "passes")

    def test_app_reaches_keycloak_but_not_its_database(self):
        self.assertEqual(verdict(pod("mec", app="x"), service("iam", 8080, {"app": "keycloak"})), "passes")
        self.assertEqual(verdict(pod("mec", app="x"), service("iam", 5432, {"app": "keycloak-db"})), "blocked")

    def test_gateway_reaches_the_positioning_engine(self):
        self.assertEqual(
            verdict(pod("camara", app="camara-gateway"), service("positioning", 8080, {"app": "positioning-engine"})),
            "passes")

    def test_front_door_cannot_reach_the_engine_directly(self):
        self.assertEqual(
            verdict(pod("frontdoor", app="frontdoor"), service("positioning", 8080, {"app": "positioning-engine"})),
            "blocked")

    def test_same_namespace_is_always_accepted(self):
        self.assertEqual(verdict(pod("5g", app="amf"), service("5g", 27017, {"app": "mongodb"})), "passes")

    def test_monitoring_scrapes_every_listed_namespace(self):
        self.assertEqual(verdict(pod("monitoring", app="prometheus"), service("5g", 9090, {"app": "amf"})), "passes")

    def test_management_network_reaches_node_ports(self):
        src = {"namespace": None, "labels": {}, "ip": "192.168.56.13"}
        self.assertEqual(verdict(src, service("iam", 8080, {"app": "keycloak"})), "passes")

    def test_unlisted_namespace_accepts_everything(self):
        self.assertEqual(verdict(pod("mec", app="x"), {**service("kube-system", 53, {"k8s-app": "kube-dns"}), "protocol": "UDP"}), "passes")

    def test_app_reaches_a_public_address(self):
        self.assertEqual(verdict(pod("mec", app="x"), address("1.1.1.1")), "passes")

    def test_app_cannot_reach_a_private_address(self):
        self.assertEqual(verdict(pod("mec", app="x"), address("192.168.56.10", 22)), "blocked")

    def test_without_policies_everything_passes(self):
        self.assertEqual(verdict(pod("mec"), service("5g", 27017, {"app": "mongodb"}), policies=[]), "passes")

    def test_an_ingress_block_names_the_namespace_default_deny(self):
        result = evaluate(POLICIES, NAMESPACES, pod("frontdoor", app="frontdoor"), service("5g", 27017, {"app": "mongodb"}))
        blocked = [s for s in result["steps"] if s["result"] == "no"]
        self.assertEqual([(s["side"], s["policy"]) for s in blocked], [("ingress", "5g/default-deny-ingress")])

    def test_an_egress_block_names_the_egress_policy_and_skips_ingress(self):
        result = evaluate(POLICIES, NAMESPACES, pod("mec", app="x"), service("5g", 27017, {"app": "mongodb"}))
        self.assertEqual([(s["side"], s["result"]) for s in result["steps"]], [("egress", "no"), ("ingress", "na")])
        self.assertEqual(result["steps"][0]["policy"], "mec/allow-declared-egress")


def policy(ns, name, spec):
    return {"metadata": {"namespace": ns, "name": name}, "spec": spec}


class NetpolFeaturesTest(unittest.TestCase):
    """Policy features phase 13 does not use today; each must not answer wrongly."""

    NS = {"a": {"kubernetes.io/metadata.name": "a"}, "b": {"kubernetes.io/metadata.name": "b"}}

    def test_ports_are_matched_on_the_pod_port(self):
        # A Service 80 -> 8080: policies see the pod's port, 8080.
        pol = [policy("b", "allow-8080", {"podSelector": {}, "policyTypes": ["Ingress"],
               "ingress": [{"from": [{"namespaceSelector": {}}], "ports": [{"port": 8080, "protocol": "TCP"}]}]})]
        dst = {**service("b", 8080, {"app": "web"})}
        self.assertEqual(evaluate(pol, self.NS, pod("a"), dst)["verdict"], "passes")

    def test_end_port_ranges(self):
        pol = [policy("b", "range", {"podSelector": {}, "policyTypes": ["Ingress"],
               "ingress": [{"ports": [{"port": 8000, "endPort": 8100, "protocol": "TCP"}]}]})]
        self.assertEqual(evaluate(pol, self.NS, pod("a"), service("b", 8080, {}))["verdict"], "passes")
        self.assertEqual(evaluate(pol, self.NS, pod("a"), service("b", 9000, {}))["verdict"], "blocked")

    def test_match_expressions_select_only_matching_pods(self):
        pol = [policy("b", "deny-db", {"policyTypes": ["Ingress"], "ingress": [],
               "podSelector": {"matchExpressions": [{"key": "app", "operator": "In", "values": ["db"]}]}})]
        self.assertEqual(evaluate(pol, self.NS, pod("a"), service("b", 5432, {"app": "db"}))["verdict"], "blocked")
        self.assertEqual(evaluate(pol, self.NS, pod("a"), service("b", 80, {"app": "web"}))["verdict"], "passes")

    def test_match_expression_operators(self):
        def sel(op, values=None):
            e = {"key": "tier", "operator": op}
            if values is not None:
                e["values"] = values
            return [policy("b", "p", {"policyTypes": ["Ingress"], "ingress": [], "podSelector": {"matchExpressions": [e]}})]
        with_tier = service("b", 80, {"tier": "x"})
        without = service("b", 80, {})
        self.assertEqual(evaluate(sel("NotIn", ["x"]), self.NS, pod("a"), with_tier)["verdict"], "passes")
        self.assertEqual(evaluate(sel("Exists"), self.NS, pod("a"), with_tier)["verdict"], "blocked")
        self.assertEqual(evaluate(sel("DoesNotExist"), self.NS, pod("a"), without)["verdict"], "blocked")

    def test_missing_policy_types_include_egress_when_there_are_egress_rules(self):
        pol = [policy("a", "egress-only-dns", {"podSelector": {}, "egress": [{"ports": [{"port": 53, "protocol": "UDP"}]}]})]
        self.assertEqual(evaluate(pol, self.NS, pod("a"), service("b", 80, {}))["verdict"], "blocked")


if __name__ == "__main__":
    unittest.main()
