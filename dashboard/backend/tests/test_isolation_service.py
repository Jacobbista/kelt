import asyncio
import json
import unittest
from pathlib import Path

from app.services.isolation_service import IsolationService, parse_rule, plane_name

POLICIES = json.loads((Path(__file__).parent / "fixtures" / "netpol-2026-09-25.json").read_text())["items"]
ROLES = {"5g": "5G core NFs", "mec": "Deployed applications", "iam": "Keycloak and its database"}


def series(rule, verdict, value):
    return {"metric": {"rule": rule, "verdict": verdict}, "value": [0, str(value)]}


class FakePrometheus:
    # `current`: the series the exporter publishes now (default: the same rules).
    def __init__(self, result=None, fail=None, current=None):
        self.result, self.fail, self.queries = result or [], fail, []
        self.current = self.result if current is None else current

    async def instant_query(self, query):
        self.queries.append(query)
        if self.fail:
            raise self.fail
        if "enforced" in query:
            return {"result": [{"metric": {}, "value": [0, "1"]}]}
        if "increase(" in query:
            return {"result": self.result}
        return {"result": self.current}


def run(coro):
    return asyncio.run(coro)


class PlaneRuleTest(unittest.TestCase):
    def test_pair(self):
        self.assertEqual(parse_rule("br-n3 -> br-n4"), {"kind": "pair", "from": "br-n3", "to": "br-n4"})

    def test_egress(self):
        self.assertEqual(parse_rule("br-n6c -> enp0s3"), {"kind": "egress", "from": "br-n6c", "to": "enp0s3"})

    def test_replies(self):
        self.assertEqual(parse_rule("enp0s3 -> br-n6c replies")["kind"], "replies")

    def test_from_a_plane_to_anything_else(self):
        self.assertEqual(parse_rule("from br-n3"), {"kind": "outside", "from": "br-n3", "to": None})

    def test_named_rules(self):
        self.assertEqual(parse_rule("into a plane")["kind"], "outside")
        self.assertEqual(parse_rule("to UE pools outside the planes")["kind"], "outside")

    def test_plane_names(self):
        self.assertEqual(plane_name("br-ran"), "RAN")
        self.assertEqual(plane_name("br-n6c"), "N6c")
        self.assertEqual(plane_name("br-n2-cell-1"), "N2 cell 1")


