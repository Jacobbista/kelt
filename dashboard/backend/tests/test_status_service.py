import inspect
import unittest
from types import SimpleNamespace as NS

from app.services.status_service import StatusService


def svc(nodes=(), pods=(), checks=(), alert=None, fail=None):
    fail = fail or {}

    def src(value, key):
        def f():
            if key in fail:
                raise fail[key]
            return value if isinstance(value, dict) else list(value)
        return f
    return StatusService(nodes=src(nodes, "nodes"), pods=src(pods, "pods"),
                         checks=src(checks, "checks"), amf_alert=src(alert or {"active": False}, "alert"))


READY = [NS(name="master", status="Ready"), NS(name="worker", status="Ready")]
RUNNING = [NS(name="amf-1", phase="Running", deployment="amf", ready=True, waiting_reason=None)]


class StatusTest(unittest.TestCase):
    def test_all_good(self):
        self.assertEqual(svc(READY, RUNNING, [{"interface": "N2", "status": "ok"}]).summary(),
                         {"state": "ok", "problems": []})

    def test_node_not_ready_is_an_error(self):
        out = svc([NS(name="worker", status="NotReady")], RUNNING).summary()
        self.assertEqual(out["state"], "error")
        self.assertEqual(out["problems"], [{"area": "Nodes", "text": "worker is not Ready", "severity": "error"}])

    def test_core_pods(self):
        pods = RUNNING + [NS(name="smf-1", phase="Failed", deployment="smf", ready=False, waiting_reason=None),
                          NS(name="upf-1", phase="Pending", deployment="upf", ready=False, waiting_reason=None)]
        out = svc(READY, pods).summary()
        self.assertEqual(out["state"], "error")
        self.assertIn({"area": "5G core", "text": "smf-1 is Failed", "severity": "error"}, out["problems"])
        self.assertIn({"area": "5G core", "text": "upf-1 is Pending", "severity": "warn"}, out["problems"])

    def test_a_crashlooping_pod_is_an_error(self):
        # A crashlooping container keeps the pod phase Running.
        pods = [NS(name="amf-1", phase="Running", deployment="amf", ready=False, waiting_reason="CrashLoopBackOff")]
        out = svc(READY, pods).summary()
        self.assertEqual(out["problems"], [{"area": "5G core", "text": "amf-1 is CrashLoopBackOff", "severity": "error"}])

    def test_an_image_that_cannot_be_pulled_is_an_error(self):
        pods = [NS(name="smf-1", phase="Pending", deployment="smf", ready=False, waiting_reason="ImagePullBackOff")]
        out = svc(READY, pods).summary()
        self.assertEqual(out["problems"], [{"area": "5G core", "text": "smf-1 is ImagePullBackOff", "severity": "error"}])

    def test_running_but_not_ready_is_a_warning(self):
        pods = [NS(name="upf-1", phase="Running", deployment="upf", ready=False, waiting_reason=None)]
        out = svc(READY, pods).summary()
        self.assertEqual(out["problems"], [{"area": "5G core", "text": "upf-1 is not ready", "severity": "warn"}])

    def test_network_checks_from_the_last_run(self):
        checks = [{"interface": "N3", "status": "fail", "detail": "no reply"},
                  {"interface": "N4", "status": "warn", "detail": "slow"},
                  {"interface": "N6", "status": "unknown"}]
        out = svc(READY, RUNNING, checks).summary()
        self.assertIn({"area": "Network", "text": "N3 check failed: no reply", "severity": "error"}, out["problems"])
        self.assertIn({"area": "Network", "text": "N4 check: slow", "severity": "warn"}, out["problems"])
        self.assertEqual(len(out["problems"]), 2)

    def test_network_problems_say_how_old_they_are(self):
        checks = [{"interface": "N2", "status": "fail", "detail": "no gNB", "age_s": 420}]
        out = svc(READY, RUNNING, checks).summary()
        self.assertEqual(out["problems"][0]["text"], "N2 check failed 7 min ago: no gNB")

    def test_a_stuck_amf_pod_is_counted_once(self):
        pods = [NS(name="amf-1", phase="Pending", deployment="amf", ready=False, waiting_reason=None)]
        out = svc(READY, pods, alert={"active": True, "reasons": ["stuck_amf_pods"]}).summary()
        self.assertEqual(out["problems"], [{"area": "5G core", "text": "amf-1 is Pending", "severity": "warn"}])

    def test_amf_alert_is_a_warning(self):
        out = svc(READY, RUNNING, alert={"active": True, "reasons": ["failed_sandbox_file_exists"]}).summary()
        self.assertEqual(out, {"state": "warn", "problems": [
            {"area": "5G core", "text": "AMF networking alert: failed_sandbox_file_exists", "severity": "warn"}]})

    def test_a_source_that_fails_is_a_warning(self):
        out = svc(READY, RUNNING, fail={"nodes": RuntimeError("api down")}).summary()
        self.assertEqual(out["state"], "warn")
        self.assertEqual(out["problems"], [{"area": "Nodes", "text": "Cannot read: api down", "severity": "warn"}])

    def test_never_runs_the_network_checks(self):
        # The checks exec into pods: the summary reads the last run only.
        from app.routers import status as router
        source = inspect.getsource(router)
        self.assertNotIn("run_health_checks", source)
        self.assertNotIn(".latest(", source)
        self.assertIn("get_cached", source)


class UnreachableClusterTest(unittest.TestCase):
    def test_an_unreachable_cluster_api_is_an_error(self):
        from app.routers import status as router

        def no_cluster():
            raise RuntimeError("connection refused")

        out = router.summarize(no_cluster)
        self.assertEqual(out, {"state": "error", "problems": [
            {"area": "Cluster", "text": "Kubernetes API unreachable: connection refused", "severity": "error"}]})


class StatusRouteTest(unittest.TestCase):
    def test_the_route_exists_and_needs_a_role(self):
        from app.main import app
        from app.auth import require_admin, require_viewer_or_admin
        from app.route_guard import PUBLIC_ROUTES, unguarded_routes
        self.assertIn("/api/v1/status/summary", {r.path for r in app.routes})
        self.assertEqual(unguarded_routes(app, {require_admin, require_viewer_or_admin}, PUBLIC_ROUTES), [])


if __name__ == "__main__":
    unittest.main()