class PlanesTest(unittest.TestCase):
    RESULT = [
        series("br-ran -> br-n3", "allowed", 1090257),
        series("br-n3 -> br-ran", "allowed", 242839),
        series("br-n6c -> enp0s3", "allowed", 49933),
        series("enp0s3 -> br-n6c replies", "allowed", 28952),
        series("br-n3 -> br-n4", "not_allowed", 3),
        series("br-n4 -> br-n3", "not_allowed", 0),
        series("into a plane", "not_allowed", 25),
        series("from br-n3", "not_allowed", 0),
        # Per-pair not-allowed rules exist only for bridges present on the worker.
        series("br-ran -> br-n4", "not_allowed", 0),
        series("from br-n6c", "not_allowed", 0),
    ]

    def test_counts_by_pair_and_outside(self):
        out = run(IsolationService(prometheus=FakePrometheus(self.RESULT)).planes("24h"))
        self.assertTrue(out["available"])
        self.assertEqual(out["mode"], "enforce")
        self.assertEqual(out["planes"], ["RAN", "N3", "N4", "N6c"])
        pairs = {(p["from"], p["to"]): (p["verdict"], p["packets"]) for p in out["pairs"]}
        self.assertEqual(pairs[("RAN", "N3")], ("allowed", 1090257))
        self.assertEqual(pairs[("N3", "N4")], ("not_allowed", 3))
        self.assertEqual(pairs[("N6c", "Internet")], ("allowed", 49933))
        self.assertEqual([(o["rule"], o["packets"]) for o in out["outside"]], [("into a plane", 25), ("from br-n3", 0), ("from br-n6c", 0)])
        self.assertEqual(out["blocked_total"], 28)

    def test_a_removed_plane_leaves_the_matrix_at_once(self):
        # br-n2-cell-1 was removed: its counters still have an increase over the
        # window, but the exporter no longer publishes its rules.
        result = self.RESULT + [series("br-n2-cell-1 -> br-n4", "not_allowed", 3),
                                series("from br-n2-cell-1", "not_allowed", 1)]
        out = run(IsolationService(prometheus=FakePrometheus(result, current=self.RESULT)).planes("24h"))
        self.assertNotIn("N2 cell 1", out["planes"])
        self.assertFalse(any("br-n2-cell-1" in x["rule"] for x in out["pairs"]))

    def test_a_missing_bridge_has_no_row_even_with_its_allowed_rules(self):
        # Physical RAN off: br-ran does not exist, but the allowed br-ran rules are
        # still exported (at zero) and must not bring a RAN row into the matrix.
        result = [
            series("br-ran -> br-n3", "allowed", 0),
            series("br-n3 -> br-ran", "allowed", 0),
            series("br-n3 -> br-n4", "not_allowed", 0),
            series("br-n4 -> br-n3", "not_allowed", 0),
        ]
        out = run(IsolationService(prometheus=FakePrometheus(result)).planes("24h"))
        self.assertEqual(out["planes"], ["N3", "N4"])
        self.assertNotIn("RAN", {p["from"] for p in out["pairs"]} | {p["to"] for p in out["pairs"]})

    def test_no_counters_in_prometheus_is_unavailable_not_all_clear(self):
        out = run(IsolationService(prometheus=FakePrometheus([])).planes("24h"))
        self.assertFalse(out["available"])
        self.assertIn("no plane filter counters", out["reason"])

    def test_window_is_validated(self):
        with self.assertRaises(ValueError):
            run(IsolationService(prometheus=FakePrometheus()).planes("1h) or vector(1"))
        with self.assertRaises(ValueError):
            run(IsolationService(prometheus=FakePrometheus()).planes("30d"))

    def test_prometheus_down_is_reported_not_raised(self):
        out = run(IsolationService(prometheus=FakePrometheus(fail=OSError("connection refused"))).planes("24h"))
        self.assertFalse(out["available"])
        self.assertIn("connection refused", out["reason"])
        self.assertEqual(out["pairs"], [])


class SamplesTest(unittest.TestCase):
    # As journalctl prints it: oldest first.
    LOG = "\n".join([
        "2026-09-24T10:16:02+0000 worker kernel: KELT-PLANES enforce: IN=cni0 OUT=br-ran SRC=10.203.0.101 "
        "DST=192.168.6.101 LEN=84 PROTO=UDP SPT=2152 DPT=2152 LEN=64",
        "2026-09-24T11:06:19+0000 worker kernel: KELT-PLANES enforce into_a_plane: IN=cni0 OUT=br-ran "
        "PHYSIN=veth7b SRC=10.203.0.101 DST=192.168.6.101 LEN=118 PROTO=ICMP TYPE=3 CODE=3 "
        "[SRC=192.168.6.101 DST=10.203.0.101 LEN=90 PROTO=UDP SPT=2152 DPT=2152 LEN=70 ]",
        "2026-09-25T09:59:31+0000 worker kernel: KELT-PLANES enforce br-n3_->_br-n4: IN=br-n3 OUT=br-n4 "
        "MAC=5e:05 SRC=10.203.0.103 DST=10.204.0.100 LEN=84 TTL=63 PROTO=ICMP TYPE=8 CODE=0 ID=1 SEQ=1",
    ])

    def fake(self, rc=0, out=None, err=""):
        return lambda cmd: (rc, self.LOG if out is None else out, err)

    def test_parses_newest_first_with_the_rule(self):
        out = IsolationService(run=self.fake()).samples(limit=5)
        self.assertTrue(out["available"])
        first = out["samples"][0]
        self.assertEqual((first["rule"], first["in"], first["out"], first["src"], first["dst"], first["proto"]),
                         ("br-n3 -> br-n4", "br-n3", "br-n4", "10.203.0.103", "10.204.0.100", "ICMP"))
        second = out["samples"][1]
        # The quoted original packet of an ICMP error must not overwrite the outer fields.
        self.assertEqual((second["rule"], second["src"], second["proto"]), ("into a plane", "10.203.0.101", "ICMP"))
        # Samples from before per-rule logging carry no rule.
        self.assertEqual((out["samples"][2]["rule"], out["samples"][2]["sport"]), (None, 2152))

    def test_the_read_is_capped(self):
        seen = []

        def run(cmd):
            seen.append(cmd[-1])
            return 0, self.LOG, ""

        IsolationService(run=run).samples(limit=5)
        self.assertIn(" -n 2000 ", seen[0])

    def test_limit_is_per_rule(self):
        line = self.LOG.splitlines()[-1]
        out = IsolationService(run=self.fake(out="\n".join([line] * 9))).samples(limit=3)
        self.assertEqual(len(out["samples"]), 3)

    def test_ssh_failure_is_reported(self):
        out = IsolationService(run=self.fake(rc=255, out="", err="ssh: connect to host worker: No route")).samples()
        self.assertFalse(out["available"])
        self.assertIn("No route", out["reason"])

    def test_exit_1_with_an_error_is_a_failure_not_an_empty_journal(self):
        out = IsolationService(run=self.fake(rc=1, out="", err="sudo: a password is required")).samples()
        self.assertFalse(out["available"])
        self.assertIn("password", out["reason"])

    def test_empty_journal_is_available_and_empty(self):
        out = IsolationService(run=self.fake(rc=1, out="")).samples()
        self.assertEqual((out["available"], out["samples"]), (True, []))


class PoliciesTest(unittest.TestCase):
    def test_summarises_each_listed_namespace(self):
        out = IsolationService(list_policies=lambda: POLICIES, namespace_roles=ROLES).policies()
        self.assertTrue(out["enabled"])
        by_ns = {n["name"]: n for n in out["namespaces"]}
        self.assertEqual(by_ns["mec"]["role"], "Deployed applications")
        self.assertTrue(by_ns["mec"]["egress"]["limited"])
        self.assertFalse(by_ns["5g"]["egress"]["limited"])
        self.assertEqual(by_ns["5g"]["allow"], [])
        self.assertIn({"from": "mec", "to": "keycloak", "policy": "allow-keycloak-ingress"}, by_ns["iam"]["allow"])
        self.assertIn({"from": "frontdoor", "to": "any pod", "policy": "allow-all-ingress"}, by_ns["mec"]["allow"])
        self.assertIn("default-deny-ingress", by_ns["iam"]["policies"])

    def test_no_managed_policies_means_disabled(self):
        out = IsolationService(list_policies=lambda: [], namespace_roles=ROLES).policies()
        self.assertEqual((out["enabled"], out["namespaces"]), (False, []))


class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeCore:
    """The CoreV1Api calls check() makes, for one Service web:80 -> pod 8080 in b."""

    def list_namespace(self):
        return _Obj(items=[_Obj(metadata=_Obj(name=n, labels={"kubernetes.io/metadata.name": n})) for n in ("a", "b")])

    def read_namespaced_service(self, name, namespace):
        port = _Obj(port=80, target_port=8080, protocol="TCP", name="http")
        return _Obj(spec=_Obj(selector={"app": "web"}, ports=[port]))


class CheckTest(unittest.TestCase):
    POLICY = [{"metadata": {"namespace": "b", "name": "allow-8080"},
               "spec": {"podSelector": {}, "policyTypes": ["Ingress"],
                        "ingress": [{"ports": [{"port": 8080, "protocol": "TCP"}]}]}}]

    def test_service_port_is_translated_to_the_pod_port(self):
        svc = IsolationService(list_policies=lambda: self.POLICY, k8s=_Obj(core=FakeCore()))
        out = svc.check("a", {"namespace": "b", "service": "web", "port": 80})
        self.assertEqual(out["verdict"], "passes")


if __name__ == "__main__":
    unittest.main()
